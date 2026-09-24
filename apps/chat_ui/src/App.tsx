import React, { useCallback, useState, useEffect, useRef, useMemo, Suspense, lazy } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { MessageBubble } from './components/MessageBubble';
import { ChatInput } from './components/ChatInput';
import { CanvasDrawer } from './components/CanvasDrawer';
import { QuickPromptBar } from './components/QuickPromptBar';
import type { Message, ChatSession, AppSettings, Attachment, CanvasItem, MediaLibraryEntry, LLMProvider, AgentActivityEvent, PlanMilestone } from './types/chat';
import { wsClient } from './services/wsClient';
import {
  getLLMProviders,
  deleteConversation,
  listConversations,
  getConversationMessages,
  getSettings,
  saveSettings,
} from './services/brainApi';
import { ServerMessage, LEGACY_STREAM_TYPES, normalizeMediaItems, normalizeGroundingSources } from './services/protocol';
import {
  Sparkles, Code, Video, FileText,
  PanelLeft, Settings, Sun, Moon, MessageSquare, Trash2, Search,
  Plus, Edit2, Check, X, Mic, Layers, Activity, FileDown, Pin
} from 'lucide-react';

const SettingsModal = lazy(() => import('./components/SettingsModal').then((m) => ({ default: m.SettingsModal })));
const ModelSelectorPopover = lazy(() => import('./components/ModelSelectorPopover').then((m) => ({ default: m.ModelSelectorPopover })));
const VoiceSessionController = lazy(() => import('./components/VoiceSessionController').then((m) => ({ default: m.VoiceSessionController })));
const CommandPalette = lazy(() => import('./components/CommandPalette').then((m) => ({ default: m.CommandPalette })));
const BrainTelemetryModal = lazy(() => import('./components/BrainTelemetryModal'));
const ChatExportModal = lazy(() => import('./components/ChatExportModal'));
const PinnedMessagesDrawer = lazy(() => import('./components/PinnedMessagesDrawer'));
const MediaLibraryPanel = lazy(() => import('./components/MediaLibraryPanel').then((m) => ({ default: m.MediaLibraryPanel })));

const DEFAULT_SETTINGS: AppSettings = {
  llmProvider: 'groq',
  model: 'llama-3.3-70b-versatile',
  theme: 'dark',
  wsUrl: 'ws://127.0.0.1:8080/ws',
  wakeWordEnabled: true,
  autoReadAloud: false,
  ttsSpeed: 1.0,
  followupTimeoutSeconds: 20,
  silenceTimeoutMs: 850,
  maxUtteranceSeconds: 30,
  voiceLanguage: 'auto',
  persona: 'general',
  systemPrompt: '',
  privacyMode: false,
  connectors: {
    browser: true,
    system: true,
    media: true,
    messaging: true,
    automation: true,
    memory: true,
    security: true,
  },
};

const WELCOME_PHRASES = [
  'code likho, bugs bhagao.',
  'system sambhalo, ek command me.',
  'web chhano, sources ke saath.',
  'report likho, canvas me kholo.',
  'gaane chalao, mood banao.',
  'kuch bhi puchho — sharmao mat.',
];

const WelcomeTypewriter: React.FC = () => {
  const [phraseIdx, setPhraseIdx] = useState(() => Math.floor(Math.random() * WELCOME_PHRASES.length));
  const [text, setText] = useState('');
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setText(WELCOME_PHRASES[0]);
      return;
    }
    const current = WELCOME_PHRASES[phraseIdx];
    let timeout: ReturnType<typeof setTimeout>;
    if (!deleting && text === current) {
      timeout = setTimeout(() => setDeleting(true), 1800);
    } else if (deleting && text === '') {
      setDeleting(false);
      setPhraseIdx((i) => (i + 1) % WELCOME_PHRASES.length);
    } else {
      const next = deleting ? current.slice(0, text.length - 1) : current.slice(0, text.length + 1);
      timeout = setTimeout(() => setText(next), deleting ? 26 : 52);
    }
    return () => clearTimeout(timeout);
  }, [text, deleting, phraseIdx]);
  return (
    <div className="welcome-typewriter welcome-rise" style={{ animationDelay: '150ms' }} aria-hidden="true">
      <span>› {text}</span>
      <span className="type-caret" />
    </div>
  );
};

