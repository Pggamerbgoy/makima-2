import type { LLMProvider } from '../types/chat';
import { DEFAULT_WS_URL, httpBaseFromWs } from './httpBase';

function getApiBase(wsUrl = DEFAULT_WS_URL): string {
  return httpBaseFromWs(wsUrl);
}

async function request<T>(path: string, init: RequestInit = {}, wsUrl?: string): Promise<T> {
  const response = await fetch(`${getApiBase(wsUrl)}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Brain request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getLLMProviders(wsUrl?: string): Promise<LLMProvider[]> {
  const result = await request<{ providers: LLMProvider[] }>('/llm/providers', {}, wsUrl);
  return result.providers;
}

export async function saveLLMProvider(
  provider: string,
  payload: { apiKey?: string; model: string; baseUrl?: string; enabled: boolean },
  wsUrl?: string,
): Promise<LLMProvider> {
  const result = await request<{ provider: LLMProvider }>(`/llm/providers/${encodeURIComponent(provider)}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }, wsUrl);
  return result.provider;
}

export async function deleteConversation(
  conversationId: string,
  wsUrl?: string,
): Promise<{ status: string; message?: string }> {
  return request<{ status: string; message?: string }>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
    wsUrl,
  );
}

// ── Conversation history sync ────────────────────────────────────────────────

export interface ConversationSummary {
  conversation_id: string;
  started: number;
  last_active: number;
  turn_count: number;
}

export interface ConversationTurn {
  id: number | string;
  conversation_id: string;
  created_at: number;
  role: string;
  message: string;
}

export async function listConversations(wsUrl?: string): Promise<ConversationSummary[]> {
  const result = await request<{ conversations: ConversationSummary[] }>('/conversations', { method: 'GET' }, wsUrl);
  return Array.isArray(result.conversations) ? result.conversations : [];
}

export async function getConversationMessages(
  conversationId: string,
  wsUrl?: string,
): Promise<ConversationTurn[]> {
  const result = await request<{ messages: ConversationTurn[] }>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'GET' },
    wsUrl,
  );
  return Array.isArray(result.messages) ? result.messages : [];
}

// ── Settings sync ────────────────────────────────────────────────────────────

export interface BrainSettingsSnapshot {
  voice?: { wake_word_enabled?: boolean; ptt_key?: string };
  ollama?: { default_model?: string };
  general?: Record<string, unknown>;
  ok?: boolean;
}

export async function getSettings(wsUrl?: string): Promise<BrainSettingsSnapshot> {
  return request<BrainSettingsSnapshot>('/settings', { method: 'GET' }, wsUrl);
}

export async function saveSettings(payload: Record<string, unknown>, wsUrl?: string): Promise<BrainSettingsSnapshot> {
  return request<BrainSettingsSnapshot>('/settings', {
    method: 'POST',
    body: JSON.stringify(payload),
  }, wsUrl);
}

// ── OAuth ────────────────────────────────────────────────────────────────────

export async function oauthStatus(wsUrl?: string): Promise<Record<string, boolean>> {
  const result = await request<{ providers: Record<string, boolean> }>('/auth/status', { method: 'GET' }, wsUrl);
  return result.providers || {};
}

export async function oauthDisconnect(provider: string, wsUrl?: string): Promise<{ ok: boolean; message?: string; error?: string }> {
  return request<{ ok: boolean; message?: string; error?: string }>(
    `/auth/disconnect/${encodeURIComponent(provider)}`,
    { method: 'DELETE' },
    wsUrl,
  );
}

export function oauthLoginUrl(provider: string, wsUrl?: string): string {
  return `${httpBaseFromWs(wsUrl)}/auth/login/${encodeURIComponent(provider)}`;
}

// ── System status ────────────────────────────────────────────────────────────

export interface SystemStatusSnapshot {
  boot?: Record<string, any>;
  services?: Record<string, { status: string; error?: string | null }>;
  agents?: any;
  llm_backends?: Record<string, { enabled: boolean; model: string; supports_tools: boolean }>;
  local_models?: string[];
  system_metrics?: {
    cpu_percent: number;
    ram_percent: number;
    ram_used_gb: number;
    ram_total_gb: number;
    disk_percent: number;
    processes_count: number;
    gpu?: {
      name?: string;
      gpu_percent?: number;
      memory_used_mb?: number;
      memory_total_mb?: number;
      error?: string;
    };
    gpu_percent?: number;
    error?: string;
  };
  active_llm?: {
    provider: string;
    model: string;
  };
}

export async function getSystemStatus(wsUrl?: string): Promise<SystemStatusSnapshot> {
  return request<SystemStatusSnapshot>('/status', { method: 'GET' }, wsUrl);
}

export async function testIntegration(
  integrationId: string,
  wsUrl?: string,
): Promise<{ ok: boolean; message: string; note?: string }> {
  return request<{ ok: boolean; message: string; note?: string }>(
    `/integrations/${encodeURIComponent(integrationId)}/test`,
    { method: 'POST' },
    wsUrl,
  );
}

// ── Tools & MCP ───────────────────────────────────────────────────────────────

export interface ToolEntry {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  is_destructive: boolean;
  priority: number;
  source: 'native' | 'mcp';
}

export interface ToolsSnapshot {
  ok: boolean;
  error?: string;
  total: number;
  enabled_count: number;
  categories: string[];
  tools: ToolEntry[];
}

export interface McpServerEntry {
  name: string;
  enabled: boolean;
  transport: string;
  command?: string[] | string | null;
  url?: string | null;
  has_env: boolean;
  has_headers: boolean;
  tools_registered: number;
  live: boolean;
}

export interface McpSnapshot {
  ok: boolean;
  error?: string;
  servers: McpServerEntry[];
  total_mcp_tools: number;
}

export async function listTools(wsUrl?: string): Promise<ToolsSnapshot> {
  return request<ToolsSnapshot>('/tools', { method: 'GET' }, wsUrl);
}

export async function setToolEnabled(
  toolName: string,
  enabled: boolean,
  wsUrl?: string,
): Promise<{ ok: boolean; error?: string; name?: string; enabled?: boolean }> {
  return request(`/tools/${encodeURIComponent(toolName)}/enable`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  }, wsUrl);
}

export async function listMcpServers(wsUrl?: string): Promise<McpSnapshot> {
  return request<McpSnapshot>('/mcp', { method: 'GET' }, wsUrl);
}

export interface McpServerPayload {
  name: string;
  enabled?: boolean;
  transport?: string;
  command?: string | string[];
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
}

export async function addMcpServer(
  payload: McpServerPayload,
  wsUrl?: string,
): Promise<{ ok: boolean; error?: string; server?: McpServerEntry; reload?: Record<string, unknown> }> {
  return request('/mcp', {
    method: 'POST',
    body: JSON.stringify(payload),
  }, wsUrl);
}

export async function deleteMcpServer(
  serverName: string,
  wsUrl?: string,
): Promise<{ ok: boolean; error?: string; removed?: string; reload?: Record<string, unknown> }> {
  return request(`/mcp/${encodeURIComponent(serverName)}`, { method: 'DELETE' }, wsUrl);
}

export async function reloadMcpServers(
  wsUrl?: string,
): Promise<{ ok: boolean; error?: string; servers_configured?: number; servers_enabled?: number; mcp_tools_registered?: number }> {
  return request('/mcp/reload', { method: 'POST' }, wsUrl);
}
