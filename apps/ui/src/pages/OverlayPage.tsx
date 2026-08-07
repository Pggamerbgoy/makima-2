import { useState, useRef, useEffect, useLayoutEffect } from "react";
import {
  Send,
  Bot,
  User,
  X,
  Pin,
  Maximize2,
  Minimize2,
  Trash2,
  AlertTriangle,
  Mic,
  MicOff,
  Monitor,
  Globe,
  Zap,
  ChevronDown,
  Radio,
} from "lucide-react";
import { getCurrentWindow, getAllWindows, LogicalSize, LogicalPosition, currentMonitor } from "@tauri-apps/api/window";
import { useMakimaWebSocket } from "../hooks/useMakimaWebSocket";
import type { ChatMessage, FocusProfile } from "../hooks/useMakimaWebSocket";
import "./OverlayPage.css";

function MessageText({ msg }: { msg: ChatMessage }) {
  return (
    <div className="overlay-message-text">
      {msg.content}
      {msg.isStreaming && <span className="overlay-streaming-cursor" aria-hidden="true" />}
    </div>
  );
}

const MAX_ISLAND_HEIGHT = 480;
const MIN_ACTIVE_HEIGHT = 160;
const WINDOW_PADDING = 22;
const COMPACT_PILL_WIDTH = 88;
const COMPACT_PILL_HEIGHT = 40;
const ACTIVE_CARD_WIDTH = 420;
const TOP_ANCHOR_Y = 30;

const PROFILES: FocusProfile[] = ["Work", "Gaming", "Coding" as FocusProfile, "Quiet", "Standard"];

