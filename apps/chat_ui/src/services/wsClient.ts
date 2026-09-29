import type { Attachment } from '../types/chat';
import { httpBaseFromWs } from './httpBase';

type MessageHandler = (data: any) => void;
type ConnectionStatusHandler = (connected: boolean) => void;

export class WSClient {
  private socket: WebSocket | null = null;
  private url: string;
  private messageHandlers: Set<MessageHandler> = new Set();
  private statusHandlers: Set<ConnectionStatusHandler> = new Set();
  private isConnected: boolean = false;
  private reconnectInterval: number = 3000;
  private shouldReconnect: boolean = true;

  constructor(url: string = 'ws://127.0.0.1:8080/ws') {
    this.url = url;
  }

  public configure(url: string): void {
    const nextUrl = url.trim();
    if (!nextUrl || nextUrl === this.url) return;
    this.shouldReconnect = false;
    this.socket?.close();
    this.socket = null;
    this.isConnected = false;
    this.url = nextUrl;
    this.shouldReconnect = true;
    this.connect();
  }

  public getUrl(): string { return this.url; }

  public connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.socket = new WebSocket(this.url);

      this.socket.onopen = () => {
        console.log('[WS] Connected to Makima Brain');
        this.isConnected = true;
        this.notifyStatus(true);
      };

