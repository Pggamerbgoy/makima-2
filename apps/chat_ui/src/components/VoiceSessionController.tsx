import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, Pause, Play, Square, Volume2, ShieldCheck } from 'lucide-react';
import { wsClient } from '../services/wsClient';

export type VoiceSessionState = 'idle' | 'arming' | 'listening' | 'recording' | 'processing' | 'speaking' | 'paused' | 'error';

interface VoiceSettings {
  wakeWordEnabled: boolean;
  followupTimeoutSeconds: number;
  silenceTimeoutMs: number;
  maxUtteranceSeconds: number;
  voiceLanguage: 'auto' | 'en' | 'hi';
  autoReadAloud: boolean;
}

interface Props {
  conversationId: string;
  connected: boolean;
  settings: VoiceSettings;
  onTurnStarted: (taskId: string, text: string) => void;
  onSessionChange?: (sessionId: string | null, state: VoiceSessionState) => void;
}

const TARGET_SAMPLE_RATE = 16000;
const SPEECH_RMS = 0.018;
const MIN_UTTERANCE_MS = 320;

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(binary);
}

function downsampleToPcm(chunks: Float32Array[], sourceRate: number): Uint8Array {
  const inputLength = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const input = new Float32Array(inputLength);
  let offset = 0;
  chunks.forEach((chunk) => { input.set(chunk, offset); offset += chunk.length; });
  const ratio = sourceRate / TARGET_SAMPLE_RATE;
  const output = new Uint8Array(Math.floor(input.length / ratio) * 2);
  const view = new DataView(output.buffer);
  for (let i = 0; i < output.length / 2; i += 1) {
    const index = Math.min(input.length - 1, Math.floor(i * ratio));
    const sample = Math.max(-1, Math.min(1, input[index] || 0));
    view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return output;
}

function absoluteAudioUrl(url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  const configured = new URL(wsClient.getUrl());
  return `${configured.protocol === 'wss:' ? 'https:' : 'http:'}//${configured.host}${url}`;
}

export function VoiceSessionController({ conversationId, connected, settings, onTurnStarted, onSessionChange }: Props) {
  const [state, setState] = useState<VoiceSessionState>('idle');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState('');
  const [level, setLevel] = useState(0);
  const [requiresWakeWord, setRequiresWakeWord] = useState(true);
  const [error, setError] = useState('');
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const frameRef = useRef<number | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const recordingSinceRef = useRef(0);
  const lastSpeechRef = useRef(0);
  const stateRef = useRef<VoiceSessionState>('idle');
  const sessionRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  const updateState = useCallback((next: VoiceSessionState) => {
    stateRef.current = next;
    setState(next);
    onSessionChange?.(sessionRef.current, next);
  }, [onSessionChange]);

  const stopAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) { audio.pause(); audio.src = ''; audioRef.current = null; }
    activeSourcesRef.current.forEach(src => {
      try { src.stop(); src.disconnect(); } catch {}
    });
    activeSourcesRef.current = [];
    nextPlayTimeRef.current = 0;
  }, []);

  const playPcm24k = useCallback((base64Audio: string) => {
    try {
      if (!playbackCtxRef.current || playbackCtxRef.current.state === 'closed') {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        playbackCtxRef.current = new AudioCtx({ sampleRate: 24000 });
      }
      const ctx = playbackCtxRef.current;
      if (ctx.state === 'suspended') {
        void ctx.resume();
      }
      const binary = atob(base64Audio);
      const len = binary.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
      const int16 = new Int16Array(bytes.buffer);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;

      const audioBuffer = ctx.createBuffer(1, float32.length, 24000);
      audioBuffer.getChannelData(0).set(float32);

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);

      const now = ctx.currentTime;
      const startAt = Math.max(now, nextPlayTimeRef.current);
      source.start(startAt);
      nextPlayTimeRef.current = startAt + audioBuffer.duration;

      activeSourcesRef.current.push(source);
      source.onended = () => {
        activeSourcesRef.current = activeSourcesRef.current.filter(s => s !== source);
        if (activeSourcesRef.current.length === 0 && sessionRef.current) {
          updateState('listening');
        }
      };
      updateState('speaking');
    } catch (e) {
      console.warn('Failed to decode/play PCM 24k audio:', e);
    }
  }, [updateState]);

  const stopCapture = useCallback(() => {
    if (frameRef.current) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    processorRef.current?.disconnect(); processorRef.current = null;
    analyserRef.current?.disconnect(); analyserRef.current = null;
    contextRef.current?.close().catch(() => undefined); contextRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop()); streamRef.current = null;
    chunksRef.current = [];
  }, []);

  const finishUtterance = useCallback(() => {
    const activeSession = sessionRef.current;
    if (!activeSession || !chunksRef.current.length || stateRef.current !== 'recording') return;
    const elapsed = performance.now() - recordingSinceRef.current;
    const audioContext = contextRef.current;
    updateState('processing');
    if (elapsed < MIN_UTTERANCE_MS || !audioContext) { updateState('listening'); return; }
    const pcm = downsampleToPcm(chunksRef.current, audioContext.sampleRate);
    chunksRef.current = [];
    wsClient.sendVoiceUtterance(activeSession, toBase64(pcm));
  }, [updateState]);

  const startUtterance = useCallback(() => {
    if (!sessionRef.current || !['listening', 'speaking'].includes(stateRef.current)) return;
    if (stateRef.current === 'speaking') { stopAudio(); wsClient.bargeIn(sessionRef.current); }
    chunksRef.current = [];
    recordingSinceRef.current = performance.now();
    lastSpeechRef.current = recordingSinceRef.current;
    updateState('recording');
  }, [stopAudio, updateState]);

  const startCapture = useCallback(async () => {
    if (!connected) { setError('Makima Brain is disconnected. Reconnect, then start voice.'); updateState('error'); return; }
    try {
      setError(''); updateState('arming');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const context = new AudioCtx();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser(); analyser.fftSize = 1024;
      const processor = context.createScriptProcessor(2048, 1, 1);
      const muteGain = context.createGain();
      muteGain.gain.value = 0;
      source.connect(analyser);
      source.connect(processor);
      processor.connect(muteGain);
      muteGain.connect(context.destination);
      processor.onaudioprocess = (event) => {
        if (stateRef.current !== 'recording') return;
        chunksRef.current.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      streamRef.current = stream; contextRef.current = context; analyserRef.current = analyser; processorRef.current = processor;
      const nextSession = wsClient.startVoiceSession(conversationId, { wake_word_enabled: settings.wakeWordEnabled, followup_timeout_s: settings.followupTimeoutSeconds, max_utterance_seconds: settings.maxUtteranceSeconds, language: settings.voiceLanguage });
      sessionRef.current = nextSession; setSessionId(nextSession); onSessionChange?.(nextSession, 'arming');
      const samples = new Uint8Array(analyser.fftSize);
      const tick = () => {
        if (!analyserRef.current || !sessionRef.current) return;
        analyserRef.current.getByteTimeDomainData(samples);
        let sum = 0; for (const sample of samples) { const value = (sample - 128) / 128; sum += value * value; }
        const rms = Math.sqrt(sum / samples.length); setLevel(Math.min(1, rms * 12));
        const now = performance.now();
        if (rms > SPEECH_RMS) {
          lastSpeechRef.current = now;
          if (stateRef.current === 'listening' || stateRef.current === 'speaking') startUtterance();
        }
        if (stateRef.current === 'recording' && (now - lastSpeechRef.current > settings.silenceTimeoutMs || now - recordingSinceRef.current > settings.maxUtteranceSeconds * 1000)) finishUtterance();
        frameRef.current = requestAnimationFrame(tick);
      };
      frameRef.current = requestAnimationFrame(tick);
    } catch (captureError) {
      stopCapture();
      setError(captureError instanceof DOMException && captureError.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow it in system settings and try again.' : 'Your microphone or audio capture is not supported.');
      updateState('error');
    }
  }, [connected, conversationId, finishUtterance, onSessionChange, settings, startUtterance, stopCapture, updateState]);

  const stopSession = useCallback(() => {
    if (sessionRef.current) wsClient.stopVoiceSession(sessionRef.current);
    stopAudio(); stopCapture(); sessionRef.current = null; setSessionId(null); setTranscript(''); setLevel(0); updateState('idle');
  }, [stopAudio, stopCapture, updateState]);

  const togglePause = useCallback(() => {
    if (!sessionRef.current) return;
    if (stateRef.current === 'paused') { wsClient.resumeVoiceSession(sessionRef.current); updateState('listening'); }
    else { wsClient.pauseVoiceSession(sessionRef.current); stopAudio(); updateState('paused'); }
  }, [stopAudio, updateState]);

  useEffect(() => {
    const unsubscribe = wsClient.onMessage((event) => {
      const incomingId = event?.payload?.voice_session_id;
      if (!incomingId) return;

      if (event.payload?.auto_started || !sessionRef.current) {
        sessionRef.current = incomingId;
        setSessionId(incomingId);
      } else if (incomingId !== sessionRef.current) {
        return;
      }

      if (event.type === 'voice_session_state') { setRequiresWakeWord(Boolean(event.payload.requires_wake_word)); updateState(event.payload.state); }
      if (event.type === 'voice_transcript' || event.type === 'voice_transcript_partial' || event.type === 'voice_transcript_final') {
        setTranscript(event.payload?.text || event.payload?.transcript || '');
      }
      if (event.type === 'voice_turn_started') onTurnStarted(event.task_id, event.payload.text || '');
      if (event.type === 'voice_tts_audio') {
        if (event.payload?.audio) {
          playPcm24k(event.payload.audio);
        } else if (event.payload?.url) {
          stopAudio(); const audio = new Audio(absoluteAudioUrl(event.payload.url)); audioRef.current = audio;
          audio.onended = () => { audioRef.current = null; if (sessionRef.current) updateState('listening'); };
          audio.onerror = () => { setError('Voice audio could not play.'); updateState('listening'); };
          void audio.play().catch(() => { setError('Audio playback was blocked. Interact with Start Voice and try again.'); updateState('listening'); });
        }
      }
      if (event.type === 'voice_tts_stopped') stopAudio();
      if (event.type === 'voice_error') { setError(event.payload.message || 'Voice session error'); updateState('error'); }
    });
    return unsubscribe;
  }, [onTurnStarted, playPcm24k, stopAudio, updateState]);

  useEffect(() => {
    const pauseForPrivacy = () => {
      if (settings.wakeWordEnabled) return;
      if (sessionRef.current && stateRef.current !== 'paused') {
        wsClient.pauseVoiceSession(sessionRef.current);
        stopAudio();
        updateState('paused');
      }
    };
    const onVisibility = () => { if (document.hidden) pauseForPrivacy(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => { document.removeEventListener('visibilitychange', onVisibility); };
  }, [settings.wakeWordEnabled, stopAudio, updateState]);

  useEffect(() => () => stopSession(), [stopSession]);

  useEffect(() => {
    if (sessionRef.current) {
      stopSession();
    }
  }, [conversationId, stopSession]);

  if (state === 'idle' || state === 'error') {
    return (
      <div className="voice-launcher">
        <button type="button" className="voice-start-button" onClick={() => void startCapture()} disabled={!connected}>
          <Mic size={15} />
          <span>Start Voice Session</span>
        </button>
        {error && <span className="voice-error" role="alert" style={{ color: 'var(--status-red)', fontSize: '0.72rem', marginLeft: '10px' }}>{error}</span>}
      </div>
    );
  }

  // Generate multi-bar equalizer heights based on audio level
  const bars = [0.3, 0.6, 0.9, 0.5, 0.8, 1.0, 0.7, 0.4, 0.8, 0.5, 0.3, 0.6];

  return (
    <div className="composer-shell" style={{ paddingBottom: 0 }}>
      <section className={`voice-session voice-${state}`} aria-live="polite">
        <div className="voice-session-main">
          <span className="voice-privacy">
            <ShieldCheck size={13} /> Secure duplex voice active
          </span>
          <strong>
            {state === 'recording' ? 'Listening to your voice…' :
             state === 'processing' ? 'Understanding intent…' :
             state === 'speaking' ? 'Makima is speaking — interrupt anytime' :
             state === 'paused' ? 'Voice session paused' :
             requiresWakeWord ? 'Say “Hey Makima” to speak' : 'Listening for follow-up…'}
          </strong>

          {transcript && <span className="voice-transcript">“{transcript}”</span>}

          {/* Equalizer Visualizer */}
          <div className="voice-wave-container">
            {bars.map((weight, i) => {
              const activeHeight = state === 'recording' || state === 'speaking'
                ? Math.max(4, Math.min(18, Math.round(weight * Math.max(0.25, level) * 20)))
                : 4;
              return (
                <span
                  key={i}
                  className="voice-wave-bar"
                  style={{
                    height: `${activeHeight}px`,
                    background: state === 'speaking' ? 'var(--accent-purple-light)' : 'var(--accent-cyan)',
                    boxShadow: state === 'recording' || state === 'speaking' ? `0 0 6px ${state === 'speaking' ? 'var(--accent-purple-light)' : 'var(--accent-cyan)'}` : 'none',
                  }}
                />
              );
            })}
          </div>
        </div>

        <div className="voice-session-actions">
          <button
            type="button"
            onClick={togglePause}
            aria-label={state === 'paused' ? 'Resume voice session' : 'Pause voice session'}
            title={state === 'paused' ? 'Resume voice' : 'Pause voice'}
          >
            {state === 'paused' ? <Play size={15} /> : <Pause size={15} />}
          </button>
          <button
            type="button"
            onClick={() => { if (sessionId) { stopAudio(); wsClient.bargeIn(sessionId); } }}
            aria-label="Stop Makima speaking"
            title="Interrupt/Stop speaking"
          >
            <Volume2 size={15} />
          </button>
          <button
            type="button"
            onClick={stopSession}
            aria-label="Stop voice session"
            title="End voice session"
            style={{ color: 'var(--status-red)' }}
          >
            <Square size={14} fill="currentColor" />
          </button>
        </div>
      </section>
    </div>
  );
}

declare global { interface Window { webkitAudioContext?: typeof AudioContext; } }
