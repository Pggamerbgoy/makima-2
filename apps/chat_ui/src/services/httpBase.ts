/** Shared HTTP base URL derivation from the configured WebSocket URL. */

export const DEFAULT_WS_URL = 'ws://127.0.0.1:8080/ws';
export const DEFAULT_HTTP_BASE = 'http://127.0.0.1:8080';

export function httpBaseFromWs(wsUrl: string = DEFAULT_WS_URL): string {
  try {
    const url = new URL(wsUrl);
    url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
    url.pathname = '';
    url.search = '';
    url.hash = '';
    return url.toString().replace(/\/$/, '');
  } catch {
    return DEFAULT_HTTP_BASE;
  }
}
