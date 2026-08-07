import { useState, useEffect, useCallback } from "react";
import { wsClient, WSMessage } from "../services/websocket";

export interface ChatMessage {
  id: string;
  role: "user" | "ai";
  content: string;
  timestamp: Date;
  agent?: string;
  isStreaming?: boolean;
}

export interface ConfirmModalRequest {
  taskId: string;
  action: string;
  description: string;
  riskLevel: string;
}

export interface AgentActivityState {
  agentName: string;
  subtask: string;
  status: "idle" | "running" | "thinking";
  resultSummary?: string;
}

export interface STTPreviewState {
  taskId: string;
  transcript: string;
  confidence: number;
  language: string;
}

export type FocusProfile = "Work" | "Gaming" | "Meeting" | "Quiet" | "Media" | "Focus" | "Standard";

// ─────────────────────────────────────────────────────────────────────────────
// Persistent Module-Level Shared Store
// Survives component unmounting (e.g. switching tabs in App.tsx) so chat messages
// and WebSocket state are never wiped out when navigating between views.
// ─────────────────────────────────────────────────────────────────────────────
let storeConnected = false;
let storeMessages: ChatMessage[] = [];
let storeActiveAgent = "Command Router";
let storeAgentActivity: AgentActivityState = {
  agentName: "Command Router",
  subtask: "",
  status: "idle",
};
let storeConfirmModal: ConfirmModalRequest | null = null;
let storeSttPreview: STTPreviewState | null = null;
let storeVoiceStatus: "idle" | "listening" | "processing" = "idle";
let storeSystemLogs: string[] = [];
let storeLastError: string | null = null;
let storeIsThinking = false;
let storeReconnectAttempt = 0;
let storeNextReconnectMs = 0;
let storeVoiceMode: "ptt" | "wakeword" | "off" = "wakeword";

const listeners = new Set<() => void>();
function notifyListeners() {
  listeners.forEach((listener) => listener());
}

function setConnectedStore(val: boolean) {
  storeConnected = val;
  notifyListeners();
}
function setMessagesStore(updater: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) {
  storeMessages = typeof updater === "function" ? updater(storeMessages) : updater;
  notifyListeners();
}
function setActiveAgentStore(val: string) {
  storeActiveAgent = val;
  notifyListeners();
}
function setAgentActivityStore(
  updater: AgentActivityState | ((prev: AgentActivityState) => AgentActivityState)
) {
  storeAgentActivity = typeof updater === "function" ? updater(storeAgentActivity) : updater;
  notifyListeners();
}
function setConfirmModalStore(val: ConfirmModalRequest | null) {
  storeConfirmModal = val;
  notifyListeners();
}
function setSttPreviewStore(val: STTPreviewState | null) {
  storeSttPreview = val;
  notifyListeners();
}
function setVoiceStatusStore(val: "idle" | "listening" | "processing") {
  storeVoiceStatus = val;
  notifyListeners();
}
function setLastErrorStore(val: string | null) {
  storeLastError = val;
  notifyListeners();
}
function setIsThinkingStore(val: boolean) {
  storeIsThinking = val;
  notifyListeners();
}
function setReconnectStore(attempt: number, nextMs: number) {
  storeReconnectAttempt = attempt;
  storeNextReconnectMs = nextMs;
  notifyListeners();
}
function setVoiceModeStore(val: "ptt" | "wakeword" | "off") {
  storeVoiceMode = val;
  notifyListeners();
}
function addLogStore(text: string) {
  const timeStr = new Date().toLocaleTimeString();
  storeSystemLogs = [...storeSystemLogs.slice(-100), `[${timeStr}] ${text}`];
  notifyListeners();
}

// Initialize singleton WebSocket listeners once per bundle load
let isWsInitialized = false;
function initSingletonWs() {
  if (isWsInitialized) return;
  isWsInitialized = true;

  wsClient.connect();

  wsClient.subscribeStatus((isConnected) => {
    setConnectedStore(isConnected);
    if (isConnected) {
      setLastErrorStore(null);
      addLogStore("[OK] Connected to Makima WebSocket (v1)");
    } else {
      addLogStore("[WARN] Disconnected from Makima WebSocket");
    }
  });

  wsClient.subscribe((msg: WSMessage) => {
    handleIncomingMessage(msg);
  });
}

