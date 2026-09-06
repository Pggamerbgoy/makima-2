import type { LLMProvider } from '../types/chat';

const DEFAULT_WS_URL = 'ws://127.0.0.1:8080/ws';

function getApiBase(wsUrl = DEFAULT_WS_URL): string {
  try {
    const parsed = new URL(wsUrl);
    parsed.protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
    parsed.pathname = '';
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString().replace(/\/$/, '');
  } catch {
    return 'http://127.0.0.1:8080';
  }
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