      this.socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.messageHandlers.forEach((handler) => handler(data));
        } catch (e) {
          console.error('[WS] Failed to parse message:', event.data, e);
        }
      };

      this.socket.onclose = () => {
        console.log('[WS] Disconnected');
        this.isConnected = false;
        this.notifyStatus(false);
        if (this.shouldReconnect) {
          setTimeout(() => this.connect(), this.reconnectInterval);
        }
      };

      this.socket.onerror = (err) => {
        console.error('[WS] Error:', err);
        this.socket?.close();
      };
    } catch (e) {
      console.error('[WS] Connection exception:', e);
      this.notifyStatus(false);
    }
  }

  public disconnect(): void {
    this.shouldReconnect = false;
    if (this.socket) {
      this.socket.close();
    }
  }

  public onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  public onStatusChange(handler: ConnectionStatusHandler): () => void {
    this.statusHandlers.add(handler);
    handler(this.isConnected);
    return () => this.statusHandlers.delete(handler);
  }

  private notifyStatus(status: boolean) {
    this.statusHandlers.forEach((h) => h(status));
  }

  public ping(): Promise<number> {
    return new Promise((resolve, reject) => {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'));
        return;
      }
      const startMs = performance.now();
      const pingId = 'ping_' + Math.random().toString(36).substring(2, 9);
      let cleanup: (() => void) | null = null;
      const timer = setTimeout(() => {
        if (cleanup) cleanup();
        reject(new Error('Ping timeout (5000ms)'));
      }, 5000);

      cleanup = this.onMessage((data) => {
        if (data && (data.type === 'pong' || data.payload?.type === 'pong')) {
          clearTimeout(timer);
          if (cleanup) cleanup();
          const rtt = Math.max(1, Math.round(performance.now() - startMs));
          resolve(rtt);
        }
      });

      this.send('ping', pingId, { timestamp: Date.now() });
    });
  }

  public sendMessage(
    text: string,
    conversationId: string,
    attachments?: Attachment[],
    options?: {
      provider?: string;
      model?: string;
      apiKey?: string;
      baseUrl?: string;
    }
  ): string {
    const taskId = 'task_' + Math.random().toString(36).substring(2, 9);
    const uploaded = (attachments || []).filter((att) => att.mediaId).map((att) => ({
      id: att.mediaId,
      kind: att.kind || this.kindFromMime(att.mimeType),
      mime_type: att.mimeType,
    }));

    // Legacy fallback remains intentionally limited to one attachment. New
    // uploads always use media ids and never put raw binary on the socket.
    let legacyPayload: Record<string, string> = {};
    const legacy = (attachments || []).find((att) => !att.mediaId && att.dataUrl);
    if (!uploaded.length && legacy?.dataUrl) {
      const b64Data = legacy.dataUrl.includes(',') ? legacy.dataUrl.split(',')[1] : legacy.dataUrl;
      legacyPayload = { file_name: legacy.name, file_mime: legacy.mimeType, file_data: b64Data };
    }

    const payload = {
      v: 1,
      type: 'user_message',
      task_id: taskId,
      payload: {
        text,
        conversation_id: conversationId,
        ...(uploaded.length ? { attachments: uploaded } : legacyPayload),
        ...(options?.provider ? { provider: options.provider } : {}),
        ...(options?.model ? { model: options.model } : {}),
        ...(options?.apiKey ? { api_key: options.apiKey } : {}),
        ...(options?.baseUrl ? { base_url: options.baseUrl } : {}),
      },
    };

    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    } else {
      console.warn('[WS] Cannot send message, socket not open');
    }

    return taskId;
  }

  private kindFromMime(mimeType: string): string {
    if (mimeType.startsWith('image/')) return 'image';
    if (mimeType.startsWith('video/')) return 'video';
    if (mimeType.startsWith('audio/')) return 'audio';
    return 'document';
  }

  private send(type: string, taskId: string, payload: Record<string, any> = {}): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ v: 1, type, task_id: taskId, payload }));
    }
  }

  public stopTask(taskId: string): void {
    this.send('cancel_task', taskId);
  }

  public regenerateMessage(
    text: string,
    conversationId: string,
    options?: {
      provider?: string;
      model?: string;
      apiKey?: string;
      baseUrl?: string;
    }
  ): string {
    const taskId = 'task_' + Math.random().toString(36).substring(2, 9);
    this.send('regenerate_message', taskId, {
      text,
      conversation_id: conversationId,
      ...(options?.provider ? { provider: options.provider } : {}),
      ...(options?.model ? { model: options.model } : {}),
      ...(options?.apiKey ? { api_key: options.apiKey } : {}),
      ...(options?.baseUrl ? { base_url: options.baseUrl } : {}),
    });
    return taskId;
  }

  public modifyResponse(
    text: string,
    instruction: string,
    conversationId: string,
    options?: {
      provider?: string;
      model?: string;
      apiKey?: string;
      baseUrl?: string;
    }
  ): string {
    const taskId = 'task_' + Math.random().toString(36).substring(2, 9);
    this.send('modify_response', taskId, {
      text,
      instruction,
      conversation_id: conversationId,
      ...(options?.provider ? { provider: options.provider } : {}),
      ...(options?.model ? { model: options.model } : {}),
      ...(options?.apiKey ? { api_key: options.apiKey } : {}),
      ...(options?.baseUrl ? { base_url: options.baseUrl } : {}),
    });
    return taskId;
  }

  public approveAction(taskId: string, action: string): void {
    this.send('approve_action', taskId, { action });
  }

  public rejectAction(taskId: string, action: string): void {
    this.send('reject_action', taskId, { action });
  }

  public setAutonomyMode(mode: 'off' | 'suggest' | 'auto'): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ v: 1, type: 'set_autonomy_mode', payload: { mode } }));
    }
  }

  public getAutonomyStatus(): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ v: 1, type: 'get_autonomy_status', payload: {} }));
    }
  }

  public sendPTTDown(): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ v: 1, type: 'ptt_down', payload: {} }));
    }
  }

  public sendPTTUp(audioBase64: string = ''): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({
        v: 1,
        type: 'ptt_up',
        payload: { audio_data: audioBase64 },
      }));
    }
  }

  public startVoiceSession(conversationId: string, settings: Record<string, unknown>, apiKey?: string): string {
    const voiceSessionId = `voice_${crypto.randomUUID().replace(/-/g, '')}`;
    this.send('voice_session_start', voiceSessionId, {
      voice_session_id: voiceSessionId,
      conversation_id: conversationId,
      settings,
      api_key: apiKey,
    });
    return voiceSessionId;
  }

  public sendVoiceUtterance(voiceSessionId: string, audioBase64: string, sampleRate: number = 16000): string {
    const taskId = `task_${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`;
    this.send('voice_audio_utterance', taskId, {
      voice_session_id: voiceSessionId,
      audio_data: audioBase64,
      audio_format: 'pcm_s16le',
      sample_rate: sampleRate,
    });
    return taskId;
  }

  public pauseVoiceSession(voiceSessionId: string): void { this.send('voice_session_pause', voiceSessionId, { voice_session_id: voiceSessionId }); }
  public resumeVoiceSession(voiceSessionId: string): void { this.send('voice_session_resume', voiceSessionId, { voice_session_id: voiceSessionId }); }
  public stopVoiceSession(voiceSessionId: string): void { this.send('voice_session_stop', voiceSessionId, { voice_session_id: voiceSessionId }); }
  public bargeIn(voiceSessionId: string): void { this.send('voice_barge_in', voiceSessionId, { voice_session_id: voiceSessionId }); }
  public requestVoiceSpeech(voiceSessionId: string, taskId: string, text: string): void {
    this.send('voice_speak', taskId, { voice_session_id: voiceSessionId, text });
  }

  /**
   * One-shot TTS: sends `voice_speak` with an ephemeral session ID.
   * The brain's synthesize_session_tts handler will respond with a
   * `voice_tts_audio` event on this same WS connection.
   * @returns the ephemeral voiceSessionId (so caller can filter incoming events)
   */
  public speakText(text: string, taskId?: string): string {
    const voiceSessionId = `tts_${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`;
    const tid = taskId || `task_${Math.random().toString(36).substring(2, 9)}`;
    this.send('voice_speak', tid, { voice_session_id: voiceSessionId, text });
    return voiceSessionId;
  }

  /**
   * Cancel an in-progress one-shot TTS playback.
   */
  public stopSpeaking(voiceSessionId: string): void {
    this.send('voice_tts_stop', voiceSessionId, { voice_session_id: voiceSessionId });
  }

  public sendFeedback(taskId: string, positive: boolean, category: string = 'general', context?: { agent_name?: string; message_id?: string }): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({
        v: 1,
        type: 'feedback',
        task_id: taskId,
        payload: { positive, category, agent_name: context?.agent_name || '', message_id: context?.message_id || '' },
      }));
    }
  }

  public saveCredential(serviceName: string, config: Record<string, any>): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({
        v: 1,
        type: 'save_credential',
        payload: { service_name: serviceName, config },
      }));
    }
  }

  public getCredentials(): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({
        v: 1,
        type: 'get_credentials',
        payload: {},
      }));
    }
  }

  public cancelTask(taskId: string): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({
        v: 1,
        type: 'cancel_task',
        task_id: taskId,
        payload: {},
      }));
    }
  }

  public openFile(filePath: string): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({
        v: 1,
        type: 'open_file',
        payload: { path: filePath },
      }));
    } else {
      fetch(`${httpBaseFromWs(this.url)}/api/open-file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: filePath }),
      }).catch((e) => console.error('[WS] Failed to open file via HTTP fallback:', e));
    }
  }
}

export const wsClient = new WSClient();