export const App: React.FC = () => {
  // Real user settings loaded from localStorage
  const [settings, setSettings] = useState<AppSettings>(() => {
    const saved = localStorage.getItem('makima_settings');
    if (!saved) return DEFAULT_SETTINGS;
    try {
      const parsed = JSON.parse(saved) as Partial<AppSettings>;
      return {
        ...DEFAULT_SETTINGS,
        ...parsed,
        connectors: { ...DEFAULT_SETTINGS.connectors, ...(parsed.connectors || {}) },
      };
    } catch (error) {
      console.error('Failed to parse settings:', error);
      return DEFAULT_SETTINGS;
    }
  });

  // Real user sessions loaded from localStorage
  const [sessions, setSessions] = useState<ChatSession[]>(() => {
    const saved = localStorage.getItem('makima_sessions');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          return parsed;
        }
      } catch (e) {
        console.error('Failed to parse sessions:', e);
      }
    }
    const defaultSession: ChatSession = {
      id: 'session_' + Math.random().toString(36).substring(2, 9),
      title: 'New Conversation',
      createdAt: Date.now(),
      messages: [],
    };
    return [defaultSession];
  });

  const [currentSessionId, setCurrentSessionId] = useState<string>(() => sessions[0]?.id || 'default');
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(true);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [settingsTab, setSettingsTab] = useState<'models' | 'general' | 'connectors' | 'voice' | 'developer' | 'tools'>('models');
  const [modelPopoverOpen, setModelPopoverOpen] = useState<boolean>(false);
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [providersLoaded, setProvidersLoaded] = useState(false);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [activeCanvasItem, setActiveCanvasItem] = useState<CanvasItem | null>(null);

  useEffect(() => {
    const onReplace = (e: Event) => {
      const detail = (e as CustomEvent<CanvasItem>).detail;
      if (detail) setActiveCanvasItem(detail);
    };
    window.addEventListener('makima:canvas-replace', onReplace);
    return () => window.removeEventListener('makima:canvas-replace', onReplace);
  }, []);
  const [voicePanelOpen, setVoicePanelOpen] = useState<boolean>(false);
  const [libraryOpen, setLibraryOpen] = useState<boolean>(false);
  const [libraryItem, setLibraryItem] = useState<MediaLibraryEntry | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  useEffect(() => {
    document.title = activeTaskId ? '✎ Makima likh rahi hai…' : 'Makima — Autonomous AI Assistant';
  }, [activeTaskId]);
  const [sessionSearch, setSessionSearch] = useState<string>('');
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingSessionTitle, setEditingSessionTitle] = useState<string>('');
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState<boolean>(false);
  const [telemetryOpen, setTelemetryOpen] = useState<boolean>(false);
  const [exportOpen, setExportOpen] = useState<boolean>(false);
  const [pinnedDrawerOpen, setPinnedDrawerOpen] = useState<boolean>(false);
  const [telemetryLatency, setTelemetryLatency] = useState<number | null>(null);

  useEffect(() => {
    document.title = activeTaskId ? '✎ Makima likh rahi hai…' : 'Makima — Autonomous AI Assistant';
  }, [activeTaskId]);

  const [toasts, setToasts] = useState<{ id: string; message: string; level: 'info' | 'success' | 'warning' | 'error'; durationMs: number }[]>([]);

  const addToast = useCallback((
    message: string,
    level: 'info' | 'success' | 'warning' | 'error' = 'info',
    durationMs: number = 4000,
  ) => {
    const id = 'toast_' + Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, message, level, durationMs }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, durationMs);
  }, []);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const incomingHandlerRef = useRef<(data: any) => void>(() => undefined);
  const streamingWatchdogTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const clearWatchdog = useCallback((taskId?: string | null) => {
    if (!taskId) return;
    const timer = streamingWatchdogTimersRef.current.get(taskId);
    if (timer) {
      clearTimeout(timer);
      streamingWatchdogTimersRef.current.delete(taskId);
    }
  }, []);

  const resetWatchdog = useCallback((taskId?: string | null) => {
    if (!taskId) return;
    clearWatchdog(taskId);
    const timer = setTimeout(() => {
      streamingWatchdogTimersRef.current.delete(taskId);
      setSessions((prev) =>
        prev.map((session) => ({
          ...session,
          messages: session.messages.map((m) =>
            m.taskId === taskId && m.isStreaming
              ? {
                  ...m,
                  isStreaming: false,
                  status: 'error',
                  text: m.text
                    ? `${m.text}\n\n[The request timed out. Please try again.]`
                    : 'The request timed out. Please try again.',
                  error: { code: 'STREAM_TIMEOUT', message: 'The request timed out. Please try again.', retryable: true },
                }
              : m
          ),
        }))
      );
      setActiveTaskId((current) => (current === taskId ? null : current));
    }, 180000);
    streamingWatchdogTimersRef.current.set(taskId, timer);
  }, [clearWatchdog]);

  // Persist real settings
  useEffect(() => {
    localStorage.setItem('makima_settings', JSON.stringify(settings));
    document.documentElement.setAttribute('data-theme', settings.theme);
  }, [settings]);

  // Persist real sessions (debounced — avoid full JSON.stringify on every stream chunk)
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const currentSessionIdRef = useRef(currentSessionId);
  currentSessionIdRef.current = currentSessionId;
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  useEffect(() => {
    const timer = setTimeout(() => {
      localStorage.setItem('makima_sessions', JSON.stringify(sessionsRef.current));
    }, 500);
    return () => clearTimeout(timer);
  }, [sessions]);
  useEffect(() => {
    const flush = () => {
      localStorage.setItem('makima_sessions', JSON.stringify(sessionsRef.current));
    };
    window.addEventListener('beforeunload', flush);
    return () => window.removeEventListener('beforeunload', flush);
  }, []);

  // Connect WebSocket to real brain backend
  useEffect(() => {
    wsClient.configure(settings.wsUrl);
  }, [settings.wsUrl]);

  // Load real providers from Makima Brain
  useEffect(() => {
    let mounted = true;
    getLLMProviders(settings.wsUrl)
      .then((list) => {
        if (mounted && list && list.length > 0) {
          setProviders(list);
        }
      })
      .catch((err) => {
        console.warn('[App] Could not load LLM providers on startup:', err);
      })
      .finally(() => {
        if (mounted) setProvidersLoaded(true);
      });
    return () => {
      mounted = false;
    };
  }, [settings.wsUrl]);

  // Hydrate settings from the brain on first load — localStorage wins on conflicts
  // (only fields still at their defaults are filled from the backend snapshot).
  const settingsHydratedRef = useRef(false);
  useEffect(() => {
    if (settingsHydratedRef.current) return;
    settingsHydratedRef.current = true;
    let cancelled = false;
    getSettings(settings.wsUrl)
      .then((snap) => {
        if (cancelled) return;
        setSettings((prev) => {
          const general = (snap.general || {}) as Record<string, unknown>;
          const fills: Partial<AppSettings> = {};
          const map: Array<[string, keyof AppSettings]> = [
            ['persona', 'persona'],
            ['system_prompt', 'systemPrompt'],
            ['privacy_mode', 'privacyMode'],
            ['auto_read_aloud', 'autoReadAloud'],
            ['tts_speed', 'ttsSpeed'],
            ['voice_language', 'voiceLanguage'],
            ['followup_timeout_seconds', 'followupTimeoutSeconds'],
            ['silence_timeout_ms', 'silenceTimeoutMs'],
            ['max_utterance_seconds', 'maxUtteranceSeconds'],
            ['llm_provider', 'llmProvider'],
            ['model', 'model'],
          ];
          for (const [key, field] of map) {
            const value = general[key];
            if (value !== undefined && value !== null && prev[field] === DEFAULT_SETTINGS[field]) {
              (fills as Record<string, unknown>)[field] = value;
            }
          }
          // Hydrate connectors object from brain (object merge, not scalar fill)
          if (general.connectors && typeof general.connectors === 'object') {
            fills.connectors = { ...DEFAULT_SETTINGS.connectors, ...(general.connectors as Record<string, boolean>) };
          }
          const wake = snap.voice?.wake_word_enabled;
          if (wake !== undefined && prev.wakeWordEnabled === DEFAULT_SETTINGS.wakeWordEnabled) {
            fills.wakeWordEnabled = Boolean(wake);
          }
          return Object.keys(fills).length > 0 ? { ...prev, ...fills } : prev;
        });
      })
      .catch(() => { /* brain offline — localStorage settings remain */ });
    return () => {
      cancelled = true;
    };
  }, [settings.wsUrl]);

  // Push whitelisted settings to the brain whenever they change (never theme/wsUrl/apiKeys).
  const whitelistedSettings = useMemo(() => ({
    wake_word_enabled: settings.wakeWordEnabled,
    persona: settings.persona,
    system_prompt: settings.systemPrompt ?? '',
    privacy_mode: settings.privacyMode ?? false,
    auto_read_aloud: settings.autoReadAloud,
    tts_speed: settings.ttsSpeed,
    voice_language: settings.voiceLanguage,
    followup_timeout_seconds: settings.followupTimeoutSeconds,
    silence_timeout_ms: settings.silenceTimeoutMs,
    max_utterance_seconds: settings.maxUtteranceSeconds,
    connectors: settings.connectors,
  }), [
    settings.wakeWordEnabled,
    settings.persona,
    settings.systemPrompt,
    settings.privacyMode,
    settings.autoReadAloud,
    settings.ttsSpeed,
    settings.voiceLanguage,
    settings.followupTimeoutSeconds,
    settings.silenceTimeoutMs,
    settings.maxUtteranceSeconds,
    settings.connectors,
  ]);
  const lastPostedSettingsRef = useRef<string | null>(null);
  useEffect(() => {
    const serialized = JSON.stringify(whitelistedSettings);
    if (lastPostedSettingsRef.current === null) {
      lastPostedSettingsRef.current = serialized;
      return;
    }
    if (lastPostedSettingsRef.current === serialized) return;
    lastPostedSettingsRef.current = serialized;
    saveSettings(whitelistedSettings, settings.wsUrl).catch((err) => {
      console.warn('[App] Settings sync to brain failed:', err);
    });
  }, [whitelistedSettings, settings.wsUrl]);

  // Sync conversation history from EternalMemory on first load (id = conversation_id).
  const historySyncedRef = useRef(false);
  useEffect(() => {
    if (historySyncedRef.current) return;
    historySyncedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const convs = await listConversations(settings.wsUrl);
        if (cancelled || !convs.length) return;
        const knownIds = new Set(sessions.map((s) => s.id));
        const missing = convs.filter((c) => c.conversation_id && !knownIds.has(c.conversation_id));
        for (const conv of missing.slice(0, 20)) {
          try {
            const turns = await getConversationMessages(conv.conversation_id, settings.wsUrl);
            if (cancelled || !turns.length) continue;
            const messages: Message[] = turns.map((t) => ({
              id: `hist_${t.conversation_id}_${t.id}`,
              sender: t.role === 'user' ? 'user' : 'ai',
              text: t.message || '',
              timestamp: Math.round((t.created_at || 0) * 1000),
              status: 'complete',
            }));
            const firstUser = turns.find((t) => t.role === 'user' && (t.message || '').trim().length > 3);
            const derived = (firstUser?.message || '').slice(0, 48).trim();
            const restored: ChatSession = {
              id: conv.conversation_id,
              title: derived || 'Restored conversation',
              createdAt: Math.round((conv.started || 0) * 1000),
              lastActiveAt: Math.round((conv.last_active || 0) * 1000),
              messages,
            };
            setSessions((prev) => (prev.some((s) => s.id === restored.id) ? prev : [...prev, restored]));
          } catch (turnErr) {
            console.warn('[App] Could not restore conversation', conv.conversation_id, turnErr);
          }
        }
      } catch (err) {
        console.warn('[App] History sync failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
    // sessions intentionally omitted: run once against the mount-time snapshot
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.wsUrl]);

  const handleSelectModel = useCallback((providerId: string, model: string) => {
    setSettings((prev) => {
      const next = { ...prev, llmProvider: providerId, model };
      localStorage.setItem('makima_settings', JSON.stringify(next));
      return next;
    });
    addToast(`Switched to ${model} (${providerId.toUpperCase()})`, 'info');
  }, [addToast]);

  const handleSaveClientApiKey = useCallback((providerId: string, apiKey: string) => {
    setSettings((prev) => {
      const apiKeys = { ...(prev.apiKeys || {}) };
      if (apiKey.trim()) {
        apiKeys[providerId] = apiKey.trim();
      } else {
        delete apiKeys[providerId];
      }
      const next = { ...prev, apiKeys };
      localStorage.setItem('makima_settings', JSON.stringify(next));
      return next;
    });
    addToast(`Saved API key for ${providerId}`, 'success');
  }, [addToast]);

  // WebSocket Subscription — intentionally has NO currentSessionId dependency.
  // Changing sessions must not re-subscribe (which would kill in-flight watchdog timers).
  // Message routing to the correct session is handled by incomingHandlerRef.
  useEffect(() => {
    wsClient.connect();

    const unsubStatus = wsClient.onStatusChange((status) => {
      setIsConnected(status);
    });

    const unsubMsg = wsClient.onMessage((data) => incomingHandlerRef.current(data));
    const timers = streamingWatchdogTimersRef.current;

    return () => {
      unsubStatus();
      unsubMsg();
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // [] — must not depend on currentSessionId, see comment above

  const scrollRafRef = useRef<number | null>(null);
  const scrollToBottom = useCallback((force: boolean = false) => {
    if (scrollRafRef.current !== null) cancelAnimationFrame(scrollRafRef.current);
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      if (!scrollContainerRef.current) {
        messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
        return;
      }
      const { scrollHeight, scrollTop, clientHeight } = scrollContainerRef.current;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 180;
      if (force || isNearBottom) {
        messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
      }
    });
  }, []);

  const currentSession = sessions.find((s) => s.id === currentSessionId) || sessions[0];

  const pinnedMessages = useMemo(() => {
    return (currentSession?.messages || []).filter((m) => m.isPinned);
  }, [currentSession]);

  useEffect(() => {
    if (!isConnected) {
      setTelemetryLatency(null);
      return;
    }
    let active = true;
    wsClient.ping().then((rtt) => {
      if (active) setTelemetryLatency(rtt);
    }).catch(() => {});

    const interval = setInterval(() => {
      if (isConnected) {
        wsClient.ping().then((rtt) => {
          if (active) setTelemetryLatency(rtt);
        }).catch(() => {});
      }
    }, 30000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [isConnected]);

  const handleTogglePin = useCallback((messageId: string) => {
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== currentSessionId) return session;
        return {
          ...session,
          messages: session.messages.map((m) =>
            m.id === messageId ? { ...m, isPinned: !m.isPinned } : m
          ),
        };
      })
    );
    addToast('Message pin updated', 'info');
  }, [currentSessionId, addToast]);

  const handleOpenCanvas = useCallback((canvasItem: CanvasItem) => {
    setActiveCanvasItem(canvasItem);
  }, []);

  const handleStopTask = useCallback((taskId?: string) => {
    if (taskId) wsClient.stopTask(taskId);
  }, []);

  const handleApproveAction = useCallback((taskId: string, action: string) => {
    wsClient.approveAction(taskId, action);
    addToast('Action approved and executing', 'success');
  }, [addToast]);

  const handleRejectAction = useCallback((taskId: string, action: string) => {
    wsClient.rejectAction(taskId, action);
    addToast('Action declined', 'info');
  }, [addToast]);

  const handleJumpToMessage = useCallback((messageId: string) => {
    const el = document.getElementById(`msg-${messageId}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('highlight-jump');
      setTimeout(() => el.classList.remove('highlight-jump'), 2200);
    }
  }, []);

  // Incoming WebSocket Message Processing (100% Real Live Events)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleIncomingWSMessage = useCallback((data: any) => {
    if (!data) return;

    const { type, task_id, payload } = data;

    if (type === ServerMessage.AI_CHUNK || LEGACY_STREAM_TYPES.includes(type)) {
      const chunk = typeof payload?.text === 'string' ? payload.text : '';
      const isFinal = Boolean(payload?.is_final);
      resetWatchdog(task_id);

      const mediaItems = isFinal ? normalizeMediaItems(payload?.media, settings.wsUrl) : undefined;
      const sourceItems = isFinal ? normalizeGroundingSources(payload?.sources) : undefined;
      const agentName = payload?.agent_name || payload?.agent || undefined;
      const format = payload?.format || undefined;

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;

          const existingMsgIndex = session.messages.findIndex((m) => m.taskId === task_id);

          if (existingMsgIndex >= 0) {
            const updated = [...session.messages];
            const currentMsg = updated[existingMsgIndex];
            const nextText = isFinal ? (chunk || currentMsg.text) : currentMsg.text + chunk;
            updated[existingMsgIndex] = {
              ...currentMsg,
              // Non-final chunks are deltas (append); finals may carry empty text (media/sources only).
              text: nextText,
              isStreaming: !isFinal,
              status: isFinal ? 'complete' : 'streaming',
              timestamp: Date.now(),
              ...(isFinal && mediaItems ? { media: mediaItems } : {}),
              ...(isFinal && sourceItems ? { sources: sourceItems } : {}),
              ...(agentName ? { agent_name: agentName } : {}),
              ...(format ? { format } : {}),
            };
            // Auto Read Aloud: synthesize on final AI message when enabled
            if (isFinal && nextText && settings.autoReadAloud) {
              const cleanForTts = nextText
                .replace(/```[\s\S]*?```/g, '')
                .replace(/`([^`]+)`/g, '$1')
                .replace(/\*\*([^*]+)\*\*/g, '$1')
                .replace(/\*([^*]+)\*/g, '$1')
                .replace(/#+\s/g, '')
                .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
                .replace(/^[-*>]\s/gm, '')
                .trim();
              if (cleanForTts) {
                wsClient.speakText(cleanForTts, task_id);
              }
            }
            return { ...session, messages: updated };
          }

          // Don't materialize an empty message from a bare final chunk.
          if (!chunk && isFinal) return session;

          const newMsg: Message = {
            id: 'msg_' + Math.random().toString(36).substring(2, 9),
            sender: 'ai',
            text: chunk,
            timestamp: Date.now(),
            taskId: task_id,
            isStreaming: !isFinal,
            status: isFinal ? 'complete' : 'streaming',
            ...(isFinal && mediaItems ? { media: mediaItems } : {}),
            ...(isFinal && sourceItems ? { sources: sourceItems } : {}),
            ...(agentName ? { agent_name: agentName } : {}),
            ...(format ? { format } : {}),
          };
          if (isFinal && chunk && settings.autoReadAloud) {
            const cleanForTts = chunk
              .replace(/```[\s\S]*?```/g, '')
              .replace(/`([^`]+)`/g, '$1')
              .replace(/\*\*([^*]+)\*\*/g, '$1')
              .replace(/\*([^*]+)\*/g, '$1')
              .replace(/#+\s/g, '')
              .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
              .replace(/^[-*>]\s/gm, '')
              .trim();
            if (cleanForTts) {
              wsClient.speakText(cleanForTts, task_id);
            }
          }
          return { ...session, messages: [...session.messages, newMsg] };
        })
      );

      if (isFinal) {
        clearWatchdog(task_id);
        setActiveTaskId((current) => (current === task_id ? null : current));
      }
      scrollToBottom(isFinal);
    } else if (type === 'tool_call_started') {
      resetWatchdog(task_id);
      const callId = payload?.call_id || 'call_' + Date.now() + Math.random().toString(36).substring(2, 5);
      const toolName = payload?.tool_name || payload?.tool || 'Tool';
      const activity: AgentActivityEvent = {
        id: callId,
        type: 'tool',
        agent: payload?.agent || 'Makima',
        status: 'running',
        message: `Executing ${toolName}...`,
        timestamp: Date.now(),
      };

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            const msg = updated[msgIdx];
            const currentActivities = msg.agentActivity || [];
            const existingIdx = currentActivities.findIndex((a) => a.id === callId);
            const newActivities = existingIdx >= 0
              ? currentActivities.map((a, i) => (i === existingIdx ? activity : a))
              : [...currentActivities, activity];
            updated[msgIdx] = { ...msg, agentActivity: newActivities };
            return { ...session, messages: updated };
          } else {
            const newMsg: Message = {
              id: 'msg_' + Math.random().toString(36).substring(2, 9),
              sender: 'ai',
              text: '',
              timestamp: Date.now(),
              taskId: task_id,
              isStreaming: true,
              status: 'streaming',
              agentActivity: [activity],
            };
            return { ...session, messages: [...session.messages, newMsg] };
          }
        })
      );
      scrollToBottom();
    } else if (type === 'tool_call_progress') {
      resetWatchdog(task_id);
      const callId = payload?.call_id || '';
      const toolName = payload?.tool_name || payload?.tool || 'Tool';
      const progress = typeof payload?.progress === 'number' ? payload.progress : null;
      const progressMsg = payload?.message || (progress !== null ? `${toolName} (${progress}%)` : null);

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            const msg = updated[msgIdx];
            const currentActivities = msg.agentActivity || [];
            let matched = false;
            const newActivities = currentActivities.map((act) => {
              if ((callId && act.id === callId) || (!matched && act.status === 'running' && act.message?.includes(toolName))) {
                matched = true;
                return {
                  ...act,
                  progress: progress !== null ? progress : act.progress,
                  message: progressMsg || act.message,
                };
              }
              return act;
            });
            updated[msgIdx] = { ...msg, agentActivity: newActivities };
            return { ...session, messages: updated };
          }
          return session;
        })
      );
    } else if (type === 'tool_call_finished') {
      resetWatchdog(task_id);
      const callId = payload?.call_id || '';
      const toolName = payload?.tool_name || payload?.tool || 'Tool';
      const isSuccess = payload?.is_success !== false;
      const duration = typeof payload?.duration_ms === 'number' ? Math.round(payload.duration_ms) : null;
      const durationStr = duration !== null ? ` (${duration}ms)` : '';
      const status = isSuccess ? 'done' : 'error';
      const statusMsg = isSuccess
        ? `${toolName} finished${durationStr}`
        : `${toolName} failed${durationStr} — attempting fallback`;

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            const msg = updated[msgIdx];
            const currentActivities = msg.agentActivity || [];
            let matched = false;
            const newActivities = currentActivities.map((act) => {
              if ((callId && act.id === callId) || (!matched && act.status === 'running' && act.message?.includes(toolName))) {
                matched = true;
                return {
                  ...act,
                  status,
                  message: statusMsg,
                };
              }
              return act;
            });
            if (!matched) {
              newActivities.push({
                id: callId || 'act_' + Date.now(),
                type: isSuccess ? 'tool' : 'tool_error',
                agent: payload?.agent || 'Makima',
                status,
                message: statusMsg,
                timestamp: Date.now(),
              });
            }
            updated[msgIdx] = { ...msg, agentActivity: newActivities };
            return { ...session, messages: updated };
          }
          return session;
        })
      );
      scrollToBottom();
    } else if (type === 'plan_milestones') {
      resetWatchdog(task_id);
      const steps: PlanMilestone[] = Array.isArray(payload?.steps) ? payload.steps : [];
      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            updated[msgIdx] = { ...updated[msgIdx], planMilestones: steps };
            return { ...session, messages: updated };
          } else {
            const newMsg: Message = {
              id: 'msg_' + Math.random().toString(36).substring(2, 9),
              sender: 'ai',
              text: '',
              timestamp: Date.now(),
              taskId: task_id,
              isStreaming: true,
              status: 'streaming',
              planMilestones: steps,
            };
            return { ...session, messages: [...session.messages, newMsg] };
          }
        })
      );
      scrollToBottom();
    } else if (type === 'plan_step_update') {
      resetWatchdog(task_id);
      const stepId = payload?.step_id;
      const stepStatus = payload?.status || 'completed';
      const summary = payload?.result_summary || '';

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            const msg = updated[msgIdx];
            const currentSteps = msg.planMilestones || [];
            const newSteps = currentSteps.map((s) => {
              if (s.id === stepId) {
                return {
                  ...s,
                  status: stepStatus as any,
                  result_summary: summary || s.result_summary,
                };
              }
              return s;
            });
            updated[msgIdx] = { ...msg, planMilestones: newSteps };
            return { ...session, messages: updated };
          }
          return session;
        })
      );
    } else if (type === ServerMessage.TOOL_ACTIVITY || type === ServerMessage.AGENT_ACTIVITY || type === ServerMessage.TOOL_COMPLETED) {
      // tool_completed may arrive without a payload wrapper (flat WSMessage-like dict).
      const tc = payload ?? data;
      resetWatchdog(task_id);
      const tcCallId = tc?.call_id;
      const activity: AgentActivityEvent = {
        // Match tool_call_started's id (callId) so completion can update it in place.
        id: tcCallId || 'act_' + Date.now() + Math.random().toString(36).substring(2, 5),
        type: 'tool',
        agent: tc?.agent || tc?.agent_name || 'System',
        status: tc?.status || 'done',
        message: tc?.action || tc?.tool_name || tc?.tool || tc?.summary || tc?.result || 'Executing action',
        timestamp: Date.now(),
      };

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            const msg = updated[msgIdx];
            const currentActivities = msg.agentActivity || [];
            const existingIdx = tcCallId
              ? currentActivities.findIndex((a) => a.id === tcCallId)
              : -1;
            if (existingIdx >= 0) {
              // Already tracked (e.g. tool_call_finished ran first) — update only
              // if still running; never append a duplicate row.
              if (currentActivities[existingIdx].status === 'running') {
                const next = [...currentActivities];
                next[existingIdx] = { ...next[existingIdx], status: activity.status, message: activity.message, timestamp: activity.timestamp };
                updated[msgIdx] = { ...msg, agentActivity: next };
                return { ...session, messages: updated };
              }
              return session;
            }
            updated[msgIdx] = {
              ...msg,
              agentActivity: [...currentActivities, activity],
            };
            return { ...session, messages: updated };
          }
          return session;
        })
      );
    } else if (type === 'canvas_item') {
      const canvasItem: CanvasItem = {
        id: payload?.id || 'canvas_' + Date.now(),
        title: payload?.title || 'Generated Artifact',
        language: payload?.language || 'text',
        content: payload?.content || '',
      };
      setActiveCanvasItem(canvasItem);
    } else if (type === ServerMessage.ACTION_CONFIRM_REQUEST || type === 'action_confirmation') {
      const confirmItem = {
        action: payload?.action || 'Execute Action',
        description: payload?.description || 'Agent requires user approval to proceed.',
        riskLevel: payload?.risk_level || payload?.severity || 'medium',
        status: 'pending' as const,
      };

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const msgIdx = session.messages.findIndex((m) => m.taskId === task_id);
          if (msgIdx >= 0) {
            const updated = [...session.messages];
            const msg = updated[msgIdx];
            updated[msgIdx] = {
              ...msg,
              actionConfirmation: confirmItem,
            };
            return { ...session, messages: updated };
          } else {
            const newMsg: Message = {
              id: 'msg_' + Math.random().toString(36).substring(2, 9),
              sender: 'ai',
              text: '',
              timestamp: Date.now(),
              taskId: task_id,
              isStreaming: true,
              status: 'streaming',
              actionConfirmation: confirmItem,
            };
            return { ...session, messages: [...session.messages, newMsg] };
          }
        })
      );
    } else if (type === ServerMessage.AI_ERROR || type === 'error') {
      clearWatchdog(task_id);
      setActiveTaskId(null);
      const errMsg = payload?.error || payload?.message || 'An error occurred during execution.';
      const errCode = payload?.code || 'EXEC_ERROR';

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          const existingMsgIndex = session.messages.findIndex((m) => m.taskId === task_id);
          if (existingMsgIndex >= 0) {
            const updated = [...session.messages];
            updated[existingMsgIndex] = {
              ...updated[existingMsgIndex],
              isStreaming: false,
              status: 'error',
              error: { code: errCode, message: errMsg },
            };
            return { ...session, messages: updated };
          } else {
            const newMsg: Message = {
              id: 'msg_' + Math.random().toString(36).substring(2, 9),
              sender: 'ai',
              text: `⚠️ **Error**: ${errMsg}`,
              timestamp: Date.now(),
              taskId: task_id,
              isStreaming: false,
              status: 'error',
              error: { code: errCode, message: errMsg },
            };
            return { ...session, messages: [...session.messages, newMsg] };
          }
        })
      );
      addToast(errMsg, 'error');
    } else if (type === ServerMessage.TOAST_NOTIFICATION) {
      const level = payload?.level;
      const toastLevel =
        level === 'success' || level === 'warning' || level === 'error' ? level : 'info';
      const durationMs = typeof payload?.duration_ms === 'number' && payload.duration_ms > 0
        ? payload.duration_ms
        : 4000;
      if (payload?.message) addToast(String(payload.message), toastLevel, durationMs);
    } else if (type === ServerMessage.AGENT_STARTED) {
      resetWatchdog(task_id);
    } else if (type === ServerMessage.AGENT_DONE) {
      resetWatchdog(task_id);
    } else if (type === ServerMessage.CREDENTIALS_DATA) {
      if (payload?.status === 'saved' && payload?.service) {
        addToast(`Credentials saved for ${payload.service}`, 'success');
      }
    } else if (type === ServerMessage.AUTONOMY_MODE_CHANGED) {
      if (payload?.mode) addToast(`Autonomy mode: ${payload.mode}`, 'info');
    }
  }, [currentSessionId, resetWatchdog, clearWatchdog, addToast, settings.wsUrl, settings.autoReadAloud, scrollToBottom]);

  useEffect(() => {
    incomingHandlerRef.current = handleIncomingWSMessage;
  });

  // Handle Sending Real Messages
  const handleSendMessage = useCallback((text: string, attachments: Attachment[] = []) => {
    if ((!text.trim() && !attachments.length) || !isConnected) return;

    const userMessage: Message = {
      id: 'msg_' + Math.random().toString(36).substring(2, 9),
      sender: 'user',
      text: text.trim(),
      timestamp: Date.now(),
      attachments,
    };

    const targetSessionId = currentSessionIdRef.current;
    const cfg = settingsRef.current;
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id === targetSessionId) {
          const isFirstMessage = session.messages.length === 0;
          // Auto-title from first meaningful message: skip very short filler words
          const rawTitle = text.slice(0, 48).trim();
          const title = isFirstMessage
            ? (rawTitle.length > 3 ? rawTitle : 'Conversation')
            : session.title;
          return {
            ...session,
            title,
            lastActiveAt: Date.now(), // Update so resumed sessions show in 'Today'
            messages: [...session.messages, userMessage],
          };
        }
        return session;
      })
    );

    const taskId = wsClient.sendMessage(text.trim(), targetSessionId, attachments, {
      provider: cfg.llmProvider,
      model: cfg.model,
      apiKey: cfg.apiKeys?.[cfg.llmProvider] || '',
    });

    setActiveTaskId(taskId);
    resetWatchdog(taskId);
    scrollToBottom(true);
  }, [isConnected, resetWatchdog, scrollToBottom]);

  const handleSuggestionClick = (promptText: string) => {
    handleSendMessage(promptText);
  };

  const handleNewChat = () => {
    const newSession: ChatSession = {
      id: 'session_' + Math.random().toString(36).substring(2, 9),
      title: 'New Conversation',
      createdAt: Date.now(),
      messages: [],
    };
    setSessions((prev) => [newSession, ...prev]);
    setCurrentSessionId(newSession.id);
    setActiveTaskId(null);
  };

  const handleDeleteSession = (sessionId: string) => {
    // First click sets pending state — second click confirms
    if (pendingDeleteId !== sessionId) {
      setPendingDeleteId(sessionId);
      // Auto-cancel confirmation after 4 seconds
      setTimeout(() => setPendingDeleteId((cur) => cur === sessionId ? null : cur), 4000);
      return;
    }
    setPendingDeleteId(null);

    // Synchronously/asynchronously notify Makima Brain backend to clean up SQLite memory & router context
    deleteConversation(sessionId, settings.wsUrl).catch((err) => {
      console.warn('[Session] Backend deletion failed or brain offline:', err);
    });

    if (sessions.length <= 1) {
      handleNewChat();
      return;
    }
    const filtered = sessions.filter((s) => s.id !== sessionId);
    setSessions(filtered);
    if (currentSessionId === sessionId) {
      setCurrentSessionId(filtered[0].id);
    }
  };

  const handleStartRename = (session: ChatSession, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(session.id);
    setEditingSessionTitle(session.title || 'New Conversation');
  };

  const handleSaveRename = (sessionId: string) => {
    if (editingSessionTitle.trim()) {
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, title: editingSessionTitle.trim() } : s))
      );
    }
    setEditingSessionId(null);
  };

  const handleRegenerate = useCallback(() => {
    const msgs = sessionsRef.current.find((s) => s.id === currentSessionIdRef.current)?.messages || [];
    let lastUserIdx = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].sender === 'user') {
        lastUserIdx = i;
        break;
      }
    }
    if (lastUserIdx < 0) return;
    const lastUserMsg = msgs[lastUserIdx];
    const cfg = settingsRef.current;

    // Drop the AI replies being replaced (everything after the last user message).
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== currentSessionIdRef.current) return session;
        if (session.messages.length <= lastUserIdx + 1) return session;
        return { ...session, messages: session.messages.slice(0, lastUserIdx + 1) };
      })
    );

    const taskId = wsClient.regenerateMessage(lastUserMsg.text, currentSessionIdRef.current, {
      provider: cfg.llmProvider,
      model: cfg.model,
      apiKey: cfg.apiKeys?.[cfg.llmProvider] || '',
    });
    setActiveTaskId(taskId);
    resetWatchdog(taskId);
    scrollToBottom(true);
  }, [resetWatchdog, scrollToBottom]);

  const handleModifyResponse = useCallback((messageId: string, instruction: string) => {
    const msgs = sessionsRef.current.find((s) => s.id === currentSessionIdRef.current)?.messages || [];
    const targetIdx = msgs.findIndex((m) => m.id === messageId);
    if (targetIdx < 0) return;
    let userText = '';
    for (let i = targetIdx; i >= 0; i--) {
      if (msgs[i].sender === 'user') {
        userText = msgs[i].text;
        break;
      }
    }
    if (!userText.trim()) return;

    // Replace the target AI message (and anything after it) with the modified reply.
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== currentSessionIdRef.current) return session;
        return { ...session, messages: session.messages.slice(0, targetIdx) };
      })
    );

    const cfg = settingsRef.current;
    const taskId = wsClient.modifyResponse(userText, instruction, currentSessionIdRef.current, {
      provider: cfg.llmProvider,
      model: cfg.model,
      apiKey: cfg.apiKeys?.[cfg.llmProvider] || '',
    });
    setActiveTaskId(taskId);
    resetWatchdog(taskId);
    scrollToBottom(true);
  }, [resetWatchdog, scrollToBottom]);

  const handleEditMessage = useCallback((messageId: string, newText: string) => {
    // Truncate: remove the edited message and everything after it, then re-send
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== currentSessionIdRef.current) return session;
        const msgIndex = session.messages.findIndex((m) => m.id === messageId);
        if (msgIndex < 0) return session;
        // Keep messages before the edited one
        return { ...session, messages: session.messages.slice(0, msgIndex) };
      })
    );
    // Small timeout so the splice renders before we re-send
    setTimeout(() => handleSendMessage(newText), 50);
  }, [handleSendMessage]);

  const onToggleTheme = () => {
    setSettings((prev) => ({
      ...prev,
      theme: prev.theme === 'dark' ? 'light' : 'dark',
    }));
  };

  // ⌘K / Ctrl+K shortcut to open Command Palette
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setCommandPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  // Group real sessions chronologically — uses lastActiveAt so resumed sessions appear in 'Today'
  const groupedSessions = useMemo(() => {
    const query = sessionSearch.toLowerCase().trim();
    const filtered = sessions.filter((s) => {
      if (!query) return true;
      if ((s.title || '').toLowerCase().includes(query)) return true;
      return (s.messages || []).some((m) => (m.text || '').toLowerCase().includes(query));
    });

    const now = Date.now();
    const oneDay = 24 * 60 * 60 * 1000;
    const today: ChatSession[] = [];
    const yesterday: ChatSession[] = [];
    const older: ChatSession[] = [];

    filtered.forEach((s) => {
      // Use lastActiveAt if set; fall back to createdAt for legacy sessions
      const ref = s.lastActiveAt || s.createdAt || now;
      const diff = now - ref;
      if (diff < oneDay) {
        today.push(s);
      } else if (diff < 2 * oneDay) {
        yesterday.push(s);
      } else {
        older.push(s);
      }
    });

    return { today, yesterday, older, hasResults: filtered.length > 0 };
  }, [sessions, sessionSearch]);

  const activeConnectorsCount = useMemo(() => {
    return Object.values(settings.connectors || {}).filter(Boolean).length;
  }, [settings.connectors]);

  return (
    <div className="makima-app-shell">
      {/* ── TOAST NOTIFICATIONS (aria-live for screen readers) ── */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="false"
        style={{
          position: 'fixed',
          top: 16,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 2000,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          pointerEvents: toasts.length > 0 ? 'none' : undefined,
        }}
      >
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              role={t.level === 'error' ? 'alert' : undefined}
              initial={{ opacity: 0, y: -12, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.97 }}
              transition={{ type: 'spring', stiffness: 450, damping: 32 }}
              style={{
                background: 'var(--bg-surface)',
                border: `1px solid ${t.level === 'error' ? 'var(--danger)' : t.level === 'success' ? 'var(--success)' : 'var(--primary-border)'}`,
                borderRadius: 'var(--radius-md)',
                padding: '8px 16px',
                fontSize: '0.82rem',
                color: 'var(--text-primary)',
                boxShadow: 'var(--shadow-md)',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                pointerEvents: 'auto',
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  flexShrink: 0,
                  background: t.level === 'error' ? 'var(--danger)' : t.level === 'success' ? 'var(--success)' : 'var(--primary)',
                }}
              />
              <span>{t.message}</span>
              <button
                type="button"
                aria-label="Dismiss notification"
                onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  padding: '0 2px',
                  fontSize: '1rem',
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>


      {/* ── LEFT COLLAPSIBLE SIDEBAR ── */}
      <aside className={`makima-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
        {/* Brand */}
        <div className="sidebar-header">
          <div className="sidebar-brand">
            <div className="brand-gem">
              <Sparkles size={16} />
            </div>
            <div>
              <div className="brand-name">Makima</div>
              <div className="brand-badge">Autonomous AI</div>
            </div>
          </div>
        </div>

        {/* New Chat Button */}
        <button className="new-chat-btn" onClick={handleNewChat} title="Start new conversation (⌘K)">
          <div className="new-chat-btn-left">
            <Plus size={16} color="var(--primary)" />
            <span>New Chat</span>
          </div>
          <span className="new-chat-shortcut">⌘K</span>
        </button>

        {/* Search Input */}
        <div className="sidebar-search-box">
          <Search size={14} className="sidebar-search-icon" />
          <input
            type="text"
            placeholder="Search chats..."
            value={sessionSearch}
            onChange={(e) => setSessionSearch(e.target.value)}
          />
        </div>

        {/* Chronological Sessions List */}
        <div className="sidebar-sessions-list">
          {groupedSessions.today.length > 0 && (
            <div>
              <div className="sessions-group-label">Today</div>
              {groupedSessions.today.map((session) => (
                <div
                  key={session.id}
                  className={`session-item ${session.id === currentSessionId ? 'active' : ''}`}
                  onClick={() => setCurrentSessionId(session.id)}
                >
                  <MessageSquare size={14} style={{ opacity: 0.7, flexShrink: 0, marginRight: 8 }} />
                  {editingSessionId === session.id ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1 }} onClick={(e) => e.stopPropagation()}>
                      <input
                        type="text"
                        value={editingSessionTitle}
                        onChange={(e) => setEditingSessionTitle(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveRename(session.id);
                          if (e.key === 'Escape') setEditingSessionId(null);
                        }}
                        autoFocus
                        style={{
                          background: 'var(--bg-canvas)',
                          border: '1px solid var(--border-focus)',
                          borderRadius: 4,
                          color: 'var(--text-primary)',
                          fontSize: '0.82rem',
                          padding: '2px 6px',
                          width: '100%',
                          outline: 'none',
                        }}
                      />
                      <button className="session-action-btn" onClick={() => handleSaveRename(session.id)} title="Save">
                        <Check size={12} />
                      </button>
                      <button className="session-action-btn" onClick={() => setEditingSessionId(null)} title="Cancel">
                        <X size={12} />
                      </button>
                    </div>
                  ) : (
                    <>
                      <span className="session-title-text">{session.title || 'New Conversation'}</span>
                      <div className="session-actions">
                        <button
                          className="session-action-btn"
                          onClick={(e) => handleStartRename(session, e)}
                          title="Rename"
                        >
                          <Edit2 size={12} />
                        </button>
                        {sessions.length > 1 && (
                          <button
                            className={`session-action-btn ${pendingDeleteId === session.id ? 'confirm-delete' : 'delete'}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteSession(session.id);
                            }}
                            title={pendingDeleteId === session.id ? 'Click again to confirm delete' : 'Delete'}
                          >
                            {pendingDeleteId === session.id ? '✕ Delete?' : <Trash2 size={12} />}
                          </button>
                        )}
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}

          {groupedSessions.yesterday.length > 0 && (
            <div>
              <div className="sessions-group-label">Yesterday</div>
              {groupedSessions.yesterday.map((session) => (
                <div
                  key={session.id}
                  className={`session-item ${session.id === currentSessionId ? 'active' : ''}`}
                  onClick={() => setCurrentSessionId(session.id)}
                >
                  <MessageSquare size={14} style={{ opacity: 0.7, flexShrink: 0, marginRight: 8 }} />
                  <span className="session-title-text">{session.title || 'New Conversation'}</span>
                  <div className="session-actions">
                    <button className="session-action-btn" onClick={(e) => handleStartRename(session, e)} title="Rename">
                      <Edit2 size={12} />
                    </button>
                    <button className="session-action-btn delete" onClick={(e) => { e.stopPropagation(); handleDeleteSession(session.id); }} title="Delete">
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {groupedSessions.older.length > 0 && (
            <div>
              <div className="sessions-group-label">Previous</div>
              {groupedSessions.older.map((session) => (
                <div
                  key={session.id}
                  className={`session-item ${session.id === currentSessionId ? 'active' : ''}`}
                  onClick={() => setCurrentSessionId(session.id)}
                >
                  <MessageSquare size={14} style={{ opacity: 0.7, flexShrink: 0, marginRight: 8 }} />
                  <span className="session-title-text">{session.title || 'New Conversation'}</span>
                  <div className="session-actions">
                    <button className="session-action-btn" onClick={(e) => handleStartRename(session, e)} title="Rename">
                      <Edit2 size={12} />
                    </button>
                    <button className="session-action-btn delete" onClick={(e) => { e.stopPropagation(); handleDeleteSession(session.id); }} title="Delete">
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Empty search state */}
          {!groupedSessions.hasResults && sessionSearch && (
            <div style={{ padding: '20px 16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
              No conversations match "{sessionSearch}"
            </div>
          )}
        </div>

        {/* Sidebar Footer */}
        <div className="sidebar-footer">
          <div className="sidebar-status-pill">
            <span className={`status-dot ${isConnected ? 'online' : ''}`} />
            <span>{isConnected ? 'Makima Brain Connected' : 'Brain Offline (Reconnecting...)'}</span>
          </div>

          <div className="sidebar-footer-actions">
            <button className="footer-icon-btn" onClick={onToggleTheme} title="Toggle Theme">
              {settings.theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
              <span>{settings.theme === 'dark' ? 'Light' : 'Dark'}</span>
            </button>
            <button className="footer-icon-btn" onClick={() => setSettingsOpen(true)} title="Settings">
              <Settings size={14} />
              <span>Settings</span>
            </button>
          </div>
        </div>
      </aside>

      {/* ── MAIN WORKSPACE ── */}
      <div className="makima-main-workspace">
        {/* Top Navigation Bar */}
        <header className="makima-topbar">
          <div className="topbar-left">
            <button
              className="sidebar-toggle-btn"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            >
              <PanelLeft size={17} />
            </button>

            {/* Model Selector Pill */}
            <button
              type="button"
              className="model-selector-pill"
              onClick={() => setModelPopoverOpen(true)}
              title="Select AI Model or Provider"
            >
              <Sparkles size={13} className="model-sparkle-icon" />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0, maxWidth: '220px' }}>{settings.llmProvider.toUpperCase()} · {settings.model}</span>
              <span style={{ opacity: 0.6, fontSize: '0.65rem' }}>▾</span>
            </button>

            {/* Quick Actions / Command Palette Trigger */}
            <button
              type="button"
              className="topbar-search-trigger"
              onClick={() => setCommandPaletteOpen(true)}
              title="Search commands, agents & conversations (⌘K)"
            >
              <Search size={13} />
              <span>Commands...</span>
              <kbd className="topbar-kbd-badge">⌘K</kbd>
            </button>
          </div>

          <div className="topbar-right">
            {/* Brain Telemetry Status Pill */}
            <button
              className="topbar-btn telemetry-pill"
              onClick={() => setTelemetryOpen(true)}
              title="Makima Brain Telemetry & Health"
            >
              <Activity size={14} color={isConnected ? 'var(--accent-emerald)' : 'var(--danger)'} />
              <span>{isConnected ? (telemetryLatency !== null ? `${telemetryLatency}ms` : 'Brain Online') : 'Brain Offline'}</span>
            </button>

            {/* Export Chat */}
            <button
              className="topbar-btn"
              onClick={() => setExportOpen(true)}
              title="Export Conversation (Markdown, JSON, Text)"
            >
              <FileDown size={14} />
              <span>Export</span>
            </button>

            {/* Pinned Highlights */}
            {pinnedMessages.length > 0 && (
              <button
                className="topbar-btn pinned-badge-btn"
                onClick={() => setPinnedDrawerOpen(true)}
                title="View Pinned Highlights"
              >
                <Pin size={14} color="var(--accent-amber)" style={{ fill: 'currentColor' }} />
                <span>{pinnedMessages.length} Pinned</span>
              </button>
            )}

            {/* Connectors Shortcut */}
            <button
              className="topbar-btn"
              onClick={() => {
                setSettingsTab('connectors');
                setSettingsOpen(true);
              }}
              title="App Connectors"
            >
              <Layers size={14} />
              <span>{activeConnectorsCount} Connectors</span>
            </button>

            {/* Voice Mode Toggle */}
            <button
              className={`topbar-btn ${voicePanelOpen ? 'active' : ''}`}
              onClick={() => setVoicePanelOpen(!voicePanelOpen)}
              title="Voice Mode"
            >
              <Mic size={14} />
              <span>Voice</span>
            </button>

            {/* Artifacts Canvas Toggle */}
            <button
              className={`topbar-btn ${activeCanvasItem ? 'active' : ''}`}
              onClick={() => {
                if (activeCanvasItem) {
                  setActiveCanvasItem(null);
                } else {
                  addToast('Open an artifact in the chat or trigger code generation', 'info');
                }
              }}
              title="Toggle Canvas / Artifacts"
            >
              <Code size={14} />
              <span>Canvas</span>
            </button>

            {/* Settings Trigger */}
            <button
              className="sidebar-toggle-btn"
              onClick={() => setSettingsOpen(true)}
              title="Settings"
            >
              <Settings size={16} />
            </button>
          </div>
        </header>

        {/* Disconnection Banner */}
        {!isConnected && (
          <div style={{
            background: 'var(--danger-subtle)',
            borderBottom: '1px solid var(--danger)',
            color: 'var(--danger)',
            padding: '6px 16px',
            fontSize: '0.78rem',
            fontWeight: 500,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
          }}>
            <span>⚠️ Makima Brain WebSocket disconnected. Attempting automatic reconnection...</span>
          </div>
        )}

        {/* Conversational Stream */}
        <div ref={scrollContainerRef} className="chat-scroll-container">
          <div className="chat-content-lane">
            {currentSession && currentSession.messages.length > 0 ? (
              <>
                {currentSession.messages.map((msg) => (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    onRegenerate={handleRegenerate}
                    onModifyResponse={handleModifyResponse}
                    onStop={handleStopTask}
                    onApproveAction={handleApproveAction}
                    onRejectAction={handleRejectAction}
                    onEditMessage={handleEditMessage}
                    onOpenCanvas={handleOpenCanvas}
                    onTogglePin={handleTogglePin}
                  />
                ))}
                <div ref={messagesEndRef} />
              </>
            ) : (
              /* Gemini-style Clean Welcome Screen (Zero Mock Conversations) */
              <div className="welcome-hero-container">
                <div className="welcome-avatar-icon welcome-rise" style={{ animationDelay: '0ms' }}>
                  <Sparkles size={26} />
                </div>
                <h1 className="welcome-headline welcome-rise" style={{ animationDelay: '60ms' }}>How can I help you today?</h1>
                <p className="welcome-subtitle welcome-rise" style={{ animationDelay: '120ms' }}>
                  Ask me anything to write code, search the web, automate local system actions, or brainstorm ideas.
                </p>
                <WelcomeTypewriter />

                <div className="welcome-suggestions-grid">
                  {[
                    {
                      icon: <Code size={18} />,
                      title: 'Code a Python script',
                      desc: 'Build an async scraper or API handler',
                      prompt: 'Write a clean Python script using httpx and asyncio to fetch and parse JSON data with retry logic.',
                    },
                    {
                      icon: <FileText size={18} />,
                      title: 'Analyze architecture',
                      desc: 'Review project files and suggest optimizations',
                      prompt: 'Review the current codebase architecture and suggest optimizations for modularity and performance.',
                    },
                    {
                      icon: <Sparkles size={18} />,
                      title: 'Brainstorm ideas',
                      desc: 'Ideate features for autonomous agent workflows',
                      prompt: 'Brainstorm 5 high-impact features for an autonomous desktop AI assistant.',
                    },
                    {
                      icon: <Video size={18} />,
                      title: 'Media & Web Search',
                      desc: 'Search for resources or control playback',
                      prompt: 'Search the web for the latest updates in AI agent architectures and summarize the key findings.',
                    },
                  ].map((card, idx) => (
                    <button
                      key={idx}
                      className="suggestion-chip-card welcome-rise"
                      style={{ animationDelay: `${180 + idx * 50}ms` }}
                      onClick={() => handleSuggestionClick(card.prompt)}
                    >
                      <div className="chip-icon-box">{card.icon}</div>
                      <div className="chip-text-group">
                        <span className="chip-title">{card.title}</span>
                        <span className="chip-desc">{card.desc}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Voice Session Panel (Slide down when active) */}
        {voicePanelOpen && (
          <div style={{
            position: 'absolute',
            top: 52,
            left: 0,
            right: 0,
            zIndex: 22,
            background: 'var(--bg-surface)',
            borderBottom: '1px solid var(--border-subtle)',
            padding: '12px 24px',
            boxShadow: 'var(--shadow-md)',
          }}>
            <Suspense fallback={null}>
              <VoiceSessionController
                conversationId={currentSessionId}
                connected={isConnected}
                settings={{
                  ...settings,
                  apiKey: settings.apiKeys?.gemini || '',
                }}
                onTurnStarted={() => undefined}
                onSessionChange={() => undefined}
              />
            </Suspense>
          </div>
        )}

        {/* Floating Composer Capsule (ChatGPT / Gemini Signature Pill) */}
        <div className="floating-composer-anchor">
          <div className="composer-capsule-wrapper">
            <QuickPromptBar
              onSelectPrompt={(prompt) => handleSendMessage(prompt)}
              disabled={!isConnected || Boolean(activeTaskId)}
            />
            <ChatInput
              onSendMessage={handleSendMessage}
              onRecordingChange={() => undefined}
              onOpenLibrary={() => setLibraryOpen(true)}
              onStop={() => {
                if (activeTaskId) {
                  clearWatchdog(activeTaskId);
                  wsClient.stopTask(activeTaskId);
                }
              }}
              isGenerating={Boolean(activeTaskId)}
              disabled={!isConnected}
              activeModelLabel={`${(settings.llmProvider || '').toUpperCase()} · ${settings.model || 'Default'}`}
              onOpenModelSelector={() => setModelPopoverOpen(true)}
              wsUrl={settings.wsUrl}
              libraryItem={libraryItem}
              onLibraryItemConsumed={() => setLibraryItem(null)}
            />
            <div className="composer-disclaimer">
              Makima can make mistakes. Verify important information and agent execution results.
            </div>
          </div>
        </div>
      </div>

      {/* ── SLIDE-OVER ARTIFACTS / CANVAS DRAWER ── */}
      <AnimatePresence>
        {activeCanvasItem && (
          <CanvasDrawer
            key={activeCanvasItem.id}
            item={activeCanvasItem}
            onClose={() => setActiveCanvasItem(null)}
          />
        )}
      </AnimatePresence>

      {/* ── MODEL SELECTOR POPOVER ── */}
      <AnimatePresence>
        {modelPopoverOpen && (
          <Suspense fallback={null}>
            <ModelSelectorPopover
              key="model-selector"
              isOpen
              onClose={() => setModelPopoverOpen(false)}
              providers={providers}
              isLoading={!providersLoaded}
              activeProviderId={settings.llmProvider}
              activeModel={settings.model}
              onSelectModel={handleSelectModel}
              onOpenFullSettings={() => {
                setModelPopoverOpen(false);
                setSettingsTab('models');
                setSettingsOpen(true);
              }}
              clientApiKeys={settings.apiKeys}
              onSaveClientApiKey={handleSaveClientApiKey}
            />
          </Suspense>
        )}
      </AnimatePresence>

      {/* ── SETTINGS MODAL ── */}
      <AnimatePresence>
        {settingsOpen && (
          <Suspense fallback={null}>
            <SettingsModal
              key="settings-modal"
              isOpen
              onClose={() => setSettingsOpen(false)}
              settings={settings}
              onSave={(newSettings) => {
                setSettings(newSettings);
                setSettingsOpen(false);
                addToast('Settings saved successfully', 'success');
              }}
              initialTab={settingsTab}
            />
          </Suspense>
        )}
      </AnimatePresence>

      {/* ── MEDIA LIBRARY PANEL ── */}
      <AnimatePresence>
        {libraryOpen && (
          <Suspense fallback={null}>
            <MediaLibraryPanel
              key="media-library"
              wsUrl={settings.wsUrl}
              onClose={() => setLibraryOpen(false)}
              onSelect={(item: MediaLibraryEntry) => {
                setLibraryItem(item); // Wire selection to ChatInput via libraryItem prop
                setLibraryOpen(false);
              }}
            />
          </Suspense>
        )}
      </AnimatePresence>

      {/* ── UNIVERSAL COMMAND PALETTE (⌘K) ── */}
      <AnimatePresence>
        {commandPaletteOpen && (
          <Suspense fallback={null}>
            <CommandPalette
              key="command-palette"
              isOpen
              onClose={() => setCommandPaletteOpen(false)}
              sessions={sessions}
              currentSessionId={currentSessionId}
              onSelectSession={(id) => setCurrentSessionId(id)}
              onNewChat={handleNewChat}
              onOpenExport={() => setExportOpen(true)}
              onToggleVoice={() => setVoicePanelOpen((prev) => !prev)}
              onToggleTheme={onToggleTheme}
              isDarkTheme={settings.theme === 'dark'}
              onOpenModels={() => setModelPopoverOpen(true)}
              onOpenConnectors={() => {
                setSettingsTab('connectors');
                setSettingsOpen(true);
              }}
              onOpenMediaLibrary={() => setLibraryOpen(true)}
              onOpenTelemetry={() => setTelemetryOpen(true)}
              onOpenPinned={() => setPinnedDrawerOpen(true)}
              onSelectAgentMode={(mode) => {
                setSettings((prev) => ({ ...prev, persona: mode as any }));
                addToast(`Switched to ${mode.toUpperCase()} persona`, 'info');
              }}
            />
          </Suspense>
        )}
      </AnimatePresence>

      {/* ── BRAIN TELEMETRY & HEALTH MODAL ── */}
      <AnimatePresence>
        {telemetryOpen && (
          <Suspense fallback={null}>
            <BrainTelemetryModal
              key="brain-telemetry"
              isOpen
              onClose={() => setTelemetryOpen(false)}
              isConnected={isConnected}
              settings={settings}
              sessionCount={sessions.length}
              totalMessagesCount={sessions.reduce((acc, s) => acc + s.messages.length, 0)}
            />
          </Suspense>
        )}
      </AnimatePresence>

      {/* ── CHAT EXPORT MODAL ── */}
      <AnimatePresence>
        {exportOpen && (
          <Suspense fallback={null}>
            <ChatExportModal
              key="chat-export"
              isOpen
              onClose={() => setExportOpen(false)}
              session={currentSession}
              onToast={addToast}
            />
          </Suspense>
        )}
      </AnimatePresence>

      {/* ── PINNED HIGHLIGHTS DRAWER ── */}
      <AnimatePresence>
        {pinnedDrawerOpen && (
          <Suspense fallback={null}>
            <PinnedMessagesDrawer
              key="pinned-drawer"
              isOpen
              onClose={() => setPinnedDrawerOpen(false)}
              pinnedMessages={pinnedMessages}
              onUnpin={handleTogglePin}
              onJumpToMessage={handleJumpToMessage}
              onToast={addToast}
            />
          </Suspense>
        )}
      </AnimatePresence>
    </div>
  );
};

export default App;
