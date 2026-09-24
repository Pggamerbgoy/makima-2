/**
 * Canonical WebSocket protocol constants — mirrors apps/brain/ws_protocol.py.
 * The backend is the source of truth; keep these values in sync with
 * ServerMessageType / ClientMessageType enums.
 */

import type { GroundingSource, MediaItem, MediaKind } from '../types/chat';
import { httpBaseFromWs } from './httpBase';

export const ServerMessage = {
  PING: 'ping',
  PONG: 'pong',
  AI_CHUNK: 'ai_chunk',
  AI_RESPONSE_DONE: 'ai_response_done',
  AI_ERROR: 'ai_error',
  THINKING_STATUS: 'thinking_status',
  MEDIA_UPLOAD_STARTED: 'media_upload_started',
  MEDIA_PROCESSING: 'media_processing',
  MEDIA_READY: 'media_ready',
  MEDIA_ERROR: 'media_error',
  AGENT_STARTED: 'agent_started',
  AGENT_DONE: 'agent_done',
  AGENT_ERROR: 'agent_error',
  AGENT_PROGRESS: 'agent_progress',
  TOOL_CALL_STARTED: 'tool_call_started',
  TOOL_CALL_PROGRESS: 'tool_call_progress',
  TOOL_CALL_FINISHED: 'tool_call_finished',
  TOOL_COMPLETED: 'tool_completed',
  TOOL_ACTIVITY: 'tool_activity',
  AGENT_ACTIVITY: 'agent_activity',
  PLAN_MILESTONES: 'plan_milestones',
  PLAN_STEP_UPDATE: 'plan_step_update',
  ACTION_CONFIRM_REQUEST: 'action_confirm_request',
  MESSAGE_PREVIEW: 'message_preview',
  TOAST_NOTIFICATION: 'toast_notification',
  CREDENTIALS_DATA: 'credentials_data',
  AUTONOMY_MODE_CHANGED: 'autonomy_mode_changed',
  CANVAS_ITEM: 'canvas_item',
  VOICE_TTS_AUDIO: 'voice_tts_audio',
  VOICE_TTS_STARTED: 'voice_tts_started',
  VOICE_TTS_STOPPED: 'voice_tts_stopped',
  VOICE_ERROR: 'voice_error',
} as const;

export const ClientMessage = {
  PING: 'ping',
  USER_MESSAGE: 'user_message',
  CANCEL_TASK: 'cancel_task',
  REGENERATE_MESSAGE: 'regenerate_message',
  MODIFY_RESPONSE: 'modify_response',
  APPROVE_ACTION: 'approve_action',
  REJECT_ACTION: 'reject_action',
  FEEDBACK: 'feedback',
  SET_AUTONOMY_MODE: 'set_autonomy_mode',
  GET_AUTONOMY_STATUS: 'get_autonomy_status',
  VOICE_SPEAK: 'voice_speak',
  VOICE_TTS_STOP: 'voice_tts_stop',
  SAVE_CREDENTIAL: 'save_credential',
  GET_CREDENTIALS: 'get_credentials',
} as const;

// Legacy aliases some scripts/native layers still emit — accepted on input only.
export const LEGACY_STREAM_TYPES = ['stream_chunk', 'assistant_chunk'] as const;

// ─── Payload normalizers ────────────────────────────────────────────────────

const MEDIA_KINDS: MediaKind[] = ['image', 'video', 'audio', 'document'];

function absolutize(path: string | undefined, base: string): string | undefined {
  if (!path) return undefined;
  if (/^https?:\/\//i.test(path) || path.startsWith('data:') || path.startsWith('blob:')) return path;
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}

export function normalizeMediaItems(raw: unknown, wsUrl: string): MediaItem[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const base = httpBaseFromWs(wsUrl);
  const items: MediaItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const item = entry as Record<string, any>;
    const rawKind = String(item.kind || item.type || 'document').toLowerCase();
    const kind = (MEDIA_KINDS as string[]).includes(rawKind) ? (rawKind as MediaKind) : 'document';
    const url = absolutize(item.url || (item.media_id ? `/media/${item.media_id}/content` : undefined), base);
    if (!url) continue;
    items.push({
      id: String(item.id || item.media_id || `media_${items.length}_${Date.now()}`),
      type: kind,
      url,
      thumbnailUrl: absolutize(item.thumbnail_url, base),
      mimeType: item.mime_type || item.mimeType || undefined,
      title: item.name || item.title || undefined,
      alt: item.name || item.title || undefined,
      duration: typeof item.duration === 'number' ? item.duration : undefined,
      sourceUrl: absolutize(item.source_url, base),
      downloadable: true,
      size: typeof item.size === 'number' ? item.size : undefined,
      status: item.status === 'processing' || item.status === 'failed' ? item.status : 'ready',
    });
  }
  return items.length ? items : undefined;
}

export function normalizeGroundingSources(raw: unknown): GroundingSource[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const sources: GroundingSource[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const item = entry as Record<string, any>;
    const url = String(item.url || item.link || item.source_url || '');
    const title = String(item.title || item.name || item.snippet || url);
    if (!url && !title) continue;
    let domain = String(item.domain || '');
    if (!domain && url) {
      try {
        domain = new URL(url).hostname.replace(/^www\./, '');
      } catch {
        domain = '';
      }
    }
    sources.push({ title, url, domain });
  }
  return sources.length ? sources : undefined;
}
