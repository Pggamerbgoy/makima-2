export type MediaKind = 'image' | 'video' | 'audio' | 'document';

export interface MediaItem {
  id: string;
  type: MediaKind;
  url: string;
  thumbnailUrl?: string;
  mimeType?: string;
  title?: string;
  alt?: string;
  duration?: number;
  sourceUrl?: string;
  downloadable?: boolean;
  size?: number;
  status?: 'ready' | 'processing' | 'failed';
}

export interface MediaLibraryEntry extends MediaItem {
  name: string;
  createdAt?: number;
}

export type UploadState = 'local' | 'uploading' | 'ready' | 'failed';

export interface Attachment {
  id: string;
  name: string;
  mimeType: string;
  dataUrl?: string; // local preview only; never sent over the chat socket after upload
  size: number;
  kind?: MediaKind;
  mediaId?: string;
  previewUrl?: string;
  uploadState?: UploadState;
  progress?: number;
  error?: string;
  sourceFile?: File;
}

export interface GroundingSource {
  title: string;
  url: string;
  domain: string;
}

export interface Message {
  id: string;
  sender: 'user' | 'ai';
  text: string;
  timestamp: number;
  isStreaming?: boolean;
  attachments?: Attachment[];
  media?: MediaItem[];
  thought?: string;
  sources?: GroundingSource[];
  agentActivity?: AgentActivityEvent[];
  taskId?: string;
  status?: 'streaming' | 'complete' | 'error' | 'cancelled';
  error?: { code?: string; message: string; retryable?: boolean };
  actionConfirmation?: { action: string; description: string; riskLevel?: string; status?: 'pending' | 'approved' | 'rejected' };
  agent_name?: string;
  format?: string;
}

export interface AgentActivityEvent {
  id: string;
  type: string;
  agent?: string;
  status?: string;
  message?: string;
  progress?: number;
  timestamp: number;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  messages: Message[];
}

export interface AppConnector {
  id: string;
  name: string;
  description: string;
  category: 'system' | 'web' | 'media' | 'messaging';
  enabled: boolean;
}

export interface AppSettings {
  llmProvider: string;
  model: string;
  theme: 'dark' | 'light';
  wsUrl: string;
  wakeWordEnabled: boolean;
  autoReadAloud: boolean;
  ttsSpeed: number; // 0.5 to 2.0
  followupTimeoutSeconds: number;
  silenceTimeoutMs: number;
  maxUtteranceSeconds: number;
  voiceLanguage: 'auto' | 'en' | 'hi';
  persona: 'general' | 'coder' | 'researcher' | 'creative';
  systemPrompt?: string;
  privacyMode?: boolean;
  connectors: Record<string, boolean>;
  apiKeys?: Record<string, string>;
  baseUrls?: Record<string, string>;
}

export interface LLMProvider {
  id: string;
  name: string;
  model: string;
  models: string[];
  enabled: boolean;
  configured: boolean;
  keyHint: string;
  baseUrl: string;
  local: boolean;
  capabilities: { text: boolean; image: boolean; audio: boolean; video: boolean };
  badge?: string;
  description?: string;
  isActive?: boolean;
}

export interface CanvasItem {
  id: string;
  title: string;
  language: string;
  content: string;
}
