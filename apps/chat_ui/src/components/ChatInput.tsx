import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useDropzone } from 'react-dropzone';
import { FolderOpen, LoaderCircle, Mic, MicOff, Paperclip, Plus, RotateCcw, Send, Sparkles, Square, X } from 'lucide-react';
import type { Attachment, MediaKind, MediaLibraryEntry } from '../types/chat';
import { uploadMedia } from '../services/mediaApi';
import { wsClient } from '../services/wsClient';

const MAX_BYTES: Record<MediaKind, number> = { image: 20 * 1024 * 1024, document: 20 * 1024 * 1024, audio: 20 * 1024 * 1024, video: 100 * 1024 * 1024 };

const COMPOSER_PLACEHOLDERS = [
  'Message Makima…',
  'Code likho…',
  'Web search karo…',
  'System check karo…',
  'Kuch bhi puchho…',
];

const SEND_BURST_BITS = [
  { dx: '-34px', dy: '-30px', rot: '-120deg', color: 'var(--primary)' },
  { dx: '-20px', dy: '-44px', rot: '80deg', color: 'var(--success)' },
  { dx: '-6px', dy: '-50px', rot: '200deg', color: 'var(--primary)' },
  { dx: '8px', dy: '-44px', rot: '-200deg', color: 'var(--warning)' },
  { dx: '20px', dy: '-30px', rot: '140deg', color: 'var(--primary)' },
  { dx: '-28px', dy: '-14px', rot: '60deg', color: 'var(--text-muted)' },
  { dx: '26px', dy: '-12px', rot: '-60deg', color: 'var(--success)' },
  { dx: '-12px', dy: '-24px', rot: '300deg', color: 'var(--warning)' },
  { dx: '12px', dy: '-22px', rot: '-300deg', color: 'var(--primary)' },
  { dx: '0px', dy: '-36px', rot: '180deg', color: 'var(--text-muted)' },
];
const kindFor = (mime: string): MediaKind => mime.startsWith('image/') ? 'image' : mime.startsWith('video/') ? 'video' : mime.startsWith('audio/') ? 'audio' : 'document';
const accept = { 'image/*': [], 'video/*': [], 'audio/*': [], 'application/pdf': [], 'text/plain': [], 'text/markdown': [], 'text/csv': [], 'application/json': [], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': [], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': [], 'application/vnd.openxmlformats-officedocument.presentationml.presentation': [] };

