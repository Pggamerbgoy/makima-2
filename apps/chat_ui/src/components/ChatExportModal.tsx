import React, { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import {
  X,
  FileDown,
  Copy,
  Check,
  FileText,
  Code2,
} from 'lucide-react';
import type { ChatSession } from '../types/chat';

interface ChatExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  session: ChatSession | null;
  onToast: (message: string, level?: 'info' | 'success' | 'warning' | 'error') => void;
}

export const ChatExportModal: React.FC<ChatExportModalProps> = ({
  isOpen,
  onClose,
  session,
  onToast,
}) => {
  const [format, setFormat] = useState<'markdown' | 'json' | 'text'>('markdown');
  const [includeTimestamps, setIncludeTimestamps] = useState(true);
  const [includeThoughts, setIncludeThoughts] = useState(true);
  const [includeAgentNames, setIncludeAgentNames] = useState(true);
  const [copied, setCopied] = useState(false);

  // Generate exported content based on selected format and options
  const exportContent = useMemo(() => {
    if (!session || !session.messages.length) {
      return '# Empty Conversation\nNo messages to export.';
    }

    const title = session.title || 'Untitled Conversation';
    const dateStr = new Date(session.createdAt).toLocaleDateString();

    if (format === 'markdown') {
      let md = `# ${title}\n\n`;
      md += `*Exported from Makima Autonomous AI on ${dateStr}*\n\n---\n\n`;

      session.messages.forEach((msg) => {
        const senderLabel = msg.sender === 'user' ? 'User' : `Makima${includeAgentNames && msg.agent_name ? ` (${msg.agent_name})` : ''}`;
        const timeLabel = includeTimestamps ? ` · ${new Date(msg.timestamp).toLocaleTimeString()}` : '';

        md += `### 👤 ${senderLabel}${timeLabel}\n\n`;

        if (includeThoughts && msg.thought) {
          md += `> **Thinking Process:**\n> ${msg.thought.split('\n').join('\n> ')}\n\n`;
        }

        md += `${msg.text}\n\n`;

        if (msg.sources && msg.sources.length > 0) {
          md += `**Sources:**\n`;
          msg.sources.forEach((s) => {
            md += `- [${s.title}](${s.url}) (${s.domain})\n`;
          });
          md += '\n';
        }

        md += `---\n\n`;
      });

      return md;
    }

    if (format === 'json') {
      const sanitizedMessages = session.messages.map((m) => {
        const item: any = {
          id: m.id,
          sender: m.sender,
          text: m.text,
          timestamp: m.timestamp,
        };
        if (includeTimestamps) item.timeString = new Date(m.timestamp).toISOString();
        if (includeAgentNames && m.agent_name) item.agent_name = m.agent_name;
        if (includeThoughts && m.thought) item.thought = m.thought;
        if (m.sources && m.sources.length) item.sources = m.sources;
        if (m.attachments && m.attachments.length) {
          item.attachments = m.attachments.map((a) => ({ name: a.name, mimeType: a.mimeType, size: a.size }));
        }
        return item;
      });

      const payload = {
        title,
        createdAt: session.createdAt,
        exportedAt: Date.now(),
        messageCount: session.messages.length,
        messages: sanitizedMessages,
      };

      return JSON.stringify(payload, null, 2);
    }

    // Plain Text format
    let text = `=== ${title} ===\nExported: ${dateStr}\n\n`;
    session.messages.forEach((msg) => {
      const sender = msg.sender === 'user' ? 'USER' : `MAKIMA${includeAgentNames && msg.agent_name ? ` [${msg.agent_name}]` : ''}`;
      const time = includeTimestamps ? ` [${new Date(msg.timestamp).toLocaleTimeString()}]` : '';
      text += `[${sender}]${time}:\n`;
      if (includeThoughts && msg.thought) {
        text += `[Thought]: ${msg.thought}\n`;
      }
      text += `${msg.text}\n\n-------------------------\n\n`;
    });
    return text;
  }, [session, format, includeTimestamps, includeThoughts, includeAgentNames]);

  // Statistics
  const stats = useMemo(() => {
    const lines = exportContent.split('\n').length;
    const words = exportContent.trim().split(/\s+/).filter(Boolean).length;
    const chars = exportContent.length;
    return { lines, words, chars };
  }, [exportContent]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(exportContent);
      setCopied(true);
      onToast('Conversation copied to clipboard', 'success');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      onToast('Failed to copy to clipboard', 'error');
    }
  };

  const handleDownload = () => {
    if (!session) return;
    const ext = format === 'markdown' ? 'md' : format === 'json' ? 'json' : 'txt';
    const cleanTitle = (session.title || 'makima_chat').replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
    const dateStr = new Date().toISOString().slice(0, 10);
    const filename = `${cleanTitle}_${dateStr}.${ext}`;

    const mimeType = format === 'json' ? 'application/json' : 'text/plain;charset=utf-8';
    const blob = new Blob([exportContent], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    onToast(`Exported as ${filename}`, 'success');
  };

  if (!isOpen) return null;

  return (
    <motion.div
      className="export-modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
      onClick={onClose}
    >
      <motion.div
        className="export-modal"
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 4 }}
        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="export-modal-header">
          <div className="export-header-title-box">
            <div className="export-header-gem">
              <FileDown size={18} />
            </div>
            <div>
              <h2 className="export-modal-title">Export Conversation</h2>
              <p className="export-modal-subtitle">Save your chat history, code, and agent reasoning logs</p>
            </div>
          </div>
          <button className="export-close-btn" onClick={onClose} aria-label="Close export modal">
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="export-modal-body">
          {/* Format Selector Pills */}
          <div className="export-format-selector">
            <button
              type="button"
              className={`export-format-tab ${format === 'markdown' ? 'active' : ''}`}
              onClick={() => setFormat('markdown')}
            >
              <FileText size={15} />
              <span>Markdown (.md)</span>
            </button>
            <button
              type="button"
              className={`export-format-tab ${format === 'json' ? 'active' : ''}`}
              onClick={() => setFormat('json')}
            >
              <Code2 size={15} />
              <span>JSON (.json)</span>
            </button>
            <button
              type="button"
              className={`export-format-tab ${format === 'text' ? 'active' : ''}`}
              onClick={() => setFormat('text')}
            >
              <FileText size={15} />
              <span>Plain Text (.txt)</span>
            </button>
          </div>

          {/* Options Checkboxes */}
          <div className="export-options-bar">
            <label className="export-option-label">
              <input
                type="checkbox"
                checked={includeTimestamps}
                onChange={(e) => setIncludeTimestamps(e.target.checked)}
              />
              <span>Include timestamps</span>
            </label>
            <label className="export-option-label">
              <input
                type="checkbox"
                checked={includeThoughts}
                onChange={(e) => setIncludeThoughts(e.target.checked)}
              />
              <span>Include agent reasoning / thoughts</span>
            </label>
            <label className="export-option-label">
              <input
                type="checkbox"
                checked={includeAgentNames}
                onChange={(e) => setIncludeAgentNames(e.target.checked)}
              />
              <span>Include agent tags</span>
            </label>
          </div>

          {/* Stats Badges */}
          <div className="export-stats-row">
            <span className="export-stat-badge">{stats.lines} lines</span>
            <span className="export-stat-badge">{stats.words} words</span>
            <span className="export-stat-badge">{stats.chars} characters</span>
            <span className="export-stat-badge">{session?.messages.length || 0} messages</span>
          </div>

          {/* Live Preview Container */}
          <div className="export-preview-container">
            <pre className="export-preview-content">
              <code>{exportContent}</code>
            </pre>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="export-modal-footer">
          <button className="export-btn secondary" onClick={handleCopy}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
            <span>{copied ? 'Copied!' : 'Copy to Clipboard'}</span>
          </button>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="export-btn outline" onClick={onClose}>
              Cancel
            </button>
            <button className="export-btn primary" onClick={handleDownload}>
              <FileDown size={15} />
              <span>Download File</span>
            </button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
};

export default ChatExportModal;