function handleIncomingMessage(msg: WSMessage) {
  switch (msg.type) {
    case "ai_chunk": {
      const textChunk = (msg.payload.text as string) || (msg.payload.chunk as string) || "";
      const isFinal = Boolean(msg.payload.is_final);
      const taskId = msg.task_id || "current";
      const aiMsgId = `ai-${taskId}`;
      setIsThinkingStore(!isFinal);

      if (isFinal) {
        setAgentActivityStore((prev) => ({ ...prev, status: "idle", subtask: "" }));
        setActiveAgentStore("Command Router"); // Bug 5 fix: reset label after each completed turn
      }

      setMessagesStore((prev) => {
        const existingIdx = prev.findIndex((m) => m.id === aiMsgId);
        if (existingIdx !== -1) {
          const updated = [...prev];
          updated[existingIdx] = {
            ...updated[existingIdx],
            content: updated[existingIdx].content + textChunk,
            isStreaming: !isFinal,
          };
          return updated;
        } else {
          return [
            ...prev,
            {
              id: aiMsgId,
              role: "ai",
              content: textChunk,
              timestamp: new Date(),
              agent: storeActiveAgent,
              isStreaming: !isFinal,
            },
          ];
        }
      });
      break;
    }

    case "ai_response_done": {
      const taskId = msg.task_id || "current";
      const aiMsgId = `ai-${taskId}`;
      setIsThinkingStore(false);
      setMessagesStore((prev) =>
        prev.map((m) => (m.id === aiMsgId || m.id === taskId ? { ...m, isStreaming: false } : m))
      );
      setAgentActivityStore((prev) => ({ ...prev, status: "idle", subtask: "" }));
      addLogStore("[OK] Response completed");
      setLastErrorStore(null);
      break;
    }

    case "ai_error": {
      const error = String(msg.payload.error || "Makima backend returned an error");
      setIsThinkingStore(false);
      setLastErrorStore(error);
      setAgentActivityStore((prev) => ({ ...prev, status: "idle", subtask: "" }));
      addLogStore(`[ERROR] ${error}`);
      setMessagesStore((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: "ai",
          content: error,
          timestamp: new Date(),
          agent: "Backend",
        },
      ]);
      break;
    }

    case "thinking_status": {
      const thinkingText = (msg.payload.text as string) || "Thinking...";
      const isThinkingStatus = Boolean(msg.payload.is_thinking);
      setIsThinkingStore(isThinkingStatus);
      setAgentActivityStore((prev) => ({
        ...prev,
        subtask: isThinkingStatus ? thinkingText : "",
        status: isThinkingStatus ? "thinking" : "idle",
      }));
      break;
    }

    case "agent_started": {
      const agentName =
        (msg.payload.agent_name as string) || (msg.payload.agent as string) || "Agent";
      const subtask = (msg.payload.subtask as string) || "";
      setIsThinkingStore(true);
      setActiveAgentStore(agentName);
      setAgentActivityStore({ agentName, subtask, status: "running" });
      addLogStore(`[AGENT] ${agentName} started: ${subtask}`);
      break;
    }

    case "agent_progress": {
      const agentName =
        (msg.payload.agent_name as string) || (msg.payload.agent as string) || "Agent";
      const subtask = (msg.payload.subtask as string) || (msg.payload.message as string) || "";
      setIsThinkingStore(true);
      setActiveAgentStore(agentName);
      setAgentActivityStore({ agentName, subtask, status: "running" });
      addLogStore(`[AGENT] ${agentName}: ${subtask}`);
      break;
    }

    case "agent_done": {
      const agentName =
        (msg.payload.agent_name as string) || (msg.payload.agent as string) || "Agent";
      const resultSummary = (msg.payload.result_summary as string) || "";
      setIsThinkingStore(false);
      setAgentActivityStore({ agentName, subtask: "", status: "idle", resultSummary });
      setActiveAgentStore("Command Router"); // Bug 5 fix: clear stale agent label after task done
      addLogStore(`[AGENT] ${agentName} completed`);
      break;
    }

    case "action_confirm_request": {
      const taskId = msg.task_id || `confirm-${Date.now()}`;
      const action = String(msg.payload.action || "confirm");
      const description = String(msg.payload.description || "Approve action?");
      const riskLevel = String(msg.payload.risk_level || "medium");
      setConfirmModalStore({ taskId, action, description, riskLevel });
      addLogStore(`[CONFIRM] Action requested: ${action} (${riskLevel})`);
      break;
    }

    case "stt_transcript_preview": {
      const taskId = msg.task_id || "voice";
      const transcript = String(msg.payload.transcript || "");
      const confidence = Number(msg.payload.confidence || 0);
      const language = String(msg.payload.language || "en");
      setSttPreviewStore({ taskId, transcript, confidence, language });
      break;
    }

    case "status_listening":
      setVoiceStatusStore("listening");
      addLogStore("[VOICE] Microphones active — listening...");
      break;

    case "status_processing":
      setVoiceStatusStore("processing");
      addLogStore("[VOICE] Whisper STT processing audio...");
      break;

    case "status_idle":
      setVoiceStatusStore("idle");
      break;

    case "service_health": {
      const svc = msg.payload.service as string;
      const status = msg.payload.status as string;
      addLogStore(`[HEALTH] ${svc}: ${status}`);
      break;
    }
  }
}

