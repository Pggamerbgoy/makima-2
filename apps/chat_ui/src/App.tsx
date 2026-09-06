import React, { useCallback, useState, useEffect, useRef } from 'react';
import { MessageBubble } from './components/MessageBubble';
import { ChatInput } from './components/ChatInput';
import { SettingsModal } from './components/SettingsModal';
import { CanvasDrawer } from './components/CanvasDrawer';
import { VoiceSessionController } from './components/VoiceSessionController';
import type { Message, ChatSession, AppSettings, Attachment, CanvasItem, MediaLibraryEntry, AgentActivityEvent } from './types/chat';
import { wsClient } from './services/wsClient';
import {
  Sparkles, Code, Cpu, Video, FileText, Download, UploadCloud,
  PanelLeft, Settings, Sun, Moon, MessageSquare, Trash2, Search,
  Plus, Activity, Database
} from 'lucide-react';
import { useDropzone } from 'react-dropzone';
import { MediaLibraryPanel } from './components/MediaLibraryPanel';

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

export const App: React.FC = () => {
  // Load settings from localStorage
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

  // Load sessions from localStorage
  const [sessions, setSessions] = useState<ChatSession[]>(() => {
    const saved = localStorage.getItem('makima_sessions');
    if (saved) {
      try {
        return JSON.parse(saved);
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
  const [activityOpen, setActivityOpen] = useState<boolean>(true);
  const [dataHubOpen, setDataHubOpen] = useState<boolean>(true);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [settingsTab, setSettingsTab] = useState<'models' | 'general' | 'connectors' | 'voice' | 'developer'>('models');
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [activeCanvasItem, setActiveCanvasItem] = useState<CanvasItem | null>(null);
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [_libraryItem, setLibraryItem] = useState<MediaLibraryEntry | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [col2Tab, setCol2Tab] = useState<'activity' | 'agents' | 'logs'>('activity');
  const [col4Tab, setCol4Tab] = useState<'sessions' | 'memory' | 'files'>('sessions');
  const [sessionSearch, setSessionSearch] = useState<string>('');
  const [liveLogs, setLiveLogs] = useState<Array<{ id: string; time: string; type: string; summary: string; level: 'info' | 'warn' | 'ok' | 'err' }>>([
    { id: '1', time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }), type: 'INIT', summary: 'Makima Neo-Glass UI initialized', level: 'info' },
  ]);

  const [toasts, setToasts] = useState<{ id: string; message: string; level: 'info' | 'success' | 'warning' | 'error'; durationMs: number }[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const incomingHandlerRef = useRef<(data: any) => void>(() => undefined);
  const voiceTaskIdsRef = useRef(new Set<string>());
  const voiceResponseRef = useRef(new Map<string, string>());
  const voiceSpokenTaskIdsRef = useRef(new Set<string>());
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
    }, 180000); // 3 minutes timeout for deep research and heavy multi-step tasks
    streamingWatchdogTimersRef.current.set(taskId, timer);
  }, [clearWatchdog]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    noClick: true,
    noKeyboard: true,
    onDrop: () => undefined,
  });

  // Save state to localStorage on changes
  useEffect(() => {
    localStorage.setItem('makima_settings', JSON.stringify(settings));
    document.documentElement.setAttribute('data-theme', settings.theme);
  }, [settings]);

  useEffect(() => {
    localStorage.setItem('makima_sessions', JSON.stringify(sessions));
  }, [sessions]);

  // Connect to WebSocket
  useEffect(() => {
    wsClient.configure(settings.wsUrl);
  }, [settings.wsUrl]);

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
  }, [currentSessionId]);

  // Smart auto scroll to bottom
  const scrollToBottom = (force: boolean = false) => {
    if (!scrollContainerRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
      return;
    }
    const { scrollHeight, scrollTop, clientHeight } = scrollContainerRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 180;
    if (force || isNearBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
    }
  };

  const currentSession = sessions.find((s) => s.id === currentSessionId) || sessions[0];

  const handleIncomingWSMessage = useCallback((data: any) => {
    if (!data) return;

    // Log incoming event to live dashboard feed
    setLiveLogs((prev) => [
      {
        id: `${Date.now()}_${Math.random()}`,
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        type: String(data.type || 'MSG').toUpperCase(),
        summary: data.payload?.message || data.payload?.subtask || (typeof data.payload?.text === 'string' ? data.payload.text.slice(0, 45) : '') || data.type || 'Event',
        level: data.type?.includes('error') ? 'err' : data.type?.includes('done') || data.type === 'pong' ? 'ok' : 'info',
      },
      ...prev.slice(0, 49),
    ]);

    // Handle Auto-Persona switching
    if (data.type === 'persona_changed' && data.payload?.persona) {
      setSettings((prev) => ({ ...prev, persona: data.payload.persona }));
      return;
    }

    // Handle Toast Notifications
    if (data.type === 'toast_notification') {
      const toastId = `toast_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
      const payload = data.payload || {};
      const duration = payload.duration_ms || 3000;
      const targetTaskId = data.task_id || payload.task_id || activeTaskId;

      const newToast = {
        id: toastId,
        message: payload.message || 'Notification',
        level: (payload.level || 'info') as 'info' | 'success' | 'warning' | 'error',
        durationMs: duration,
      };
      setToasts((prev) => [...prev, newToast]);
      setTimeout(() => removeToast(toastId), duration);

      // Clean up optimistic empty AI bubble for direct/toast tasks
      if (targetTaskId) {
        clearWatchdog(targetTaskId);
        setActiveTaskId((current) => (current === targetTaskId ? null : current));
        setSessions((prev) =>
          prev.map((session) => ({
            ...session,
            messages: session.messages.filter(
              (m) => !(m.taskId === targetTaskId && m.sender === 'ai' && !m.text.trim())
            ),
          }))
        );
      }
      return;
    }

    // Handle Real-Time Conversational Canvas updates
    if (data.type === 'update_canvas' && data.payload) {
      setActiveCanvasItem({
        id: data.task_id || 'canvas_' + Date.now(),
        title: data.payload.title || 'Canvas Snippet',
        content: data.payload.content || '',
        language: data.payload.language || 'typescript',
      });
      return;
    }

    const taskId = data.task_id || data.payload?.task_id;
    const textChunk = data.payload?.text || data.text || '';
    const thoughtChunk = data.payload?.thought || data.thought || '';
    const isFinal = data.payload?.is_final || data.is_final || data.type === 'ai_response_done';
    const incomingAgentName = data.payload?.agent || data.payload?.agent_name || data.agent || data.agent_name || undefined;
    const incomingFormat = data.payload?.format || data.format || undefined;
    const brainBase = settings.wsUrl.replace(/^ws/, 'http').replace(/\/ws$/, '');
    const toAbsoluteMediaUrl = (value?: string) => value?.startsWith('/') ? `${brainBase}${value}` : value;
    const incomingMedia = Array.isArray(data.payload?.media) ? data.payload.media.map((item: any) => ({ id: item.id || `media_${Date.now()}`, type: item.type || item.kind || 'document', url: toAbsoluteMediaUrl(item.url) || '', thumbnailUrl: toAbsoluteMediaUrl(item.thumbnailUrl || item.thumbnail_url), mimeType: item.mimeType || item.mime_type, title: item.title || item.name, alt: item.alt, downloadable: item.downloadable !== false })) : undefined;
    const incomingSources = Array.isArray(data.payload?.sources) ? data.payload.sources : undefined;

    if (data.type.startsWith('voice_')) return;

    if (data.type === 'media_ready' || data.type === 'media_processing' || data.type === 'media_error') {
      setSessions((prev) => prev.map((session) => session.id !== currentSessionId ? session : {
        ...session,
        messages: session.messages.map((message) => message.taskId === taskId ? { ...message, attachments: message.attachments?.map((att) => att.mediaId === data.payload?.media_id ? { ...att, uploadState: data.type === 'media_error' ? 'failed' : 'ready', error: data.payload?.error } : att), status: data.type === 'media_error' ? 'error' : message.status } : message),
      }));
      return;
    }

    if (data.type === 'ai_error') {
      clearWatchdog(taskId);
      setSessions((prev) => prev.map((session) => session.id !== currentSessionId ? session : {
        ...session,
        messages: session.messages.map((message) => message.taskId === taskId ? { ...message, isStreaming: false, status: 'error', error: { code: data.payload?.code, message: data.payload?.error || data.payload?.message || 'Request failed', retryable: true } } : message),
      }));
      setActiveTaskId((current) => current === taskId ? null : current);
      return;
    }

    if (data.type === 'action_confirm_request') {
      const targetTaskId = taskId || activeTaskId;
      setSessions((prev) => prev.map((session) => {
        const hasMatchingMsg = session.messages.some((m) => m.taskId === targetTaskId || m.id === `ai_${targetTaskId}`);
        if (!hasMatchingMsg && session.id !== currentSessionId) return session;
        return {
          ...session,
          messages: session.messages.map((message) => {
            const isMatch = message.taskId === targetTaskId || message.id === `ai_${targetTaskId}` || (message.sender === 'ai' && message.isStreaming);
            return isMatch ? {
              ...message,
              actionConfirmation: {
                action: data.payload?.action || 'action',
                description: data.payload?.description || 'Makima wants to perform an action.',
                riskLevel: data.payload?.risk_level || 'medium',
                status: 'pending',
              },
            } : message;
          }),
        };
      }));
      return;
    }

    if (data.type === 'autonomy_mode_changed') {
      window.dispatchEvent(new CustomEvent('makima-autonomy', { detail: data.payload || {} }));
      return;
    }

    const activityTypes = new Set(['thinking_status', 'agent_started', 'agent_progress', 'agent_done', 'agent_error', 'agent_guardrail_hit', 'multi_agent_progress']);
    if (data.type === 'proactive_suggestion' || data.type === 'proactive_action') {
      const p = data.payload || {};
      const event: AgentActivityEvent = { id: `proactive_${Date.now()}_${Math.random()}`, type: data.type, agent: p.agent || 'makima', status: p.status, message: [p.description, p.rationale && `— ${p.rationale}`].filter(Boolean).join(' '), progress: undefined, timestamp: Date.now() };
      setSessions((prev) => prev.map((session) => session.id !== currentSessionId ? session : { ...session, messages: session.messages.map((message, idx, arr) => idx === arr.length - 1 && message.sender === 'ai' ? { ...message, agentActivity: [...(message.agentActivity || []), event] } : message) }));
      return;
    }
    if (activityTypes.has(data.type)) {
      const event: AgentActivityEvent = { id: `${taskId || 'task'}_${Date.now()}_${Math.random()}`, type: data.type, agent: data.payload?.agent, status: data.payload?.status, message: data.payload?.message || data.payload?.subtask || data.payload?.result_summary || data.payload?.text, progress: data.payload?.progress, timestamp: Date.now() };
      setSessions((prev) => prev.map((session) => session.id !== currentSessionId ? session : { ...session, messages: session.messages.map((message) => message.taskId === taskId ? { ...message, agentActivity: [...(message.agentActivity || []), event] } : message) }));
      return;
    }
    if (data.type === 'tool_call_finished') {
      const targetTaskId = taskId || activeTaskId;
      if (targetTaskId) {
        setSessions((prev) =>
          prev.map((session) => ({
            ...session,
            messages: session.messages.filter(
              (m) => !(m.taskId === targetTaskId && m.sender === 'ai' && !m.text.trim())
            ),
          }))
        );
      }
    }

    if (data.type === 'ai_response_done') {
      clearWatchdog(taskId);
      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== currentSessionId) return session;
          return {
            ...session,
            messages: session.messages
              .map((message) =>
                message.taskId === taskId ? { ...message, isStreaming: false, status: 'complete' as const } : message
              )
              .filter(
                (message) =>
                  !(
                    message.taskId === taskId &&
                    message.sender === 'ai' &&
                    !message.text.trim() &&
                    !message.media?.length &&
                    !message.sources?.length
                  )
              ),
          };
        })
      );
      setActiveTaskId((current) => (current === taskId ? null : current));
      return;
    }

    let completedMessageText = '';
    let completedMessageFormat = incomingFormat;

    setSessions((prevSessions) => {
      return prevSessions.map((session) => {
        if (session.id !== currentSessionId) return session;

        const msgs = [...session.messages];
        const streamingIndex = taskId ? msgs.findIndex((message) => message.sender === 'ai' && message.taskId === taskId) : msgs.findIndex((message) => message.sender === 'ai' && message.isStreaming);
        const streamingMsg = streamingIndex >= 0 ? msgs[streamingIndex] : null;
        if (streamingMsg) {
          const fullText = streamingMsg.text + textChunk;
          const updatedMsg: Message = {
            ...streamingMsg,
            text: fullText,
            thought: (streamingMsg.thought || '') + thoughtChunk,
            isStreaming: !isFinal,
            status: isFinal ? 'complete' : 'streaming',
            media: incomingMedia || streamingMsg.media,
            sources: incomingSources || streamingMsg.sources,
            agent_name: incomingAgentName || streamingMsg.agent_name,
            format: incomingFormat || streamingMsg.format,
          };
          if (isFinal) {
            completedMessageText = fullText;
            completedMessageFormat = updatedMsg.format;
          }
          if (voiceTaskIdsRef.current.has(taskId) && textChunk) voiceResponseRef.current.set(taskId, updatedMsg.text);
          return {
            ...session,
            messages: [...msgs.slice(0, streamingIndex), updatedMsg, ...msgs.slice(streamingIndex + 1)],
          };
        } else if (textChunk || thoughtChunk) {
          const newAiMsg: Message = {
            id: data.task_id || 'ai_' + Math.random().toString(36).substring(2, 9),
            sender: 'ai',
            text: textChunk,
            thought: thoughtChunk,
            timestamp: Date.now(),
            isStreaming: !isFinal,
            taskId,
            status: isFinal ? 'complete' : 'streaming',
            media: incomingMedia,
            sources: incomingSources,
            agent_name: incomingAgentName,
            format: incomingFormat,
          };
          if (isFinal) {
            completedMessageText = textChunk;
            completedMessageFormat = incomingFormat;
          }
          return {
            ...session,
            messages: [...msgs, newAiMsg],
          };
        }

        return session;
      });
    });

    if (isFinal) {
      clearWatchdog(taskId);
      setActiveTaskId((current) => current === taskId ? null : current);

      if (completedMessageFormat === 'report' && completedMessageText && completedMessageText.length > 1500) {
        setActiveCanvasItem({
          id: taskId || 'report_' + Date.now(),
          title: 'Executive Research Report',
          language: 'markdown',
          content: completedMessageText,
        });
      }

      if (voiceSessionId && voiceTaskIdsRef.current.has(taskId) && !voiceSpokenTaskIdsRef.current.has(taskId)) {
        voiceSpokenTaskIdsRef.current.add(taskId);
        const response = voiceResponseRef.current.get(taskId) || textChunk;
        wsClient.requestVoiceSpeech(voiceSessionId, taskId, settings.autoReadAloud ? response : '');
      }
    } else if (taskId) {
      resetWatchdog(taskId);
    }

    scrollToBottom();
  }, [activeTaskId, clearWatchdog, currentSessionId, resetWatchdog, settings.autoReadAloud, settings.wsUrl, voiceSessionId]);

  incomingHandlerRef.current = handleIncomingWSMessage;

  const handleSendMessage = (text: string, attachments: Attachment[]) => {
    const userMsgId = 'user_' + Math.random().toString(36).substring(2, 9);
    const newMsg: Message = {
      id: userMsgId,
      sender: 'user',
      text,
      timestamp: Date.now(),
      attachments,
    };

    setSessions((prev) =>
      prev.map((s) => {
        if (s.id === currentSessionId) {
          const isFirstMsg = s.messages.length === 0;
          const newTitle = isFirstMsg ? (text.length > 28 ? text.substring(0, 28) + '...' : text) : s.title;
          return {
            ...s,
            title: newTitle,
            messages: [...s.messages, newMsg],
          };
        }
        return s;
      })
    );

    const taskId = wsClient.sendMessage(text, currentSessionId, attachments);
    resetWatchdog(taskId);
    setActiveTaskId(taskId);
    setSessions((prev) => prev.map((session) => session.id === currentSessionId ? { ...session, messages: [...session.messages, { id: `ai_${taskId}`, sender: 'ai', text: '', timestamp: Date.now(), isStreaming: true, taskId, status: 'streaming' }] } : session));
    setTimeout(() => scrollToBottom(true), 50);
  };

  const handleVoiceTurnStarted = useCallback((taskId: string, text: string) => {
    voiceTaskIdsRef.current.add(taskId);
    voiceResponseRef.current.set(taskId, '');
    resetWatchdog(taskId);
    setActiveTaskId(taskId);
    setSessions((prev) => prev.map((session) => session.id !== currentSessionId ? session : {
      ...session,
      title: session.messages.length === 0 ? (text.length > 28 ? `${text.slice(0, 28)}…` : text) : session.title,
      messages: [...session.messages,
        { id: `user_${taskId}`, sender: 'user', text, timestamp: Date.now(), taskId },
        { id: `ai_${taskId}`, sender: 'ai', text: '', timestamp: Date.now(), isStreaming: true, taskId, status: 'streaming' },
      ],
    }));
    setTimeout(scrollToBottom, 50);
  }, [currentSessionId, resetWatchdog]);

  const handleEditMessage = (messageId: string, newText: string) => {
    if (activeTaskId) {
      try {
        wsClient.stopTask(activeTaskId);
      } catch (_stopErr) {
        console.debug('Failed to stop previous task before edit:', _stopErr);
      }
    }

    const taskId = wsClient.sendMessage(newText, currentSessionId);
    setActiveTaskId(taskId);

    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== currentSessionId) return session;
        const msgIdx = session.messages.findIndex((m) => m.id === messageId);
        if (msgIdx === -1) return session;

        const updatedMsg: Message = { ...session.messages[msgIdx], text: newText };
        const newMessages = [
          ...session.messages.slice(0, msgIdx),
          updatedMsg,
          { id: `ai_${taskId}`, sender: 'ai' as const, text: '', timestamp: Date.now(), isStreaming: true, taskId, status: 'streaming' as const },
        ];

        return {
          ...session,
          messages: newMessages,
        };
      })
    );
    setTimeout(scrollToBottom, 50);
  };

  const handleRegenerate = (messageId: string) => {
    const message = currentSession?.messages.find((item) => item.id === messageId);
    if (!message) return;
    const index = currentSession.messages.findIndex((item) => item.id === messageId);
    const userMessage = [...currentSession.messages.slice(0, index)].reverse().find((item) => item.sender === 'user');
    if (!userMessage) return;
    const taskId = `task_${Math.random().toString(36).slice(2, 9)}`;
    wsClient.regenerateMessage(taskId, userMessage.text, currentSessionId);
    setActiveTaskId(taskId);
    setSessions((prev) => prev.map((session) => session.id === currentSessionId ? { ...session, messages: [...session.messages, { id: `ai_${taskId}`, sender: 'ai', text: '', timestamp: Date.now(), isStreaming: true, taskId, status: 'streaming' }] } : session));
  };

  const handleModifyResponse = (messageId: string, instruction: string) => {
    const message = currentSession?.messages.find((item) => item.id === messageId);
    if (!message || !currentSession) return;
    const index = currentSession.messages.findIndex((item) => item.id === messageId);
    const userMessage = [...currentSession.messages.slice(0, index)].reverse().find((item) => item.sender === 'user');
    if (!userMessage) return;
    const taskId = `task_${Math.random().toString(36).slice(2, 9)}`;
    wsClient.modifyResponse(taskId, userMessage.text, instruction, currentSessionId);
    setActiveTaskId(taskId);
    setSessions((prev) => prev.map((session) => session.id === currentSessionId ? { ...session, messages: [...session.messages, { id: `ai_${taskId}`, sender: 'ai', text: '', timestamp: Date.now(), isStreaming: true, taskId, status: 'streaming' }] } : session));
  };

  const handleApproveAction = (taskId: string, action: string) => {
    wsClient.approveAction(taskId, action);
    setSessions((prev) => prev.map((session) => ({
      ...session,
      messages: session.messages.map((m) =>
        (m.taskId === taskId || m.id === taskId || m.id === `ai_${taskId}`) && m.actionConfirmation
          ? { ...m, actionConfirmation: { ...m.actionConfirmation, status: 'approved' } }
          : m
      ),
    })));
  };

  const handleRejectAction = (taskId: string, action: string) => {
    wsClient.rejectAction(taskId, action);
    setSessions((prev) => prev.map((session) => ({
      ...session,
      messages: session.messages.map((m) =>
        (m.taskId === taskId || m.id === taskId || m.id === `ai_${taskId}`) && m.actionConfirmation
          ? { ...m, actionConfirmation: { ...m.actionConfirmation, status: 'rejected' } }
          : m
      ),
    })));
  };

  const handleExportChat = () => {
    if (!currentSession) return;
    let mdContent = `# ${currentSession.title}\n\n`;
    currentSession.messages.forEach((m) => {
      mdContent += `### ${m.sender === 'user' ? 'User' : 'Makima AI'}\n${m.text}\n\n`;
    });

    const blob = new Blob([mdContent], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentSession.title.replace(/\s+/g, '_').toLowerCase()}.md`;
    a.click();
    URL.revokeObjectURL(url);
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
  };

  const handleDeleteSession = (id: string) => {
    setSessions((prev) => {
      const filtered = prev.filter((s) => s.id !== id);
      if (filtered.length === 0) {
        const fresh: ChatSession = {
          id: 'session_' + Math.random().toString(36).substring(2, 9),
          title: 'New Conversation',
          createdAt: Date.now(),
          messages: [],
        };
        setCurrentSessionId(fresh.id);
        return [fresh];
      }
      if (currentSessionId === id) {
        setCurrentSessionId(filtered[0].id);
      }
      return filtered;
    });
  };

  const handleSuggestionClick = (promptText: string) => {
    handleSendMessage(promptText, []);
  };

  const onToggleTheme_ = () =>
    setSettings((prev) => ({ ...prev, theme: prev.theme === 'dark' ? 'light' : 'dark' }));

  return (
    <div {...getRootProps()} className="dashboard-root">
      <input {...getInputProps()} />

      {/* Global Drag-and-Drop Overlay */}
      {isDragActive && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 9999,
          background: 'rgba(0, 242, 254, 0.08)',
          border: '2px dashed var(--accent-cyan)',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', color: 'var(--accent-cyan)', pointerEvents: 'none',
          backdropFilter: 'blur(8px)',
        }}>
          <UploadCloud size={56} style={{ marginBottom: '16px', opacity: 0.9 }} />
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem', letterSpacing: '0.12em', textTransform: 'uppercase', fontWeight: 800 }}>
            Drop files to attach
          </span>
        </div>
      )}

      {/* Floating Toast Notification Container */}
      {toasts.length > 0 && (
        <div style={{
          position: 'fixed',
          top: '20px',
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 10000,
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          alignItems: 'center',
          pointerEvents: 'none',
        }}>
          {toasts.map((t) => {
            const isErr = t.level === 'error';
            const isWarn = t.level === 'warning';
            const isSucc = t.level === 'success';
            const accent = isErr ? 'var(--status-red)' : isWarn ? 'var(--status-amber)' : isSucc ? 'var(--status-green)' : 'var(--accent-cyan)';
            return (
              <div
                key={t.id}
                style={{
                  background: 'var(--bg-glass-heavy)',
                  backdropFilter: 'blur(20px)',
                  WebkitBackdropFilter: 'blur(20px)',
                  border: `1px solid ${accent}`,
                  boxShadow: `0 12px 36px rgba(0, 0, 0, 0.6), 0 0 16px ${accent}`,
                  borderRadius: 'var(--radius-md)',
                  padding: '10px 22px',
                  color: 'var(--text-primary)',
                  fontSize: '0.84rem',
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  maxWidth: '90vw',
                  pointerEvents: 'auto',
                  animation: 'fadeIn 0.25s ease-out',
                }}
              >
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: accent, boxShadow: `0 0 10px ${accent}`, flexShrink: 0 }} />
                <span>{t.message}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* ── TOP HEADER BAR ── */}
      <header className="dashboard-header">
        <div className="header-brand">
          <div className="header-logo-mark">M</div>
          <div className="header-title-wrap">
            <span className="header-title">Makima</span>
            <span className="header-subtitle">v2.0 · Agent Engine</span>
          </div>

          <button
            type="button"
            className="model-pill-badge"
            onClick={() => { setSettingsTab('models'); setSettingsOpen(true); }}
            title="Switch AI model or provider"
          >
            <Sparkles size={12} color="var(--accent-cyan)" />
            <span>{settings.llmProvider.toUpperCase()} · {settings.model || 'Default'}</span>
            <span style={{ opacity: 0.6, fontSize: '0.6rem' }}>▾</span>
          </button>
        </div>

        <div className="header-controls">
          <div className={`header-status-pill ${isConnected ? 'connected' : 'disconnected'}`}>
            <span className={`status-dot ${isConnected ? 'pulse' : ''}`} />
            {isConnected ? 'Brain Online' : 'Offline'}
          </div>

          <button
            className={`header-icon-btn ${sidebarOpen ? 'active' : ''}`}
            onClick={() => setSidebarOpen(!sidebarOpen)}
            title="Toggle Control Panel"
          >
            <PanelLeft size={15} />
          </button>

          <button
            className={`header-icon-btn ${activityOpen ? 'active' : ''}`}
            onClick={() => setActivityOpen(!activityOpen)}
            title="Toggle Activity Stream"
          >
            <Activity size={15} />
          </button>

          <button
            className={`header-icon-btn ${dataHubOpen ? 'active' : ''}`}
            onClick={() => setDataHubOpen(!dataHubOpen)}
            title="Toggle Sessions & Memory"
          >
            <Database size={15} />
          </button>

          <button className="header-icon-btn" onClick={onToggleTheme_} title="Toggle Theme">
            {settings.theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>

          <button className="header-icon-btn" onClick={() => setSettingsOpen(true)} title="Settings">
            <Settings size={15} />
          </button>
        </div>
      </header>

      {/* ── MAIN WORKSPACE GRID ── */}
      <div className="dashboard-grid">

        {/* ── COL 1: AGENT CONTROL PANEL ── */}
        {sidebarOpen && (
          <div className="os-panel sidebar-col">
            <div className="panel-header">
              <span className="panel-header-title">
                <Cpu size={12} /> System Control
              </span>
              <button
                className="header-icon-btn"
                onClick={handleNewChat}
                style={{ width: 24, height: 24 }}
                title="New Session"
              >
                <Plus size={13} />
              </button>
            </div>
            <div className="panel-body">
              <button className="quick-action-btn" onClick={handleNewChat} style={{ width: '100%', marginBottom: 4 }}>
                <Plus size={14} color="var(--accent-cyan)" />
                <span>New Session</span>
              </button>

              <div className="panel-section-label">Active Agents</div>
              {[
                { name: 'Brain Core', desc: 'LLM routing & planning', status: isConnected ? (activeTaskId ? 'active' : 'idle') : 'offline' },
                { name: 'Browser Agent', desc: 'CDP Playwright Automation', status: 'idle' },
                { name: 'Code Agent', desc: 'Syntax AST & Sandbox', status: 'idle' },
                { name: 'Memory Agent', desc: 'HNSW SQLite Vector Recall', status: 'idle' },
                { name: 'System Agent', desc: 'Win32 Shell & Process', status: 'idle' },
                { name: 'Voice Agent', desc: 'Gemini Live Multimodal Audio', status: voiceSessionId ? 'active' : 'idle' },
              ].map((ag) => (
                <div
                  key={ag.name}
                  className="agent-status-row"
                  onClick={() => { setCol2Tab('agents'); setActivityOpen(true); }}
                  style={{ cursor: 'pointer' }}
                >
                  <div className="agent-icon"><Cpu size={12} /></div>
                  <div className="agent-info">
                    <div className="agent-name">{ag.name}</div>
                    <div className="agent-desc">{ag.desc}</div>
                  </div>
                  <span className={`agent-chip ${ag.status}`}>{ag.status}</span>
                </div>
              ))}

              <div className="panel-section-label" style={{ marginTop: 8 }}>Connectors</div>
              <div className="capabilities-list">
                {Object.entries(settings.connectors || {}).map(([key, enabled]) => (
                  <span
                    key={key}
                    className={`capability ${enabled ? 'enabled' : ''}`}
                    style={{ cursor: 'pointer' }}
                    onClick={() => { setSettingsTab('connectors'); setSettingsOpen(true); }}
                    title={`Configure ${key} connector`}
                  >
                    {key}
                  </span>
                ))}
              </div>
            </div>

            <div style={{ borderTop: '1px solid var(--border-panel)', padding: '8px 10px', display: 'flex', gap: 6 }}>
              <button className="quick-action-btn" onClick={onToggleTheme_} style={{ flex: 1 }}>
                {settings.theme === 'dark' ? <Sun size={13} /> : <Moon size={13} />}
                <span>Theme</span>
              </button>
              <button className="quick-action-btn" onClick={() => setSettingsOpen(true)} style={{ flex: 1 }}>
                <Settings size={13} />
                <span>Config</span>
              </button>
            </div>
          </div>
        )}

        {/* ── COL 2: AUTOMATION & ACTIVITY ── */}
        {activityOpen && (
          <div className="os-panel automation-col">
            <div className="panel-tabs">
              <button className={`panel-tab ${col2Tab === 'activity' ? 'active' : ''}`} onClick={() => setCol2Tab('activity')}>Activity</button>
              <button className={`panel-tab ${col2Tab === 'agents' ? 'active' : ''}`} onClick={() => setCol2Tab('agents')}>Agents</button>
              <button className={`panel-tab ${col2Tab === 'logs' ? 'active' : ''}`} onClick={() => setCol2Tab('logs')}>Logs ({liveLogs.length})</button>
            </div>
            <div className="panel-body">
              {col2Tab === 'activity' && (
                currentSession && currentSession.messages.length > 0 ? (
                  <>
                    <div className="panel-section-label">Recent Interaction Turns</div>
                    {currentSession.messages.slice(-8).reverse().map((msg) => (
                      <div key={msg.id} className="activity-item">
                        <div className="activity-item-header">
                          <span className={`activity-dot ${msg.isStreaming ? 'running' : 'done'}`} />
                          <span className="activity-name">{msg.sender === 'user' ? 'User Prompt' : 'Makima Response'}</span>
                          <span className="activity-time">{new Date(msg.timestamp || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                        </div>
                        <div className="activity-desc">
                          {(msg.text || '').slice(0, 68)}{(msg.text || '').length > 68 ? '…' : ''}
                        </div>
                      </div>
                    ))}
                  </>
                ) : (
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.72rem', textAlign: 'center', padding: '24px' }}>
                    No activity in this session.<br />Send a message to start.
                  </div>
                )
              )}

              {col2Tab === 'agents' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div className="panel-section-label">Agent Engine Status</div>
                  {[
                    { name: 'Brain Core', status: isConnected ? (activeTaskId ? 'active' : 'idle') : 'offline', role: 'LLM Orchestrator' },
                    { name: 'Browser Agent', status: 'idle', role: 'CDP Playwright Automation' },
                    { name: 'Code Agent', status: 'idle', role: 'Syntax AST & Sandbox' },
                    { name: 'Memory Agent', status: 'idle', role: 'HNSW SQLite Vector Recall' },
                    { name: 'System Agent', status: 'idle', role: 'Win32 Shell & Process Control' },
                    { name: 'Voice Agent', status: voiceSessionId ? 'active' : 'idle', role: 'Gemini Live 24kHz Audio Bridge' },
                  ].map((ag) => (
                    <div key={ag.name} className="agent-status-row">
                      <div className="agent-icon"><Cpu size={12} /></div>
                      <div className="agent-info">
                        <div className="agent-name">{ag.name}</div>
                        <div className="agent-desc">{ag.role}</div>
                      </div>
                      <span className={`agent-chip ${ag.status}`}>{ag.status}</span>
                    </div>
                  ))}
                </div>
              )}

              {col2Tab === 'logs' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', fontFamily: 'var(--font-mono)' }}>
                  <div className="panel-section-label">Live Event Stream</div>
                  {liveLogs.map((log) => (
                    <div key={log.id} style={{
                      padding: '5px 8px',
                      borderRadius: 'var(--radius-xs)',
                      background: 'rgba(0,0,0,0.3)',
                      borderLeft: `3px solid ${log.level === 'err' ? 'var(--status-red)' : log.level === 'ok' ? 'var(--status-green)' : 'var(--accent-cyan)'}`,
                      fontSize: '0.64rem',
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-muted)' }}>
                        <span style={{ color: log.level === 'err' ? 'var(--status-red)' : log.level === 'ok' ? 'var(--status-green)' : 'var(--accent-cyan)', fontWeight: 700 }}>{log.type}</span>
                        <span>{log.time}</span>
                      </div>
                      <div style={{ color: 'var(--text-secondary)', marginTop: '2px', wordBreak: 'break-all' }}>{log.summary}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── COL 3: CHAT CORE (MAIN PANEL) ── */}
        <div className="chat-core-panel">
          {!isConnected && (
            <div style={{
              background: 'rgba(255, 75, 114, 0.15)',
              borderBottom: '1px solid rgba(255, 75, 114, 0.35)',
              color: 'var(--status-red)',
              padding: '6px 14px',
              fontSize: '0.72rem',
              fontWeight: 600,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
            }}>
              ⚠ Makima Brain WebSocket disconnected. Attempting automatic reconnection...
            </div>
          )}

          {/* Messages Area */}
          <div ref={scrollContainerRef} className="chat-messages-area">
            <div className="chat-messages-inner">
              {currentSession && currentSession.messages.length > 0 ? (
                <>
                  {currentSession.messages.map((msg) => (
                    <MessageBubble
                      key={msg.id}
                      message={msg}
                      onRegenerate={handleRegenerate}
                      onModifyResponse={handleModifyResponse}
                      onEditMessage={handleEditMessage}
                      onStop={(taskId) => taskId && wsClient.stopTask(taskId)}
                      onApproveAction={handleApproveAction}
                      onRejectAction={handleRejectAction}
                      onOpenCanvas={(canvasItem) => setActiveCanvasItem(canvasItem)}
                    />
                  ))}
                  <div ref={messagesEndRef} />
                </>
              ) : (
                /* Neo-Glass Welcome Screen */
                <div className="chat-welcome">
                  <div className="chat-welcome-orb" />
                  <div className="chat-welcome-logo">M</div>
                  <div className="chat-welcome-title">Makima Core</div>
                  <p className="chat-welcome-sub">
                    Your autonomous AI engineering partner. Plan tasks, generate code, automate browsers, control system tools, or start voice sessions.
                  </p>
                  <div className="suggestion-grid">
                    {[
                      { icon: <Code size={16} color="var(--accent-cyan)" />, bg: 'rgba(0,242,254,0.1)', text: 'Write a Python script for real-time audio VAD' },
                      { icon: <Video size={16} color="var(--status-red)" />, bg: 'rgba(255,75,114,0.1)', text: 'Search YouTube for AI agent architecture guides' },
                      { icon: <Cpu size={16} color="var(--accent-purple-light)" />, bg: 'rgba(121,40,202,0.12)', text: 'Inspect and optimize Makima tool execution pipeline' },
                      { icon: <FileText size={16} color="var(--status-green)" />, bg: 'rgba(0,245,160,0.1)', text: 'Draft an executive briefing on today’s changes' },
                    ].map((chip, idx) => (
                      <div key={idx} className="suggestion-card" onClick={() => handleSuggestionClick(chip.text)}>
                        <div className="suggestion-card-icon" style={{ background: chip.bg }}>
                          {chip.icon}
                        </div>
                        <span className="suggestion-card-text">{chip.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Voice + Composer */}
          <div style={{ flexShrink: 0 }}>
            <VoiceSessionController
              conversationId={currentSessionId}
              connected={isConnected}
              settings={settings}
              onTurnStarted={handleVoiceTurnStarted}
              onSessionChange={(sessionId) => setVoiceSessionId(sessionId)}
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
            />
          </div>
        </div>

        {/* ── COL 4: DATA HUB ── */}
        {dataHubOpen && (
          <div className="os-panel data-hub-col">
            <div className="panel-tabs">
              <button className={`panel-tab ${col4Tab === 'sessions' ? 'active' : ''}`} onClick={() => setCol4Tab('sessions')}>Sessions</button>
              <button className={`panel-tab ${col4Tab === 'memory' ? 'active' : ''}`} onClick={() => setCol4Tab('memory')}>Memory</button>
              <button className={`panel-tab ${col4Tab === 'files' ? 'active' : ''}`} onClick={() => setCol4Tab('files')}>Files</button>
            </div>
            <div className="panel-body">
              {col4Tab === 'sessions' && (
                <>
                  <div className="panel-search">
                    <Search size={13} color="var(--text-muted)" />
                    <input
                      type="text"
                      placeholder="Search conversations..."
                      value={sessionSearch}
                      onChange={(e) => setSessionSearch(e.target.value)}
                    />
                  </div>

                  <div className="panel-section-label">Saved Conversations ({sessions.length})</div>
                  {sessions
                    .filter((s) => !sessionSearch || (s.title || '').toLowerCase().includes(sessionSearch.toLowerCase()))
                    .map((session) => (
                      <div
                        key={session.id}
                        className={`session-item ${session.id === currentSessionId ? 'active' : ''}`}
                        onClick={() => setCurrentSessionId(session.id)}
                      >
                        <MessageSquare size={13} className="session-item-icon" />
                        <span className="session-item-text">{session.title || 'New Conversation'}</span>
                        {sessions.length > 1 && (
                          <button
                            className="session-item-menu-btn"
                            onClick={(e) => { e.stopPropagation(); handleDeleteSession(session.id); }}
                            title="Delete"
                          >
                            <Trash2 size={12} />
                          </button>
                        )}
                      </div>
                    ))}

                  <div className="panel-section-label" style={{ marginTop: 10 }}>Actions</div>
                  <div className="quick-action-grid">
                    <button className="quick-action-btn" onClick={handleNewChat}><Plus size={13} /><span>New</span></button>
                    <button className="quick-action-btn" onClick={handleExportChat}><Download size={13} /><span>Export</span></button>
                    <button className="quick-action-btn" onClick={() => setLibraryOpen(true)}><FileText size={13} /><span>Library</span></button>
                    <button className="quick-action-btn" onClick={() => setSettingsOpen(true)}><Settings size={13} /><span>Settings</span></button>
                  </div>
                </>
              )}

              {col4Tab === 'memory' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div className="panel-section-label">Session Memory State</div>
                  <div className="glass-card" style={{ padding: '10px 12px' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>SESSION ID</div>
                    <div style={{ fontSize: '0.74rem', fontFamily: 'var(--font-mono)', color: 'var(--accent-cyan)', marginTop: '3px' }}>{currentSessionId}</div>
                  </div>
                  <div className="glass-card" style={{ padding: '10px 12px' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>STORED TURNS</div>
                    <div style={{ fontSize: '0.88rem', fontWeight: 700, color: 'var(--text-primary)', marginTop: '3px' }}>{currentSession?.messages.length ?? 0} messages</div>
                  </div>
                  <div className="glass-card" style={{ padding: '10px 12px' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>PRIVACY MODE</div>
                    <div style={{ fontSize: '0.74rem', color: settings.privacyMode ? 'var(--status-amber)' : 'var(--status-green)', fontWeight: 600, marginTop: '3px' }}>
                      {settings.privacyMode ? 'Ephemeral (No DB Persist)' : 'Active (Persisting to SQLite)'}
                    </div>
                  </div>
                </div>
              )}

              {col4Tab === 'files' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div className="panel-section-label">Attached Files</div>
                  {(() => {
                    const allAttachments = (currentSession?.messages || []).flatMap((m) => m.attachments || []);
                    if (allAttachments.length === 0) {
                      return (
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.72rem', textAlign: 'center', padding: '24px 8px' }}>
                          No files attached in this session.
                        </div>
                      );
                    }
                    return allAttachments.map((att, idx) => (
                      <div key={idx} className="glass-card" style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 10px' }}>
                        <FileText size={14} color="var(--accent-cyan)" />
                        <span style={{ fontSize: '0.72rem', color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {att.name || `File ${idx + 1}`}
                        </span>
                      </div>
                    ));
                  })()}
                  <button className="quick-action-btn" onClick={() => setLibraryOpen(true)} style={{ marginTop: 6 }}>
                    <FileText size={13} /><span>Open Media Library</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

      </div>

      {/* ── INTELLIGENCE STATUS BAR ── */}
      <div className="intelligence-feed">
        <span className="feed-label">⚡ Intelligence Feed</span>
        <span className="feed-divider" />
        <div className="feed-items">
          <div className={`feed-item ${isConnected ? 'ok' : 'warn'}`}>
            <span className="feed-item-dot" />
            {isConnected ? 'Brain Core Connected' : 'Brain Core Offline'}
          </div>
          <div className="feed-item info">
            <span className="feed-item-dot" />
            Engine: {settings.llmProvider.toUpperCase()} · {settings.model}
          </div>
          <div className="feed-item ok">
            <span className="feed-item-dot" />
            Conversations: {sessions.length} · Messages: {currentSession?.messages.length ?? 0}
          </div>
        </div>
      </div>

      {/* Canvas Drawer */}
      <CanvasDrawer item={activeCanvasItem} onClose={() => setActiveCanvasItem(null)} />

      {/* Settings Modal */}
      <SettingsModal
        isOpen={settingsOpen}
        settings={settings}
        initialTab={settingsTab}
        onClose={() => setSettingsOpen(false)}
        onSave={(newSettings) => setSettings(newSettings)}
      />

      {libraryOpen && (
        <MediaLibraryPanel
          wsUrl={settings.wsUrl}
          onClose={() => setLibraryOpen(false)}
          onSelect={(item) => { setLibraryItem(item); setLibraryOpen(false); }}
        />
      )}
    </div>
  );
};

export default App;
