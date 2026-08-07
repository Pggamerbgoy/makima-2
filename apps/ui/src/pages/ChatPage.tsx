import React, { useState, useRef, useEffect } from "react";
import {
  Search,
  Plus,
  Mic,
  AudioLines,
  MicOff,
  Paperclip,
  Send,
  ChevronDown,
  Bot,
  User,
  Archive,
  Wifi,
  WifiOff,
  AlertTriangle,
  Trash2,
  Sun,
  Moon,
  Type,
} from "lucide-react";
import { useMakimaWebSocket, FocusProfile } from "../hooks/useMakimaWebSocket";
import FormattedMessage from "../components/FormattedMessage";
import "./ChatPage.css";

type VoiceMode = "ptt" | "wakeword" | "off";

const VOICE_ICONS: Record<VoiceMode, typeof Mic> = {
  ptt: Mic,
  wakeword: AudioLines,
  off: MicOff,
};

const VOICE_LABELS: Record<VoiceMode, string> = {
  ptt: "Push-to-Talk",
  wakeword: "Hey Makima",
  off: "Voice Off",
};

export default function ChatPage() {
  const {
    connected,
    messages,
    activeAgent,
    confirmModal,
    sttPreview,
    voiceStatus,
    sendUserMessage,
    approveAction,
    rejectAction,
    confirmStt,
    cancelStt,
    sendPttDown,
    sendPttUp,
    setFocusProfile: sendFocusProfileWS,
    lastError,
    loadConversation,
    clearMessages,
    isThinking,
    agentActivity,
  } = useMakimaWebSocket();

  const [input, setInput] = useState("");
  const [voiceMode, setVoiceMode] = useState<VoiceMode>("wakeword");
  const [focusProfile, setFocusProfile] = useState<FocusProfile>("Work");
  const [showFocusDropdown, setShowFocusDropdown] = useState(false);
  const [sidebarOpen, _setSidebarOpen] = useState(true);
  const [isPttPressed, setIsPttPressed] = useState(false);
  const [conversationId, setConversationId] = useState(() => `conversation-${Date.now()}`);
  const [conversations, setConversations] = useState<Array<{ conversation_id: string; title: string }>>([]);
  const [search, setSearch] = useState("");
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    return (localStorage.getItem("makima_theme") as "dark" | "light") || "dark";
  });
  const [fontFamily, setFontFamily] = useState<"inter" | "outfit" | "jakarta" | "jetbrains">(() => {
    return (localStorage.getItem("makima_font") as any) || "inter";
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("makima_theme", theme);
  }, [theme]);

  useEffect(() => {
    document.body.className = `font-${fontFamily}`;
    localStorage.setItem("makima_font", fontFamily);
  }, [fontFamily]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    fetch("http://127.0.0.1:8080/conversations")
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => setConversations(data.conversations || []))
      .catch(() => setConversations([]));
  }, [messages]);

  const cycleVoiceMode = () => {
    const modes: VoiceMode[] = ["ptt", "wakeword", "off"];
    const idx = modes.indexOf(voiceMode);
    setVoiceMode(modes[(idx + 1) % modes.length]);
  };

  const handleSend = () => {
    if (!input.trim()) return;
    sendUserMessage(input, conversationId);
    setInput("");
  };

  const handleFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const encoded = String(reader.result).split(",")[1] || "";
      sendUserMessage(`Attached file: ${file.name}`, conversationId, {
        file_name: file.name,
        file_mime: file.type || "application/octet-stream",
        file_data: encoded,
      });
    };
    reader.readAsDataURL(file);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFocusChange = (fp: FocusProfile) => {
    setFocusProfile(fp);
    sendFocusProfileWS(fp);
    setShowFocusDropdown(false);
  };

  const handleDeleteConversation = async (e: React.MouseEvent, cid: string) => {
    e.stopPropagation();
    try {
      await fetch(`http://127.0.0.1:8080/conversations/${cid}`, { method: "DELETE" });
      const fresh = await fetch("http://127.0.0.1:8080/conversations").then((r) => r.json());
      setConversations(fresh.conversations || []);
      if (conversationId === cid) {
        setConversationId(`conversation-${Date.now()}`);
        clearMessages();
      }
    } catch (err) {
      console.error("Failed to delete conversation:", err);
    }
  };

  const handlePttMouseDown = () => {
    if (voiceMode === "ptt") {
      setIsPttPressed(true);
      sendPttDown();
    }
  };

  const handlePttMouseUp = () => {
    if (voiceMode === "ptt" && isPttPressed) {
      setIsPttPressed(false);
      sendPttUp();
    }
  };

  const VoiceIcon = VOICE_ICONS[voiceMode];

  const filteredConversations = conversations.filter((c) => {
    const text = c.title || c.conversation_id || "";
    const q = search || "";
    return text.toLowerCase().includes(q.toLowerCase());
  });

  return (
    <div className="chat-layout">
      {/* Sidebar */}
      {sidebarOpen && (
        <aside className="chat-sidebar">
          <div className="chat-sidebar-header">
            <div className="chat-sidebar-search">
              <Search size={14} className="text-muted" />
              <input
                type="text"
                placeholder="Search history..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="chat-sidebar-search-input"
              />
            </div>
            <button
              className="btn-icon"
              title="New Chat"
              onClick={() => {
                const newId = `conversation-${Date.now()}`;
                setConversationId(newId);
              }}
            >
              <Plus size={16} />
            </button>
          </div>

          <div className="chat-sidebar-list">
            {filteredConversations.map((c) => (
              <button
                key={c.conversation_id}
                className={`chat-sidebar-item${c.conversation_id === conversationId ? " active" : ""}`}
                onClick={() => {
                  setConversationId(c.conversation_id);
                  loadConversation(c.conversation_id);
                }}
              >
                <span className="chat-sidebar-item-title">{c.title || c.conversation_id}</span>
                <button
                  className="btn-icon"
                  style={{ opacity: 0.6, width: 24, height: 24, padding: 4 }}
                  title="Delete conversation"
                  onClick={(e) => handleDeleteConversation(e, c.conversation_id)}
                >
                  <Trash2 size={14} />
                </button>
              </button>
            ))}
          </div>

          <div className="chat-sidebar-footer">
            <button className="btn-icon" title="Archives">
              <Archive size={16} />
            </button>
            <span className="text-label text-muted">{conversations.length} Conversations</span>
          </div>
        </aside>
      )}

      {/* Main Chat */}
      <main className="chat-main">
        {/* Topbar */}
        <header className="chat-topbar">
          <div className="chat-topbar-left">
            <h2 className="chat-topbar-title">Chat with Makima</h2>
            <span className="text-label text-muted">Agent: {activeAgent}</span>
          </div>

          <div className="chat-topbar-right">
            <div className="font-selector-container">
              <Type size={14} className="text-muted" />
              <select
                value={fontFamily}
                onChange={(e) => setFontFamily(e.target.value as any)}
                className="font-select-dropdown"
                title="Select Text Font"
              >
                <option value="inter">Inter (Default)</option>
                <option value="outfit">Outfit (Modern)</option>
                <option value="jakarta">Plus Jakarta (Tech)</option>
                <option value="jetbrains">JetBrains Mono (Code)</option>
              </select>
            </div>

            <button
              className="theme-toggle-btn btn-icon"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              title={`Switch to ${theme === "dark" ? "White / Light" : "Dark"} Mode`}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>

            <div className={`status-badge ${connected ? "online" : "offline"}`}>
              {connected ? <Wifi size={14} /> : <WifiOff size={14} />}
              <span>{connected ? "Connected" : "Disconnected"}</span>
            </div>
          </div>
        </header>

        {/* Messages */}
        <div className="chat-messages">
          {confirmModal && (
            <div className="overlay-confirm-card" style={{ maxWidth: 640, margin: "0 auto 16px auto" }}>
              <div className="overlay-confirm-header">
                <span className={`overlay-risk-badge overlay-risk-${(confirmModal.riskLevel || "unknown").toLowerCase()}`}>
                  <AlertTriangle size={12} style={{ marginRight: 4 }} />
                  {(confirmModal.riskLevel || "unknown").toUpperCase()} RISK
                </span>
                <span className="overlay-confirm-title">Confirmation Required</span>
              </div>
              <p className="overlay-confirm-desc">{confirmModal.description}</p>
              <div className="overlay-confirm-actions">
                <button
                  className="overlay-confirm-btn overlay-confirm-reject"
                  onClick={() => rejectAction(confirmModal.taskId, confirmModal.action)}
                >
                  Reject
                </button>
                <button
                  className="overlay-confirm-btn overlay-confirm-approve"
                  onClick={() => approveAction(confirmModal.taskId, confirmModal.action)}
                >
                  Approve Action
                </button>
              </div>
            </div>
          )}

          {sttPreview && (
            <div className="overlay-stt-preview" style={{ maxWidth: 640, margin: "0 auto 16px auto" }}>
              <div className="overlay-stt-header">
                <Mic size={13} />
                <span>Whisper Heard ({sttPreview.language}, {Math.round(sttPreview.confidence * 100)}% conf):</span>
              </div>
              <div className="overlay-stt-text">{sttPreview.transcript}</div>
              <div className="overlay-stt-actions">
                <button
                  className="overlay-stt-btn overlay-stt-cancel"
                  onClick={() => cancelStt(sttPreview.taskId)}
                >
                  Cancel
                </button>
                <button
                  className="overlay-stt-btn overlay-stt-confirm"
                  onClick={() => confirmStt(sttPreview.taskId, sttPreview.transcript)}
                >
                  Confirm & Send
                </button>
              </div>
            </div>
          )}

          {messages.length === 0 ? (
            <div className="chat-empty-state">
              <Bot size={48} className="text-muted" />
              <h3>How can I assist you today?</h3>
              <p className="text-muted">Ask anything, request actions, or try voice commands.</p>
            </div>
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                className={`chat-message ${msg.role === "user" ? "chat-message-user" : "chat-message-ai"}`}
              >
                <div className="chat-message-avatar">
                  {msg.role === "user" ? <User size={16} /> : <Bot size={16} />}
                </div>

                <div className="chat-message-content">
                  {msg.agent && <span className="chat-message-agent">{msg.agent}</span>}
                  <div className="chat-message-text">
                    <FormattedMessage content={msg.content} role={msg.role} />
                  </div>
                </div>
              </div>
            ))
          )}

          {isThinking && !messages.some((m) => m.isStreaming) && (
            <div className="chat-message chat-message-ai thinking-bubble-container">
              <div className="chat-message-avatar">
                <Bot size={16} />
              </div>
              <div className="chat-message-content">
                <span className="chat-message-agent">
                  {agentActivity.status !== "idle" && agentActivity.subtask
                    ? `${agentActivity.agentName}: ${agentActivity.subtask}`
                    : "Makima is thinking..."}
                </span>
                <div className="chat-message-text thinking-dots-box">
                  <span className="dot dot-1">•</span>
                  <span className="dot dot-2">•</span>
                  <span className="dot dot-3">•</span>
                  <span className="thinking-text-label">
                    {agentActivity.subtask || "Processing your request..."}
                  </span>
                </div>
              </div>
            </div>
          )}

          {lastError && <div className="chat-error-banner">{lastError}</div>}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="chat-input-bar">
          <div className="chat-input-container glass">
            <input
              type="file"
              ref={fileInputRef}
              style={{ display: "none" }}
              onChange={(e) => {
                if (e.target.files?.[0]) handleFile(e.target.files[0]);
              }}
            />
            <button
              className="btn-icon"
              title="Attach file"
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip size={18} />
            </button>

            {/* Voice Control Button */}
            <button
              className={`chat-voice-btn ${voiceMode !== "off" ? "active" : ""} ${
                voiceStatus === "listening" ? "listening" : ""
              }`}
              onClick={cycleVoiceMode}
              onMouseDown={handlePttMouseDown}
              onMouseUp={handlePttMouseUp}
              title={`Voice mode: ${VOICE_LABELS[voiceMode]}`}
            >
              <VoiceIcon size={16} />
              <span className="chat-voice-label">{VOICE_LABELS[voiceMode]}</span>
            </button>

            <textarea
              className="chat-input-textarea"
              placeholder="Type your message... (Shift+Enter for new line)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
            />

            {/* Focus Profile */}
            <div className="chat-focus-wrapper">
              <button
                className="chat-focus-btn"
                onClick={() => setShowFocusDropdown(!showFocusDropdown)}
              >
                {focusProfile}
                <ChevronDown size={14} />
              </button>
              {showFocusDropdown && (
                <div className="chat-focus-dropdown glass">
                  {(["Work", "Gaming", "Meeting", "Quiet"] as FocusProfile[]).map((fp) => (
                    <button
                      key={fp}
                      className={`chat-focus-option${fp === focusProfile ? " active" : ""}`}
                      onClick={() => handleFocusChange(fp)}
                    >
                      {fp}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Send Button */}
            <button
              className="chat-send-btn btn-primary"
              onClick={handleSend}
              disabled={!input.trim() || !connected}
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
