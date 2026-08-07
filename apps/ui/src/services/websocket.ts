/**
 * Makima v7.1 ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â WebSocket Client Service
 *
 * Implements Protocol v1 matching `apps/brain/ws_protocol.py`.
 * Handshakes with `ws://127.0.0.1:8080/ws`.
 * Handles auto-reconnect with exponential backoff.
 */

export const PROTOCOL_VERSION = 1;

export interface WSMessage<T = Record<string, unknown>> {
  v: number;
  type: string;
  payload: T;
  task_id?: string;
  timestamp: number;
}

export type WSListener = (msg: WSMessage) => void;

class MakimaWebSocketClient {
  private ws: WebSocket | null = null;
  private listeners: Set<WSListener> = new Set();
  private statusListeners: Set<(connected: boolean) => void> = new Set();
  private isConnected = false;
  private reconnectAttempts = 0;
  private maxBackoffMs = 30000;
  private reconnectTimer: number | null = null;
  private disconnectNotified = false;
  private url: string;

  constructor(url = "ws://127.0.0.1:8080/ws") {
    this.url = url;
  }

  public connect() {
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.disconnectNotified = false;
        console.log("[Makima WS] Connected to backend");
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.notifyStatus(true);
      };

      this.ws.onmessage = (event) => {
        try {
          const data: WSMessage = JSON.parse(event.data);
          if (data.v !== PROTOCOL_VERSION && data.type !== "error") {
            console.warn(`[Makima WS] Protocol mismatch: expected ${PROTOCOL_VERSION}, got ${data.v}`);
          }
          this.listeners.forEach((listener) => listener(data));
        } catch (err) {
          console.error("[Makima WS] Failed to parse JSON message:", err);
        }
      };

      this.ws.onclose = () => {
        console.log("[Makima WS] Connection closed");
        this.handleDisconnect();
      };

      this.ws.onerror = (err) => {
        console.error("[Makima WS] Error:", err);
        this.handleDisconnect();
      };
    } catch (e) {
      console.error("[Makima WS] Connection failed to initialize:", e);
      this.handleDisconnect();
    }
  }

  private handleDisconnect() {
    if (this.disconnectNotified && !this.ws) return;
    this.disconnectNotified = true;
    this.notifyStatus(false);
    this.ws = null;

    // Exponential backoff reconnect
    this.reconnectAttempts++;
    const backoff = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), this.maxBackoffMs);
    console.log(`[Makima WS] Reconnecting in ${(backoff / 1000).toFixed(1)}s...`);
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(() => { this.reconnectTimer = null; this.connect(); }, backoff);
  }

  public send(type: string, payload: Record<string, unknown> = {}, taskId?: string): string | false {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[Makima WS] Cannot send message, WebSocket not connected.");
      return false;
    }

    const message: WSMessage = {
      v: PROTOCOL_VERSION,
      type,
      payload,
      task_id: taskId || `task-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
      timestamp: Date.now() / 1000,
    };

    try {
      this.ws.send(JSON.stringify(message));
      return message.task_id || "sent";
    } catch (error) {
      console.error("[Makima WS] Send failed:", error);
      return false;
    }
  }

  public subscribe(listener: WSListener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  public subscribeStatus(listener: (connected: boolean) => void) {
    this.statusListeners.add(listener);
    listener(this.isConnected);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  private notifyStatus(connected: boolean) {
    this.statusListeners.forEach((listener) => listener(connected));
  }

  public getConnected(): boolean {
    return this.isConnected;
  }
}

export const wsClient = new MakimaWebSocketClient();
