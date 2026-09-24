import React, { useState, useEffect, useRef, useMemo } from 'react';
import { motion } from 'framer-motion';
import {
  Search,
  MessageSquare,
  Plus,
  FileDown,
  Mic,
  Sun,
  Moon,
  Sparkles,
  Layers,
  FolderOpen,
  Activity,
  Code,
  Globe,
  Monitor,
  Lightbulb,
  Pin,
  ArrowRight,
  CornerDownLeft,
} from 'lucide-react';
import type { ChatSession } from '../types/chat';

export interface CommandPaletteItem {
  id: string;
  category: 'Conversations' | 'Actions' | 'Agent Modes' | 'System';
  title: string;
  subtitle?: string;
  icon: React.ReactNode;
  shortcut?: string;
  action: () => void;
}

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  sessions: ChatSession[];
  currentSessionId: string;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onOpenExport: () => void;
  onToggleVoice: () => void;
  onToggleTheme: () => void;
  isDarkTheme: boolean;
  onOpenModels: () => void;
  onOpenConnectors: () => void;
  onOpenMediaLibrary: () => void;
  onOpenTelemetry: () => void;
  onOpenPinned: () => void;
  onSelectAgentMode?: (mode: string) => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  isOpen,
  onClose,
  sessions,
  currentSessionId,
  onSelectSession,
  onNewChat,
  onOpenExport,
  onToggleVoice,
  onToggleTheme,
  isDarkTheme,
  onOpenModels,
  onOpenConnectors,
  onOpenMediaLibrary,
  onOpenTelemetry,
  onOpenPinned,
  onSelectAgentMode,
}) => {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Focus input on open
  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const items = useMemo<CommandPaletteItem[]>(() => {
    const list: CommandPaletteItem[] = [
      // Core Actions
      {
        id: 'action_new_chat',
        category: 'Actions',
        title: 'New Conversation',
        subtitle: 'Start a fresh conversation thread',
        icon: <Plus size={16} color="var(--primary)" />,
        shortcut: '⌘K / Ctrl+K',
        action: () => {
          onNewChat();
          onClose();
        },
      },
      {
        id: 'action_export_chat',
        category: 'Actions',
        title: 'Export Conversation',
        subtitle: 'Export current chat as Markdown, JSON, or Text',
        icon: <FileDown size={16} color="var(--accent-blue)" />,
        shortcut: 'Export',
        action: () => {
          onOpenExport();
          onClose();
        },
      },
      {
        id: 'action_pinned_messages',
        category: 'Actions',
        title: 'Pinned Highlights',
        subtitle: 'View saved code snippets and key takeaways',
        icon: <Pin size={16} color="var(--accent-amber)" />,
        action: () => {
          onOpenPinned();
          onClose();
        },
      },
      {
        id: 'action_voice_mode',
        category: 'Actions',
        title: 'Toggle Voice Mode',
        subtitle: 'Activate hands-free speech conversation',
        icon: <Mic size={16} color="var(--accent-emerald)" />,
        action: () => {
          onToggleVoice();
          onClose();
        },
      },
      {
        id: 'action_models',
        category: 'Actions',
        title: 'Select AI Model & Provider',
        subtitle: 'Switch between Gemini, OpenAI, Groq, Ollama, etc.',
        icon: <Sparkles size={16} color="var(--primary)" />,
        action: () => {
          onOpenModels();
          onClose();
        },
      },
      {
        id: 'action_telemetry',
        category: 'System',
        title: 'Makima Brain Telemetry & Health',
        subtitle: 'Real-time WebSocket latency, agent fleet, and memory status',
        icon: <Activity size={16} color="var(--accent-emerald)" />,
        action: () => {
          onOpenTelemetry();
          onClose();
        },
      },
      {
        id: 'action_theme',
        category: 'System',
        title: `Switch to ${isDarkTheme ? 'Light' : 'Dark'} Mode`,
        subtitle: 'Toggle user interface appearance',
        icon: isDarkTheme ? <Sun size={16} color="var(--accent-amber)" /> : <Moon size={16} color="var(--accent-indigo)" />,
        action: () => {
          onToggleTheme();
          onClose();
        },
      },
      {
        id: 'action_connectors',
        category: 'System',
        title: 'Active Agent Connectors',
        subtitle: 'Configure system, web, and media tool capabilities',
        icon: <Layers size={16} color="var(--accent-blue)" />,
        action: () => {
          onOpenConnectors();
          onClose();
        },
      },
      {
        id: 'action_media_library',
        category: 'System',
        title: 'Media Library',
        subtitle: 'Browse generated charts, artifacts, and uploads',
        icon: <FolderOpen size={16} color="var(--accent-purple)" />,
        action: () => {
          onOpenMediaLibrary();
          onClose();
        },
      },

      // Agent Modes
      {
        id: 'agent_code',
        category: 'Agent Modes',
        title: 'Code Agent Mode',
        subtitle: 'Full-stack software engineering, debugging, and refactoring',
        icon: <Code size={16} color="var(--accent-purple)" />,
        action: () => {
          if (onSelectAgentMode) onSelectAgentMode('coder');
          onClose();
        },
      },
      {
        id: 'agent_research',
        category: 'Agent Modes',
        title: 'Research Agent Mode',
        subtitle: 'Deep multi-source web discovery & fact-checked briefing',
        icon: <Globe size={16} color="var(--accent-blue)" />,
        action: () => {
          if (onSelectAgentMode) onSelectAgentMode('researcher');
          onClose();
        },
      },
      {
        id: 'agent_system',
        category: 'Agent Modes',
        title: 'System Agent Mode',
        subtitle: 'Windows OS operations, app automation, and file organization',
        icon: <Monitor size={16} color="var(--accent-emerald)" />,
        action: () => {
          if (onSelectAgentMode) onSelectAgentMode('system');
          onClose();
        },
      },
      {
        id: 'agent_creative',
        category: 'Agent Modes',
        title: 'Creative Agent Mode',
        subtitle: 'Design ideation, copywriting, storytelling, and prompts',
        icon: <Lightbulb size={16} color="var(--accent-amber)" />,
        action: () => {
          if (onSelectAgentMode) onSelectAgentMode('creative');
          onClose();
        },
      },

      // Recent Sessions
      ...sessions.map((s) => ({
        id: `session_${s.id}`,
        category: 'Conversations' as const,
        title: s.title || 'Untitled Conversation',
        subtitle: `${s.messages.length} messages${s.id === currentSessionId ? ' · Current chat' : ''}`,
        icon: <MessageSquare size={16} color={s.id === currentSessionId ? 'var(--primary)' : 'var(--text-muted)'} />,
        action: () => {
          onSelectSession(s.id);
          onClose();
        },
      })),
    ];

    return list;
  }, [
    sessions,
    currentSessionId,
    isDarkTheme,
    onNewChat,
    onOpenExport,
    onOpenPinned,
    onToggleVoice,
    onOpenModels,
    onOpenTelemetry,
    onToggleTheme,
    onOpenConnectors,
    onOpenMediaLibrary,
    onSelectAgentMode,
    onSelectSession,
    onClose,
  ]);

  // Filter items based on user search query
  const filteredItems = useMemo(() => {
    if (!query.trim()) return items;
    const lower = query.toLowerCase().trim();
    return items.filter(
      (item) =>
        item.title.toLowerCase().includes(lower) ||
        (item.subtitle && item.subtitle.toLowerCase().includes(lower)) ||
        item.category.toLowerCase().includes(lower)
    );
  }, [items, query]);

  // Keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1 < filteredItems.length ? prev + 1 : 0));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 >= 0 ? prev - 1 : filteredItems.length - 1));
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const selected = filteredItems[selectedIndex];
      if (selected) {
        selected.action();
      }
    }
  };

  // Scroll active item into view
  useEffect(() => {
    if (listRef.current) {
      const activeEl = listRef.current.querySelector('.command-palette-item.active') as HTMLElement;
      if (activeEl) {
        activeEl.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [selectedIndex]);

  if (!isOpen) return null;

  return (
    <motion.div
      className="command-palette-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
      onClick={onClose}
    >
      <motion.div
        className="command-palette-modal"
        initial={{ opacity: 0, y: -10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -6, scale: 0.98 }}
        transition={{ type: 'spring', stiffness: 420, damping: 32 }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* Search Bar Header */}
        <div className="command-palette-header">
          <Search size={18} className="command-palette-search-icon" />
          <input
            ref={inputRef}
            type="text"
            className="command-palette-input"
            placeholder="Search conversations, agents, or type a command..."
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
          />
          <span className="command-palette-badge">ESC to close</span>
        </div>

        {/* Results List */}
        <div className="command-palette-list" ref={listRef}>
          {filteredItems.length === 0 ? (
            <div className="command-palette-empty">
              No matching commands or conversations found for "{query}"
            </div>
          ) : (
            filteredItems.map((item, index) => {
              const isSelected = index === selectedIndex;
              return (
                <div
                  key={item.id}
                  className={`command-palette-item ${isSelected ? 'active' : ''}`}
                  onMouseEnter={() => setSelectedIndex(index)}
                  onClick={() => item.action()}
                >
                  <div className="command-item-icon-box">{item.icon}</div>
                  <div className="command-item-text">
                    <div className="command-item-title-row">
                      <span className="command-item-title">{item.title}</span>
                      <span className="command-item-category">{item.category}</span>
                    </div>
                    {item.subtitle && <span className="command-item-subtitle">{item.subtitle}</span>}
                  </div>
                  {item.shortcut ? (
                    <span className="command-item-shortcut">{item.shortcut}</span>
                  ) : isSelected ? (
                    <CornerDownLeft size={13} className="command-item-enter-hint" />
                  ) : (
                    <ArrowRight size={13} style={{ opacity: 0.2 }} />
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer Navigation Bar */}
        <div className="command-palette-footer">
          <div className="command-footer-hints">
            <span><kbd>↑</kbd> <kbd>↓</kbd> Navigate</span>
            <span><kbd>↵</kbd> Select</span>
            <span><kbd>esc</kbd> Dismiss</span>
          </div>
          <span className="command-footer-brand">Makima Quick Launch</span>
        </div>
      </motion.div>
    </motion.div>
  );
};

export default CommandPalette;