export function useMakimaWebSocket() {
  const [, forceUpdate] = useState({});

  useEffect(() => {
    initSingletonWs();
    const listener = () => forceUpdate({});
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const sendUserMessage = useCallback(
    (
      text: string,
      conversationId?: string,
      attachment?: { file_name: string; file_mime: string; file_data: string }
    ) => {
      const taskId = `task-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
      const newMsg: ChatMessage = {
        id: taskId,
        role: "user",
        content: text,
        timestamp: new Date(),
      };

      setMessagesStore((prev) => [...prev, newMsg]);
      setIsThinkingStore(true);
      setAgentActivityStore((prev) => ({
        ...prev,
        subtask: "Thinking...",
        status: "thinking",
      }));
      addLogStore(`[USER] Sent message: ${text.slice(0, 30)}...`);

      const sent = wsClient.send(
        "user_message",
        { text, conversation_id: conversationId, ...(attachment || {}) },
        taskId
      );
      if (!sent) {
        setIsThinkingStore(false);
        setLastErrorStore("Makima backend is not connected. Reconnecting…");
        setMessagesStore((prev) => prev.filter((message) => message.id !== taskId));
      }
    },
    []
  );

  const approveAction = useCallback((taskId: string, action: string) => {
    wsClient.send("approve_action", { action }, taskId);
    setConfirmModalStore(null);
    addLogStore(`[CONFIRM] Approved action: ${action}`);
  }, []);

  const rejectAction = useCallback((taskId: string, action: string) => {
    wsClient.send("reject_action", { action }, taskId);
    setConfirmModalStore(null);
    addLogStore(`[CONFIRM] Rejected action: ${action}`);
  }, []);

  const confirmStt = useCallback((taskId: string, transcript: string) => {
    wsClient.send("stt_confirm", { final_transcript: transcript }, taskId);
    setSttPreviewStore(null);
  }, []);

  const cancelStt = useCallback((taskId: string) => {
    wsClient.send("stt_cancel", {}, taskId);
    setSttPreviewStore(null);
  }, []);

  const clearError = useCallback(() => setLastErrorStore(null), []);
  const clearMessages = useCallback(() => setMessagesStore([]), []);

  const loadConversation = useCallback(async (conversationId: string) => {
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/conversations/${encodeURIComponent(conversationId)}`
      );
      if (!response.ok) throw new Error(`History request failed (${response.status})`);
      const data = await response.json();
      setMessagesStore(
        (data.messages || []).map(
          (
            message: {
              conversation_id?: string;
              role: string;
              content?: string;
              message?: string;
              created_at?: number;
            },
            index: number
          ) => ({
            id: `${message.conversation_id || conversationId}-${index}`,
            role: message.role === "user" ? "user" : "ai",
            content: message.content || message.message || "",
            timestamp: new Date((message.created_at || Date.now() / 1000) * 1000),
          })
        )
      );
    } catch (error) {
      setLastErrorStore(`Failed to load history: ${String(error)}`);
    }
  }, []);

  const sendPttDown = useCallback(() => {
    wsClient.send("voice_ptt_start", {});
  }, []);

  const sendPttUp = useCallback(() => {
    wsClient.send("voice_ptt_stop", {});
  }, []);

  const setFocusProfile = useCallback((profile: FocusProfile) => {
    wsClient.send("focus_profile_update", { profile });
    addLogStore(`[PROFILE] Switched focus profile to: ${profile}`);
  }, []);

  const pullOllamaModel = useCallback((modelName: string) => {
    wsClient.send("pull_ollama_model", { model_name: modelName });
    addLogStore(`[MODEL] Pull requested for Ollama model: ${modelName}`);
  }, []);

  const deleteOllamaModel = useCallback((modelName: string) => {
    wsClient.send("delete_ollama_model", { model_name: modelName });
    addLogStore(`[MODEL] Delete requested for Ollama model: ${modelName}`);
  }, []);

  return {
    connected: storeConnected,
    messages: storeMessages,
    activeAgent: storeActiveAgent,
    agentActivity: storeAgentActivity,
    confirmModal: storeConfirmModal,
    sttPreview: storeSttPreview,
    voiceStatus: storeVoiceStatus,
    systemLogs: storeSystemLogs,
    sendUserMessage,
    approveAction,
    rejectAction,
    confirmStt,
    cancelStt,
    clearError,
    clearMessages,
    sendPttDown,
    sendPttUp,
    setFocusProfile,
    pullOllamaModel,
    deleteOllamaModel,
    loadConversation,
    lastError: storeLastError,
    isThinking: storeIsThinking,
    reconnectAttempt: storeReconnectAttempt,
    nextReconnectMs: storeNextReconnectMs,
    voiceMode: storeVoiceMode,
    setVoiceMode: setVoiceModeStore,
    setReconnectAttempt: setReconnectStore,
  };
}
