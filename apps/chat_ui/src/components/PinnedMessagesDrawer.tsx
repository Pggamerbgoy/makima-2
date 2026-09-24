import React from 'react';
import { motion } from 'framer-motion';
import {
  X,
  Pin,
  PinOff,
  Copy,
  Check,
  ArrowDownRight,
} from 'lucide-react';
import type { Message } from '../types/chat';

interface PinnedMessagesDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  pinnedMessages: Message[];
  onUnpin: (messageId: string) => void;
  onJumpToMessage: (messageId: string) => void;
  onToast: (message: string, level?: 'info' | 'success' | 'warning' | 'error') => void;
}

export const PinnedMessagesDrawer: React.FC<PinnedMessagesDrawerProps> = ({
  isOpen,
  onClose,
  pinnedMessages,
  onUnpin,
  onJumpToMessage,
  onToast,
}) => {
  const [copiedId, setCopiedId] = React.useState<string | null>(null);

  if (!isOpen) return null;

  const handleCopy = (msg: Message) => {
    navigator.clipboard.writeText(msg.text);
    setCopiedId(msg.id);
    onToast('Pinned snippet copied', 'success');
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <motion.div
      className="pinned-drawer-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
      onClick={onClose}
    >
      <motion.div
        className="pinned-drawer"
        initial={{ opacity: 0, x: 30 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 20 }}
        transition={{ type: 'spring', stiffness: 380, damping: 32 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Drawer Header */}
        <div className="pinned-drawer-header">
          <div className="pinned-header-title-box">
            <div className="pinned-header-icon-box">
              <Pin size={16} color="var(--accent-amber)" />
            </div>
            <div>
              <h3 className="pinned-drawer-title">Pinned Highlights</h3>
              <span className="pinned-drawer-count">{pinnedMessages.length} saved item{pinnedMessages.length === 1 ? '' : 's'}</span>
            </div>
          </div>
          <button className="pinned-close-btn" onClick={onClose} aria-label="Close pinned drawer">
            <X size={17} />
          </button>
        </div>

        {/* Drawer Body */}
        <div className="pinned-drawer-body">
          {pinnedMessages.length === 0 ? (
            <div className="pinned-empty-state">
              <div className="pinned-empty-icon-box">
                <Pin size={24} />
              </div>
              <h4 className="pinned-empty-title">No pinned messages yet</h4>
              <p className="pinned-empty-desc">
                Save key code blocks, architecture decisions, and answers by clicking the pin icon on any message toolbar.
              </p>
            </div>
          ) : (
            <div className="pinned-list">
              {pinnedMessages.map((msg) => (
                <div key={msg.id} className="pinned-card">
                  <div className="pinned-card-header">
                    <span className="pinned-sender-tag">
                      {msg.sender === 'user' ? 'You' : `Makima${msg.agent_name ? ` · ${msg.agent_name}` : ''}`}
                    </span>
                    <span className="pinned-timestamp">
                      {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>

                  <div className="pinned-card-preview">
                    {msg.text.length > 300 ? `${msg.text.slice(0, 300)}...` : msg.text}
                  </div>

                  <div className="pinned-card-actions">
                    <button
                      type="button"
                      className="pinned-action-btn"
                      onClick={() => handleCopy(msg)}
                      title="Copy pinned snippet"
                    >
                      {copiedId === msg.id ? <Check size={13} color="var(--accent-emerald)" /> : <Copy size={13} />}
                      <span>{copiedId === msg.id ? 'Copied' : 'Copy'}</span>
                    </button>

                    <button
                      type="button"
                      className="pinned-action-btn"
                      onClick={() => {
                        onJumpToMessage(msg.id);
                        onClose();
                      }}
                      title="Jump to message in conversation"
                    >
                      <ArrowDownRight size={13} />
                      <span>Jump</span>
                    </button>

                    <button
                      type="button"
                      className="pinned-action-btn danger"
                      onClick={() => onUnpin(msg.id)}
                      title="Unpin message"
                    >
                      <PinOff size={13} />
                      <span>Unpin</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
};

export default PinnedMessagesDrawer;