async function recorderBlobToPcmBase64(blob: Blob): Promise<string> {
  const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
  const context = new AudioCtx();
  try {
    const buffer = await context.decodeAudioData(await blob.arrayBuffer());
    const source = buffer.getChannelData(0);
    const ratio = buffer.sampleRate / 16000;
    const pcm = new Uint8Array(Math.floor(source.length / ratio) * 2);
    const view = new DataView(pcm.buffer);
    for (let i = 0; i < pcm.length / 2; i += 1) {
      const sample = Math.max(-1, Math.min(1, source[Math.min(source.length - 1, Math.floor(i * ratio))] || 0));
      view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    let binary = '';
    for (let i = 0; i < pcm.length; i += 0x8000) binary += String.fromCharCode(...pcm.subarray(i, i + 0x8000));
    return btoa(binary);
  } finally { await context.close(); }
}

interface ChatInputProps {
  onSendMessage: (text: string, attachments: Attachment[]) => void;
  onRecordingChange?: (isRecording: boolean) => void;
  onOpenLibrary?: () => void;
  libraryItem?: MediaLibraryEntry | null;
  onLibraryItemConsumed?: () => void; // Called after library item is injected as attachment
  onStop?: () => void;
  wsUrl?: string;
  disabled?: boolean;
  isGenerating?: boolean;
  activeModelLabel?: string;
  onOpenModelSelector?: () => void;
}

interface SlashCommand {
  cmd: string;
  label: string;
  description: string;
  autoSend?: boolean;
}

const SLASH_COMMANDS: SlashCommand[] = [
  { cmd: '/diagnostics', label: 'System Diagnostics', description: 'Run full CPU, RAM, battery & services health check', autoSend: true },
  { cmd: '/clean-temp', label: 'Clean Temporary Files', description: 'Scan and clean safe temporary and cache storage', autoSend: true },
  { cmd: '/capabilities', label: 'Describe Capabilities', description: 'List all registered system tools and agent powers', autoSend: true },
  { cmd: '/screen', label: 'Screen Perception', description: 'Inspect active windows and desktop screen state', autoSend: true },
  { cmd: '/plan', label: 'Autonomous Plan', description: 'Create multi-step execution plan: /plan <goal>', autoSend: false },
  { cmd: '/browse', label: 'Browse Web', description: 'Launch browser: /browse <url>', autoSend: false },
  { cmd: '/code', label: 'Code Interpreter', description: 'Run Python script: /code <snippet>', autoSend: false },
  { cmd: '/organize-desktop', label: 'Organize Desktop', description: 'Group desktop clutter into organized folders', autoSend: true },
];

export const ChatInput: React.FC<ChatInputProps> = ({ 
  onSendMessage, 
  onRecordingChange, 
  onOpenLibrary, 
  libraryItem,
  onLibraryItemConsumed,
  onStop, 
  wsUrl = 'ws://127.0.0.1:8080/ws', 
  disabled, 
  isGenerating,
  activeModelLabel,
  onOpenModelSelector
}) => {
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [micError, setMicError] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [slashIndex, setSlashIndex] = useState(0);
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  const [burstKey, setBurstKey] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const isSlashPrompt = text.startsWith('/') && !text.includes('\n') && !text.includes(' ');
  const filteredCommands = isSlashPrompt
    ? SLASH_COMMANDS.filter((c) =>
        c.cmd.toLowerCase().startsWith(text.split(' ')[0].toLowerCase()) ||
        c.label.toLowerCase().includes(text.replace('/', '').toLowerCase())
      )
    : [];

  useEffect(() => {
    setSlashIndex(0);
  }, [text]);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const t = window.setInterval(() => setPlaceholderIdx((i) => (i + 1) % COMPOSER_PLACEHOLDERS.length), 4000);
    return () => window.clearInterval(t);
  }, []);

  const selectCommand = (cmd: SlashCommand) => {
    if (cmd.autoSend) {
      onSendMessage(cmd.cmd, []);
      setText('');
    } else {
      setText(cmd.cmd + ' ');
      textareaRef.current?.focus();
    }
  };

  useEffect(() => { if (textareaRef.current) { textareaRef.current.style.height = 'auto'; textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`; } }, [text]);
  useEffect(() => { if (libraryItem) { setAttachments((prev) => prev.some((entry) => entry.mediaId === libraryItem.id) ? prev : [...prev, addLibraryAttachment(libraryItem)]); onLibraryItemConsumed?.(); } }, [libraryItem, onLibraryItemConsumed]);

  const addFile = async (file: File) => {
    const kind = kindFor(file.type || 'application/octet-stream');
    if (!file.size || file.size > MAX_BYTES[kind]) {
      setAttachments((prev) => [...prev, { id: `att_${Date.now()}`, name: file.name, mimeType: file.type, size: file.size, kind, uploadState: 'failed', error: `${kind} files have a ${MAX_BYTES[kind] / 1024 / 1024} MB limit.` }]);
      return;
    }
    const id = `att_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const localUrl = URL.createObjectURL(file);
    const initial: Attachment = { id, name: file.name, mimeType: file.type || 'application/octet-stream', size: file.size, kind, previewUrl: localUrl, sourceFile: file, uploadState: 'uploading', progress: 0 };
    setAttachments((prev) => [...prev, initial]);
    try {
      const item = await uploadMedia(file, wsUrl, (progress) => setAttachments((prev) => prev.map((entry) => entry.id === id ? { ...entry, progress } : entry)));
      setAttachments((prev) => prev.map((entry) => entry.id === id ? { ...entry, mediaId: item.id, previewUrl: item.url, uploadState: 'ready', progress: 100 } : entry));
    } catch (error) {
      setAttachments((prev) => prev.map((entry) => entry.id === id ? { ...entry, uploadState: 'failed', error: error instanceof Error ? error.message : 'Upload failed' } : entry));
    }
  };
  const retryUpload = async (attachment: Attachment) => {
    if (!attachment.sourceFile) return;
    setAttachments((prev) => prev.map((entry) => entry.id === attachment.id ? { ...entry, uploadState: 'uploading', progress: 0, error: undefined } : entry));
    try {
      const item = await uploadMedia(attachment.sourceFile, wsUrl, (progress) => setAttachments((prev) => prev.map((entry) => entry.id === attachment.id ? { ...entry, progress } : entry)));
      setAttachments((prev) => prev.map((entry) => entry.id === attachment.id ? { ...entry, mediaId: item.id, previewUrl: item.url, uploadState: 'ready', progress: 100 } : entry));
    } catch (error) {
      setAttachments((prev) => prev.map((entry) => entry.id === attachment.id ? { ...entry, uploadState: 'failed', error: error instanceof Error ? error.message : 'Upload failed' } : entry));
    }
  };
  const handleDrop = (files: File[]) => { files.forEach((file) => void addFile(file)); setMenuOpen(false); };
  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({ onDrop: handleDrop, accept, noClick: true, noKeyboard: true, multiple: true });
  const remove = (id: string) => setAttachments((prev) => { const item = prev.find((entry) => entry.id === id); if (item?.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl); return prev.filter((entry) => entry.id !== id); });
  const isUploading = attachments.some((entry) => entry.uploadState === 'uploading');
  const handleSend = () => { const ready = attachments.filter((entry) => entry.uploadState === 'ready' && entry.mediaId); if ((!text.trim() && !ready.length) || disabled || isUploading) return; setBurstKey((k) => k + 1); onSendMessage(text.trim(), ready); setText(''); attachments.forEach((entry) => entry.previewUrl?.startsWith('blob:') && URL.revokeObjectURL(entry.previewUrl)); setAttachments([]); };
  const handlePaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => { const image = Array.from(event.clipboardData.files).find((file) => file.type.startsWith('image/')); if (image) { event.preventDefault(); void addFile(image); } };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (filteredCommands.length > 0) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setSlashIndex((prev) => (prev + 1) % filteredCommands.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setSlashIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
        return;
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault();
        if (filteredCommands[slashIndex]) {
          selectCommand(filteredCommands[slashIndex]);
          return;
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setText('');
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const startRecording = async () => {
    setMicError('');
    setIsRecording(true); onRecordingChange?.(true); wsClient.sendPTTDown();
    try { const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }); const recorder = new MediaRecorder(stream); mediaRecorderRef.current = recorder; audioChunksRef.current = []; recorder.ondataavailable = (event) => event.data.size && audioChunksRef.current.push(event.data); recorder.onstop = () => { const blob = new Blob(audioChunksRef.current, { type: recorder.mimeType || 'audio/webm' }); void recorderBlobToPcmBase64(blob).then((audio) => wsClient.sendPTTUp(audio)).catch((err) => { console.error('[ChatInput] PCM encode failed:', err); wsClient.sendPTTUp(); }).finally(() => stream.getTracks().forEach((track) => track.stop())); }; recorder.start(); } catch (err) {
      const msg = err instanceof DOMException && err.name === 'NotAllowedError'
        ? 'Microphone permission denied. Allow mic access in browser settings.'
        : 'Could not start microphone recording.';
      console.error('[ChatInput] Recording error:', err);
      setMicError(msg);
      window.setTimeout(() => setMicError(''), 6000);
      setIsRecording(false); onRecordingChange?.(false); wsClient.sendPTTUp();
    }
  };
  const stopRecording = () => { setIsRecording(false); onRecordingChange?.(false); if (mediaRecorderRef.current?.state === 'recording') mediaRecorderRef.current.stop(); else wsClient.sendPTTUp(); };

  return <div {...getRootProps()} className="composer-shell">
    <input {...getInputProps()} />
    {isDragActive && <div className="composer-drop-overlay">Drop files to attach</div>}
    <div className="composer-panel">
      {attachments.length > 0 && <div className="attachment-grid">{attachments.map((att) => <div className={`attachment-preview ${att.uploadState}`} key={att.id}>
        {att.kind === 'image' && att.previewUrl ? <img src={att.previewUrl} alt={att.name} /> : <div className="attachment-file-icon">{att.kind === 'video' ? '▶' : att.kind === 'audio' ? '♫' : '▤'}</div>}
        <div className="attachment-info"><strong title={att.name}>{att.name}</strong><span>{att.uploadState === 'uploading' ? `Uploading ${att.progress || 0}%` : att.error || (att.uploadState === 'failed' ? 'Upload failed' : 'Ready')}</span></div>
        {att.uploadState === 'failed' && att.sourceFile && <button onClick={() => void retryUpload(att)} aria-label={`Retry upload for ${att.name}`} title="Retry upload"><RotateCcw size={14} /></button>}
        <button onClick={() => remove(att.id)} aria-label={`Remove ${att.name}`}><X size={14} /></button>
        {att.uploadState === 'uploading' && <div className="attachment-progress"><i style={{ width: `${att.progress || 0}%` }} /></div>}
      </div>)}</div>}
      {filteredCommands.length > 0 && (
        <div className="composer-slash-menu">
          {filteredCommands.map((cmd, idx) => (
            <div
              key={cmd.cmd}
              className={`composer-slash-item ${idx === slashIndex ? 'selected' : ''}`}
              onClick={() => selectCommand(cmd)}
              onMouseEnter={() => setSlashIndex(idx)}
            >
              <div className="composer-slash-left">
                <span className="composer-slash-cmd">{cmd.cmd}</span>
                <span className="composer-slash-label">{cmd.label}</span>
              </div>
              <span className="composer-slash-desc">{cmd.description}</span>
            </div>
          ))}
        </div>
      )}
      <div className="composer-row">
        <div className="composer-add-wrap"><button className="composer-icon-button" onClick={() => setMenuOpen((value) => !value)} aria-label="Add attachment" aria-expanded={menuOpen}><Plus size={20} /></button>{menuOpen && <div className="composer-add-menu"><button onClick={open}><Paperclip size={16} /> Upload files</button><button onClick={() => { onOpenLibrary?.(); setMenuOpen(false); }}><FolderOpen size={16} /> Local library</button><span>Paste an image directly into the message box</span></div>}</div>
        <textarea ref={textareaRef} value={text} onChange={(event) => setText(event.target.value)} onPaste={handlePaste} onKeyDown={handleKeyDown} placeholder={COMPOSER_PLACEHOLDERS[placeholderIdx]} rows={1} aria-label="Message Makima" />
        <button
          className={`composer-icon-button ${isRecording ? 'recording' : ''}`}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={isRecording ? stopRecording : undefined}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          onTouchCancel={stopRecording}
          aria-label={isRecording ? 'Stop recording' : 'Hold to speak'}
          title={isRecording ? 'Release to send audio' : 'Hold to speak'}
        >
          {isRecording ? <MicOff size={19} /> : <Mic size={19} />}
        </button>
        <AnimatePresence mode="wait" initial={false}>
          {isGenerating ? (
            <motion.button
              key="stop"
              className="composer-send-button stop"
              onClick={onStop}
              aria-label="Stop generation"
              initial={{ scale: 0.7, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.7, opacity: 0 }}
              transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
            >
              <Square size={16} fill="currentColor" />
            </motion.button>
          ) : (
            <motion.button
              key="send"
              className="composer-send-button"
              onClick={handleSend}
              disabled={disabled || isUploading || (!text.trim() && !attachments.some((entry) => entry.uploadState === 'ready'))}
              title={isUploading ? 'Waiting for upload to finish…' : 'Send message'}
              aria-label="Send message"
              initial={{ scale: 0.7, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.7, opacity: 0 }}
              transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
            >
              {disabled ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}
            </motion.button>
          )}
        </AnimatePresence>
        {burstKey > 0 && (
          <span key={burstKey} className="send-burst" aria-hidden="true">
            {SEND_BURST_BITS.map((b, i) => (
              <i key={i} style={{ background: b.color, '--dx': b.dx, '--dy': b.dy, '--rot': b.rot } as React.CSSProperties} />
            ))}
          </span>
        )}
      </div>
      {micError && <div style={{ fontSize: '0.75rem', color: 'var(--danger)', padding: '4px 14px 0' }}>{micError}</div>}
      <div className="composer-hint">
        {onOpenModelSelector && (
          <button
            type="button"
            className="composer-model-chip"
            onClick={onOpenModelSelector}
            title="Click to switch AI model or provider"
          >
            <Sparkles size={11} className="composer-chip-sparkle" />
            <span className="composer-chip-text">{activeModelLabel || 'Model'}</span>
            <span className="composer-chip-caret">▾</span>
          </button>
        )}
        <span className="composer-hint-shortcuts">
          Enter to send · Shift+Enter for newline · {text.length}/12,000
        </span>
      </div>
    </div>
  </div>;
};

function addLibraryAttachment(item: MediaLibraryEntry): Attachment { return { id: `att_${item.id}`, mediaId: item.id, name: item.name, mimeType: item.mimeType || 'application/octet-stream', size: item.size || 0, kind: item.type, previewUrl: item.url, uploadState: 'ready', progress: 100 }; }