export default function OverlayPage() {
  const {
    connected,
    messages,
    activeAgent,
    agentActivity,
    confirmModal,
    sttPreview,
    voiceStatus,
    sendUserMessage,
    clearError,
    clearMessages,
    sendPttDown,
    sendPttUp,
    setFocusProfile,
    lastError,
    isThinking,
  } = useMakimaWebSocket();

  const [input, setInput] = useState("");
  const [isWindowFocused, setIsWindowFocused] = useState(false);
  const [isPinned, setIsPinned] = useState(true);
  const [isManuallyMinimized, setIsManuallyMinimized] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [currentProfile, setCurrentProfile] = useState<FocusProfile>("Standard");
  const [conversationId, setConversationId] = useState(() => `overlay-${Date.now()}`);
  const [islandHeight, setIslandHeight] = useState(MIN_ACTIVE_HEIGHT);
  const [customSize, setCustomSize] = useState<{ width: number; height: number } | null>(null);
  const [isResizing, setIsResizing] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const lastWidthRef = useRef<number>(0);

  const isCompact = isManuallyMinimized;

  const expandIsland = async () => {
    setIsManuallyMinimized(false);
    if ("__TAURI_INTERNALS__" in window) {
      await getCurrentWindow().setFocus().catch(() => {});
      setIsWindowFocused(true);
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      setIsWindowFocused(true);
    }
  };

  const collapseToPill = () => {
    setIsManuallyMinimized(true);
  };

  useLayoutEffect(() => {
    if (isCompact || !shellRef.current || customSize) return;
    const el = shellRef.current;
    const measure = () => {
      if (!el) return;
      const next = Math.min(MAX_ISLAND_HEIGHT, Math.max(MIN_ACTIVE_HEIGHT, el.scrollHeight));
      setIslandHeight((prev) => (Math.abs(prev - next) > 4 ? next : prev));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [
    isCompact,
    customSize,
    messages.length,
    agentActivity.status,
    voiceStatus,
    Boolean(confirmModal),
    Boolean(sttPreview),
    isThinking,
    Boolean(lastError),
  ]);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    const targetWidth = isCompact ? COMPACT_PILL_WIDTH : customSize ? customSize.width : ACTIVE_CARD_WIDTH;
    const targetHeight = isCompact ? COMPACT_PILL_HEIGHT : customSize ? customSize.height : islandHeight;
    const winWidth = targetWidth + WINDOW_PADDING * 2;
    const winHeight = targetHeight + WINDOW_PADDING * 2;

    let cancelled = false;
    (async () => {
      try {
        const win = getCurrentWindow();
        await win.setSize(new LogicalSize(winWidth, winHeight));

        if (lastWidthRef.current !== winWidth) {
          const prevWidth = lastWidthRef.current;
          lastWidthRef.current = winWidth;
          const monitor = await currentMonitor();
          if (cancelled || !monitor) return;
          const scale = monitor.scaleFactor || 1;
          const monitorLogicalWidth = monitor.size.width / scale;

          if (prevWidth === 0) {
            const x = Math.max(0, Math.round((monitorLogicalWidth - winWidth) / 2));
            await win.setPosition(new LogicalPosition(x, TOP_ANCHOR_Y));
          } else {
            const currentPos = await win.outerPosition();
            const currentLogicalX = currentPos.x / scale;
            const currentLogicalY = currentPos.y / scale;
            const deltaX = Math.round((prevWidth - winWidth) / 2);
            const newX = Math.max(0, Math.min(monitorLogicalWidth - winWidth, currentLogicalX + deltaX));
            await win.setPosition(new LogicalPosition(newX, currentLogicalY));
          }
        }
      } catch (err) {
        console.warn("[OverlayPage] window resize failed:", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isCompact, islandHeight, customSize]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isThinking]);

  useEffect(() => {
    document.documentElement.classList.add("overlay-window");
    return () => document.documentElement.classList.remove("overlay-window");
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    if ("__TAURI_INTERNALS__" in window) {
      getCurrentWindow()
        .onFocusChanged(({ payload: focused }) => {
          setIsWindowFocused(focused);
          if (focused && !isManuallyMinimized) {
            requestAnimationFrame(() => inputRef.current?.focus());
          }
        })
        .then((fn) => {
          unlisten = fn;
        })
        .catch(() => {});
    } else {
      setIsWindowFocused(true);
    }
    return () => {
      unlisten?.();
    };
  }, [isManuallyMinimized]);

  const hideOverlay = () => {
    if ("__TAURI_INTERNALS__" in window) {
      getCurrentWindow().hide().catch(() => {});
    } else {
      window.close();
    }
  };

  const togglePin = async () => {
    const next = !isPinned;
    setIsPinned(next);
    if ("__TAURI_INTERNALS__" in window) {
      await getCurrentWindow().setAlwaysOnTop(next).catch(() => {});
    }
  };

  const openInMainWindow = async () => {
    if ("__TAURI_INTERNALS__" in window) {
      try {
        const windows = await getAllWindows();
        const mainWin = windows.find((w) => w.label === "main");
        if (mainWin) {
          await mainWin.show().catch(() => {});
          await mainWin.setFocus().catch(() => {});
          hideOverlay();
        }
      } catch (e) {
        console.error("Failed to focus main window:", e);
      }
    } else {
      window.open("/", "_blank");
    }
  };

  const handleClearChat = () => {
    clearMessages();
    setConversationId(`overlay-${Date.now()}`);
  };

  const handleSend = () => {
    if (!input.trim() || !connected) return;
    sendUserMessage(input, conversationId);
    setInput("");
  };

  const handleQuickAction = (promptText: string) => {
    if (connected) {
      sendUserMessage(promptText, conversationId);
    }
  };

  const handleToggleVoicePTT = () => {
    if (!connected) return;
    if (!isRecording) {
      sendPttDown();
      setIsRecording(true);
    } else {
      sendPttUp();
      setIsRecording(false);
    }
  };

  const handleSelectProfile = (profile: FocusProfile) => {
    setCurrentProfile(profile);
    setFocusProfile(profile);
    setShowProfileMenu(false);
  };

  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    const startX = e.clientX;
    const startY = e.clientY;
    const initialWidth = customSize ? customSize.width : ACTIVE_CARD_WIDTH;
    const initialHeight = customSize ? customSize.height : islandHeight;

    const onMouseMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startX;
      const deltaY = moveEvent.clientY - startY;
      setCustomSize({
        width: Math.max(300, Math.min(900, initialWidth + deltaX)),
        height: Math.max(180, Math.min(900, initialHeight + deltaY)),
      });
    };

    const onMouseUp = () => {
      setIsResizing(false);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  const handleResetSize = () => {
    setCustomSize(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (showProfileMenu) {
        setShowProfileMenu(false);
      } else if (input.trim()) {
        setInput("");
      } else {
        collapseToPill();
      }
    }
  };

  const recentMessages = messages.slice(-50);
  const shellWidth = isCompact ? COMPACT_PILL_WIDTH : customSize ? customSize.width : ACTIVE_CARD_WIDTH;
  const shellHeight = isCompact ? COMPACT_PILL_HEIGHT : customSize ? customSize.height : islandHeight;

  return (
    <div className="overlay-island-wrap">
      {isCompact ? (
        <button
          className="overlay-pill overlay-pill-compact"
          onClick={expandIsland}
          title="Click to expand Makima overlay"
        >
          <span className={`dot ${connected ? "dot-green" : "dot-red"}`} />
          <Bot size={15} className="overlay-pill-icon" />
          {(voiceStatus !== "idle" || agentActivity.status === "running") && (
            <span className="overlay-pill-badge" />
          )}
        </button>
      ) : (
        <div
          ref={shellRef}
          className={`overlay-shell${isWindowFocused ? " overlay-shell-focused" : ""}${
            isResizing ? " overlay-shell-resizing" : ""
          }`}
          style={{ width: shellWidth, height: shellHeight }}
        >
          {/* Titlebar */}
          <div className="overlay-titlebar" data-tauri-drag-region>
            <div className="overlay-titlebar-left" data-tauri-drag-region>
              <span className={`dot ${connected ? "dot-green" : "dot-red"}`} />
              <div className="overlay-titlebar-info" data-tauri-drag-region>
                <span className="overlay-titlebar-label" data-tauri-drag-region>
                  Makima
                </span>
                <span className="overlay-agent-badge" data-tauri-drag-region>
                  {activeAgent}
                </span>
              </div>
              <div className="overlay-profile-dropdown" data-tauri-drag-region={false}>
                <button
                  className="overlay-profile-btn"
                  onClick={() => setShowProfileMenu((prev) => !prev)}
                >
                  <span>{currentProfile}</span>
                  <ChevronDown size={11} />
                </button>
                {showProfileMenu && (
                  <div className="overlay-profile-menu">
                    {PROFILES.map((profile) => (
                      <button
                        key={profile}
                        className={`overlay-profile-item${currentProfile === profile ? " active" : ""}`}
                        onClick={() => handleSelectProfile(profile)}
                      >
                        {profile}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="overlay-titlebar-actions" data-tauri-drag-region={false}>
              <button
                className={`overlay-icon-btn${isPinned ? " active-pin" : ""}`}
                onClick={togglePin}
                title="Pin on Top"
              >
                <Pin size={13} />
              </button>
              <button className="overlay-icon-btn" onClick={openInMainWindow} title="Open in Main Window">
                <Maximize2 size={13} />
              </button>
              <button className="overlay-icon-btn" onClick={handleClearChat} title="Clear Chat">
                <Trash2 size={13} />
              </button>
              <button className="overlay-icon-btn" onClick={collapseToPill} title="Minimize to Pill">
                <Minimize2 size={13} />
              </button>
              <button className="overlay-close-btn" onClick={hideOverlay} title="Hide">
                <X size={14} />
              </button>
            </div>
          </div>

          {/* Status Pill */}
          {(voiceStatus !== "idle" || agentActivity.status !== "idle" || isRecording) && (
            <div className="overlay-status-pill">
              {isRecording && (
                <span className="overlay-status-item overlay-status-voice">
                  <Radio size={13} className="pulse-icon" /> Recording voice...
                </span>
              )}
              {!isRecording && voiceStatus === "listening" && (
                <span className="overlay-status-item overlay-status-voice">Listening...</span>
              )}
              {!isRecording && voiceStatus === "processing" && (
                <span className="overlay-status-item overlay-status-voice">Transcribing...</span>
              )}
              {voiceStatus === "idle" && !isRecording && (agentActivity.status === "running" || agentActivity.status === "thinking") && (
                <span className="overlay-status-item">
                  {agentActivity.agentName}: {agentActivity.subtask || "Executing..."}
                </span>
              )}
            </div>
          )}

          {/* Error Banner */}
          {lastError && (
            <div className="overlay-error-banner">
              <div className="overlay-error-left">
                <AlertTriangle size={13} />
                <span>{lastError}</span>
              </div>
              <button className="overlay-error-dismiss" onClick={clearError}>
                <X size={12} />
              </button>
            </div>
          )}

          {/* Chat Messages */}
          <div className="overlay-messages">
            {recentMessages.length === 0 && !isThinking && (
              <div className="overlay-empty-state">
                <Bot size={30} className="overlay-empty-icon" />
                <p>How can I help you today?</p>
                <span className="overlay-empty-sub">Type a command or use quick actions below.</span>
              </div>
            )}
            {recentMessages.map((msg) => (
              <div key={msg.id} className={`overlay-message overlay-message-${msg.role}`}>
                <div className="overlay-message-avatar">
                  {msg.role === "user" ? <User size={12} /> : <Bot size={12} />}
                </div>
                <MessageText msg={msg} />
              </div>
            ))}
            {isThinking && (
              <div className="overlay-message overlay-message-ai overlay-thinking-bubble">
                <div className="overlay-message-avatar">
                  <Bot size={12} />
                </div>
                <div className="thinking-dots-box">
                  <span className="dot dot-1" />
                  <span className="dot dot-2" />
                  <span className="dot dot-3" />
                  <span className="thinking-text-label">Makima is processing...</span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Quick Actions */}
          <div className="overlay-quick-actions" data-tauri-drag-region={false}>
            <button
              className="quick-chip"
              onClick={() => handleQuickAction("Analyze my screen and explain what is visible")}
            >
              <Monitor size={11} />
              <span>Analyze Screen</span>
            </button>
            <button className="quick-chip" onClick={() => handleQuickAction("Open browser and execute task")}>
              <Globe size={11} />
              <span>Browser Task</span>
            </button>
            <button className="quick-chip" onClick={() => handleQuickAction("Check system health and status of all agents")}>
              <Zap size={11} />
              <span>System Status</span>
            </button>
          </div>

          {/* Input Bar */}
          <div className="overlay-input-bar" data-tauri-drag-region={false}>
            <button
              className={`overlay-mic-btn${isRecording ? " recording" : ""}`}
              onClick={handleToggleVoicePTT}
              disabled={!connected}
              title="Voice PTT"
            >
              {isRecording ? <MicOff size={14} /> : <Mic size={14} />}
            </button>
            <textarea
              ref={inputRef}
              className="overlay-input overlay-textarea"
              rows={1}
              placeholder={connected ? "Ask Makima..." : "Connecting..."}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={!connected}
            />
            <button className="overlay-send-btn" onClick={handleSend} disabled={!input.trim() || !connected}>
              <Send size={14} />
            </button>
          </div>

          {/* Resize Handle */}
          <div
            className="overlay-resize-handle"
            onMouseDown={handleResizeMouseDown}
            onDoubleClick={handleResetSize}
            data-tauri-drag-region={false}
          >
            <div className="resize-grip-lines" />
          </div>
        </div>
      )}
    </div>
  );
}
