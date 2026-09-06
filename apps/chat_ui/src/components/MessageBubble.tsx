import React, { Component, useState } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import remarkEmoji from 'remark-emoji';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import { motion } from 'framer-motion';
import type { Message, CanvasItem } from '../types/chat';
import { Sparkles, User, Copy, Check, ThumbsUp, ThumbsDown, RotateCcw, Volume2, VolumeX, ChevronDown, ChevronUp, Edit3, Sliders, Share2, Search, FileSpreadsheet, FileText, FileCode, ExternalLink, Table as TableIcon } from 'lucide-react';
import { wsClient } from '../services/wsClient';
import { CodeBlockRunner } from './CodeBlockRunner';
import { ImageViewerModal } from './ImageViewerModal';
import { MermaidChart } from './MermaidChart';
import { MediaStrip } from './MediaCard';
import { AgentActivityTimeline } from './AgentActivityTimeline';
import { ActionConfirmationCard } from './ActionConfirmationCard';

const MarkdownTable: React.FC<{ children?: ReactNode; [key: string]: any }> = ({ children, ...props }) => {
  const [copied, setCopied] = useState(false);
  const tableRef = React.useRef<HTMLTableElement>(null);

  const copyTable = () => {
    if (!tableRef.current) return;
    const table = tableRef.current;
    const rows = Array.from(table.querySelectorAll('tr'));
    if (!rows.length) return;

    // Convert to markdown table format
    const matrix: string[][] = rows.map((row) =>
      Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent?.trim().replace(/\|/g, '\\|') || '')
    );

    if (!matrix.length || !matrix[0].length) return;

    const colWidths = matrix[0].map((_, colIdx) =>
      Math.max(...matrix.map((row) => (row[colIdx] || '').length), 3)
    );

    let md = '';
    // Header
    const headerRow = matrix[0];
    md += '| ' + headerRow.map((cell, idx) => cell.padEnd(colWidths[idx])).join(' | ') + ' |\n';
    // Divider
    md += '| ' + colWidths.map((w) => '-'.repeat(w)).join(' | ') + ' |\n';
    // Data rows
    for (let r = 1; r < matrix.length; r++) {
      md += '| ' + matrix[r].map((cell, idx) => (cell || '').padEnd(colWidths[idx])).join(' | ') + ' |\n';
    }

    navigator.clipboard?.writeText(md.trim());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="gpt-table-card">
      <div className="gpt-table-header">
        <div className="gpt-table-title">
          <TableIcon size={14} className="gpt-table-icon" />
          <span>Data Table</span>
        </div>
        <button
          onClick={copyTable}
          className="gpt-table-copy-btn"
          title="Copy table as Markdown"
        >
          {copied ? <Check size={13} style={{ color: '#54c58a' }} /> : <Copy size={13} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <div className="gpt-table-scroll">
        <table ref={tableRef} {...props} className="gpt-table">
          {children}
        </table>
      </div>
    </div>
  );
};

interface MarkdownErrorBoundaryProps {
  fallbackText: string;
  children: ReactNode;
}

const extractTextFromReactNode = (node: any): string => {
  if (node === null || node === undefined) return '';
  if (typeof node === 'string') return node;
  if (typeof node === 'number' || typeof node === 'boolean') return String(node);
  if (Array.isArray(node)) {
    return node.map(extractTextFromReactNode).join('');
  }
  if (typeof node === 'object') {
    if (node.props && node.props.children !== undefined) {
      return extractTextFromReactNode(node.props.children);
    }
    if ('value' in node && typeof node.value === 'string') {
      return node.value;
    }
    if ('children' in node && Array.isArray(node.children)) {
      return node.children.map(extractTextFromReactNode).join('');
    }
  }
  return '';
};

interface MarkdownErrorBoundaryState {
  hasError: boolean;
}

class MarkdownErrorBoundary extends Component<MarkdownErrorBoundaryProps, MarkdownErrorBoundaryState> {
  state: MarkdownErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(_: Error): MarkdownErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.warn('[MarkdownRenderError] Fallback to raw text:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', color: 'var(--text-primary)' }}>
          {this.props.fallbackText}
        </div>
      );
    }
    return this.props.children;
  }
}

const getAgentBadgeStyle = (agentName?: string) => {
  const norm = (agentName || '').toLowerCase().trim();
  if (norm.includes('research')) {
    return {
      backgroundColor: 'rgba(0, 210, 255, 0.12)',
      color: '#00d2ff',
      border: '1px solid rgba(0, 210, 255, 0.3)',
    };
  }
  if (norm.includes('system')) {
    return {
      backgroundColor: 'rgba(0, 230, 118, 0.12)',
      color: '#00e676',
      border: '1px solid rgba(0, 230, 118, 0.3)',
    };
  }
  if (norm.includes('code')) {
    return {
      backgroundColor: 'rgba(168, 85, 247, 0.12)',
      color: '#c084fc',
      border: '1px solid rgba(168, 85, 247, 0.3)',
    };
  }
  if (norm.includes('browser')) {
    return {
      backgroundColor: 'rgba(255, 171, 0, 0.12)',
      color: '#ffb74d',
      border: '1px solid rgba(255, 171, 0, 0.3)',
    };
  }
  return {
    backgroundColor: 'rgba(255, 255, 255, 0.06)',
    color: 'var(--text-secondary)',
    border: '1px solid rgba(255, 255, 255, 0.15)',
  };
};

interface MessageBubbleProps {
  message: Message;
  onRegenerate?: (messageId: string) => void;
  onModifyResponse?: (messageId: string, instruction: string) => void;
  onStop?: (taskId?: string) => void;
  onApproveAction?: (taskId: string, action: string) => void;
  onRejectAction?: (taskId: string, action: string) => void;
  onEditMessage?: (messageId: string, newText: string) => void;
  onOpenCanvas?: (canvasItem: CanvasItem) => void;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  onRegenerate,
  onModifyResponse,
  onStop,
  onApproveAction,
  onRejectAction,
  onEditMessage,
  onOpenCanvas,
}) => {
  const isUser = message.sender === 'user';
  const [copied, setCopied] = useState(false);
  const [feedbackState, setFeedbackState] = useState<'up' | 'down' | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isThoughtOpen, setIsThoughtOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState(message.text);
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [showModifyMenu, setShowModifyMenu] = useState(false);

  const handleCopyText = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleFeedback = (isPositive: boolean) => {
    setFeedbackState(isPositive ? 'up' : 'down');
    wsClient.sendFeedback(message.id, isPositive);
  };

  // TTS Read Aloud
  const handleToggleSpeech = () => {
    if (isSpeaking) {
      window.speechSynthesis.cancel();
      setIsSpeaking(false);
    } else {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(message.text);
      utterance.rate = 1.0;
      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = () => setIsSpeaking(false);
      window.speechSynthesis.speak(utterance);
      setIsSpeaking(true);
    }
  };

  const handleSaveEdit = () => {
    if (editText.trim() && onEditMessage) {
      onEditMessage(message.id, editText.trim());
      setIsEditing(false);
    }
  };

  const handleGoogleCheck = () => {
    const query = encodeURIComponent(message.text.substring(0, 100));
    window.open(`https://www.google.com/search?q=${query}`, '_blank');
  };

  const handleShare = async () => {
    if (navigator.share) await navigator.share({ title: 'Makima response', text: message.text }).catch(() => undefined);
    else await navigator.clipboard?.writeText(message.text);
  };

  // Helper to extract YouTube video ID
  const getYouTubeId = (url: string): string | null => {
    const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|&v=)([^#&?]*).*/;
    const match = url.match(regExp);
    return match && match[2].length === 11 ? match[2] : null;
  };

  // Helper to extract Spotify Embed URL
  const getSpotifyEmbedUrl = (url: string): string | null => {
    try {
      const match = /open\.spotify\.com\/(track|album|playlist|artist)\/([a-zA-Z0-9]+)/.exec(url);
      if (match) {
        return `https://open.spotify.com/embed/${match[1]}/${match[2]}`;
      }
    } catch {}
    return null;
  };

  const isLongReport = !isUser && message.format === 'report' && message.text.length > 1500 && !message.isStreaming;
  const displayText = message.text;
  const getSafeUrl = (value: string | undefined): string | null => {
    if (!value) return null;
    if (value.startsWith('file://')) return value;
    try {
      const parsed = new URL(value, window.location.origin);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:' || parsed.protocol === 'blob:' || parsed.protocol === 'file:' || (parsed.protocol === 'data:' && parsed.pathname.startsWith('image/'))) return parsed.toString();
    } catch { /* Invalid media/link URLs render as a safe fallback. */ }
    return null;
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        gap: '16px',
        padding: '16px 24px',
        width: '100%',
        maxWidth: '860px',
        margin: '0 auto',
      }}
    >
      {/* Avatar */}
      <div
        style={{
          width: '36px',
          height: '36px',
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          background: isUser
            ? 'var(--bg-tertiary)'
            : 'linear-gradient(135deg, #4285f4 0%, #9b51e0 50%, #e91e63 100%)',
          color: '#ffffff',
          boxShadow: isUser ? 'none' : '0 2px 10px rgba(66, 133, 244, 0.3)',
        }}
      >
        {isUser ? <User size={20} /> : <Sparkles size={20} />}
      </div>

      {/* Message Content Container */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', fontWeight: 600 }}>
            {isUser ? 'You' : 'Makima AI'}
          </div>
          {!isUser && message.agent_name && (
            <span
              style={{
                fontSize: '0.65rem',
                fontWeight: 600,
                padding: '2px 8px',
                borderRadius: '10px',
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
                fontFamily: 'var(--font-mono)',
                ...getAgentBadgeStyle(message.agent_name),
              }}
            >
              {message.agent_name.replace(/_agent$/i, '').replace(/_/g, ' ')}
            </span>
          )}

          {/* Edit button for user message */}
          {isUser && !isEditing && (
            <button
              onClick={() => setIsEditing(true)}
              title="Edit message"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '4px',
                borderRadius: '4px',
                display: 'flex',
              }}
            >
              <Edit3 size={14} />
            </button>
          )}
        </div>

        {/* Attachments preview */}
        {message.attachments && message.attachments.length > 0 && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
            {message.attachments.map((att) => (
              <div
                key={att.id}
                style={{
                  padding: '6px 12px',
                  borderRadius: '12px',
                  backgroundColor: 'var(--bg-tertiary)',
                  fontSize: '0.85rem',
                  color: 'var(--accent-blue)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
              >
                📎 {att.name}
              </div>
            ))}
          </div>
        )}

        <MediaStrip items={message.media} />
        <AgentActivityTimeline events={message.agentActivity} />
        {message.sources?.length ? <div className="source-cards">{message.sources.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer"><strong>{source.title}</strong><span>{source.domain}</span></a>)}</div> : null}
        {message.actionConfirmation && (
          <ActionConfirmationCard
            {...message.actionConfirmation}
            onApprove={() => onApproveAction?.(message.taskId || message.id.replace(/^ai_/, ''), message.actionConfirmation!.action)}
            onReject={() => onRejectAction?.(message.taskId || message.id.replace(/^ai_/, ''), message.actionConfirmation!.action)}
          />
        )}
        {message.error && <div className="response-error-card"><strong>Response failed</strong><span>{message.error.message}</span>{message.error.retryable && onRegenerate && <button onClick={() => onRegenerate(message.id)}>Retry</button>}</div>}

        {/* Thinking Accordion (For AI messages) */}
        {!isUser && message.thought && (
          <div
            style={{
              margin: '0 0 12px 0',
              borderRadius: '12px',
              border: '1px solid var(--border-color)',
              backgroundColor: 'var(--bg-tertiary)',
              overflow: 'hidden',
              width: '100%',
            }}
          >
            <button
              onClick={() => setIsThoughtOpen(!isThoughtOpen)}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: 'transparent',
                border: 'none',
                color: 'var(--text-secondary)',
                fontSize: '0.82rem',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Sparkles size={14} color="var(--accent-blue)" />
                <span>Thinking Process</span>
              </div>
              {isThoughtOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {isThoughtOpen && (
              <div
                style={{
                  padding: '10px 14px',
                  borderTop: '1px solid var(--border-color)',
                  fontSize: '0.82rem',
                  color: 'var(--text-muted)',
                  fontStyle: 'italic',
                  lineHeight: 1.5,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {message.thought}
              </div>
            )}
          </div>
        )}

        {/* User Message Edit Mode */}
        {isEditing ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%', margin: '8px 0' }}>
            <textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              style={{
                width: '100%',
                padding: '10px',
                borderRadius: '12px',
                backgroundColor: 'var(--bg-tertiary)',
                border: '1px solid var(--accent-blue)',
                color: 'var(--text-primary)',
                outline: 'none',
                fontSize: '0.95rem',
                resize: 'vertical',
                minHeight: '60px',
              }}
            />
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setIsEditing(false)}
                style={{ padding: '6px 12px', borderRadius: '16px', border: '1px solid var(--border-color)', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '0.8rem' }}
              >
                Cancel
              </button>
              <button
                onClick={handleSaveEdit}
                style={{ padding: '6px 14px', borderRadius: '16px', border: 'none', background: 'var(--accent-blue)', color: '#000', fontWeight: 600, cursor: 'pointer', fontSize: '0.8rem' }}
              >
                Save & Submit
              </button>
            </div>
          </div>
        ) : (
          /* Message Content Body: Pill-shaped for User, Borderless for AI */
          <div
            style={{
              fontSize: '0.95rem',
              lineHeight: '1.6',
              padding: isUser ? '12px 16px' : '4px 0',
              borderRadius: isUser ? '18px 18px 4px 18px' : '0',
              backgroundColor: isUser ? 'var(--bg-tertiary)' : 'transparent',
              color: 'var(--text-primary)',
              maxWidth: '100%',
              wordBreak: 'break-word',
            }}
            className="markdown-content"
          >
            {!isUser && message.isStreaming && !message.text.trim() ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '6px 0', opacity: 0.7 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-teal)', display: 'inline-block' }} />
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-teal)', display: 'inline-block', opacity: 0.6 }} />
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-teal)', display: 'inline-block', opacity: 0.3 }} />
              </div>
            ) : (
              <MarkdownErrorBoundary fallbackText={message.text}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath, remarkEmoji]}
                  rehypePlugins={[rehypeHighlight, rehypeKatex]}
                  components={{
                  // Google & ChatGPT SOTA Table renderer
                  table(props: any) {
                    return <MarkdownTable {...props} />;
                  },
                  // Code block renderer with Run Code + Copy + Canvas + Mermaid
                  code({ node, inline, className, children, ...props }: any) {
                    const match = /language-([^\s]+)(?:\s+(.*))?/.exec(className || '');
                    const fullLang = match ? match[1].toLowerCase() : 'code';
                    const isMermaid = fullLang.startsWith('mermaid');
                    const subType = isMermaid ? (match && match[2] ? match[2].trim() : fullLang.replace(/^mermaid/i, '').trim()) : '';
                    const lang = isMermaid ? 'mermaid' : fullLang;
                    const rawCode = extractTextFromReactNode(children) || (node && node.value ? String(node.value) : '') || String(children || '');
                    const codeString = rawCode.replace(/\n$/, '');

                    if (!inline) {
                      if (lang === 'mermaid') {
                        if (message.isStreaming) {
                          return (
                            <pre
                              style={{
                                backgroundColor: 'rgba(0, 0, 0, 0.35)',
                                border: '1px solid rgba(0, 210, 255, 0.15)',
                                padding: '10px 14px',
                                borderRadius: '8px',
                                fontSize: '0.78rem',
                                color: 'var(--text-muted)',
                                fontFamily: 'monospace',
                                margin: '8px 0',
                              }}
                            >
                              <code>{codeString}</code>
                            </pre>
                          );
                        }
                        return <MermaidChart chart={codeString} subType={subType} />;
                      }
                      return (
                        <CodeBlockRunner
                          language={lang}
                          codeString={codeString}
                          onOpenCanvas={onOpenCanvas}
                        />
                      );
                    }

                    return (
                      <code
                        style={{
                          backgroundColor: 'rgba(255, 255, 255, 0.08)',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          fontSize: '0.88em',
                          fontFamily: 'monospace',
                        }}
                        {...props}
                      >
                        {extractTextFromReactNode(children) || children}
                      </code>
                    );
                  },
                  // Image renderer for Fullscreen Lightbox
                  img({ src, alt, ...props }: any) {
                    const safeSrc = getSafeUrl(src);
                    if (!safeSrc) return <span className="media-card-error">Image preview unavailable.</span>;
                    return (
                      <img
                        src={safeSrc}
                        alt={alt || 'Image'}
                        onClick={() => setSelectedImage(src)}
                        style={{
                          maxWidth: '100%',
                          borderRadius: '12px',
                          cursor: 'pointer',
                          margin: '8px 0',
                          boxShadow: '0 4px 16px rgba(0, 0, 0, 0.3)',
                        }}
                        {...props}
                      />
                    );
                  },
                  // Custom Link renderer for YouTube and Spotify embeds
                  a({ href, children, ...props }) {
                    if (href) {
                      const ytId = getYouTubeId(href);
                      if (ytId) {
                        return (
                          <div style={{ margin: '12px 0' }}>
                            <div className="youtube-embed-card">
                              <iframe
                                src={`https://www.youtube.com/embed/${ytId}`}
                                title="YouTube video player"
                                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                                allowFullScreen
                              />
                            </div>
                            <a
                              href={href}
                              target="_blank"
                              rel="noreferrer"
                              style={{ color: 'var(--accent-blue)', fontSize: '0.85rem', display: 'inline-flex', alignItems: 'center', gap: '4px', marginTop: '6px' }}
                            >
                              {children || 'Watch on YouTube'}
                            </a>
                          </div>
                        );
                      }
                      const spotifyEmbed = getSpotifyEmbedUrl(href);
                      if (spotifyEmbed) {
                        return (
                          <div style={{ margin: '12px 0' }}>
                            <div style={{ borderRadius: '12px', overflow: 'hidden', maxWidth: '480px', boxShadow: '0 4px 14px rgba(0,0,0,0.3)' }}>
                              <iframe
                                src={spotifyEmbed}
                                width="100%"
                                height="152"
                                frameBorder="0"
                                allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
                                loading="lazy"
                              />
                            </div>
                          </div>
                        );
                      }
                    }

                    if (href && (href.startsWith('file:') || /\.(xlsx|docx|pdf|pptx|csv|html|md|json|txt)$/i.test(href))) {
                      const cleanPath = href.replace(/^file:\/\/\/?/, '');
                      const fileName = cleanPath.split(/[/\\]/).pop() || href;
                      const ext = (fileName.split('.').pop() || '').toLowerCase();
                      
                      let badgeColor = 'rgba(59, 130, 246, 0.15)';
                      let borderColor = 'rgba(59, 130, 246, 0.35)';
                      let textColor = '#60a5fa';
                      let label = 'DOCUMENT';
                      let IconComponent = FileText;

                      if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') {
                        badgeColor = 'rgba(16, 185, 129, 0.15)';
                        borderColor = 'rgba(16, 185, 129, 0.35)';
                        textColor = '#34d399';
                        label = ext === 'csv' ? 'CSV DATA' : 'EXCEL SPREADSHEET';
                        IconComponent = FileSpreadsheet;
                      } else if (ext === 'pdf') {
                        badgeColor = 'rgba(239, 68, 68, 0.15)';
                        borderColor = 'rgba(239, 68, 68, 0.35)';
                        textColor = '#f87171';
                        label = 'PDF DOCUMENT';
                        IconComponent = FileText;
                      } else if (ext === 'docx' || ext === 'doc') {
                        badgeColor = 'rgba(37, 99, 235, 0.15)';
                        borderColor = 'rgba(37, 99, 235, 0.35)';
                        textColor = '#60a5fa';
                        label = 'WORD DOCUMENT';
                        IconComponent = FileText;
                      } else if (ext === 'pptx' || ext === 'ppt') {
                        badgeColor = 'rgba(245, 158, 11, 0.15)';
                        borderColor = 'rgba(245, 158, 11, 0.35)';
                        textColor = '#fbbf24';
                        label = 'POWERPOINT DECK';
                        IconComponent = FileSpreadsheet;
                      } else if (ext === 'html' || ext === 'md' || ext === 'json') {
                        badgeColor = 'rgba(139, 92, 246, 0.15)';
                        borderColor = 'rgba(139, 92, 246, 0.35)';
                        textColor = '#a78bfa';
                        label = ext.toUpperCase() + ' REPORT';
                        IconComponent = FileCode;
                      }

                      return (
                        <div
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '12px',
                            padding: '10px 14px',
                            margin: '8px 0',
                            borderRadius: '10px',
                            background: 'rgba(15, 23, 42, 0.65)',
                            border: `1px solid ${borderColor}`,
                            backdropFilter: 'blur(8px)',
                            boxShadow: '0 4px 14px rgba(0, 0, 0, 0.25)',
                            maxWidth: '100%',
                          }}
                        >
                          <div
                            style={{
                              width: '36px',
                              height: '36px',
                              borderRadius: '8px',
                              background: badgeColor,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              color: textColor,
                              flexShrink: 0,
                            }}
                          >
                            <IconComponent size={20} />
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                            <span style={{ fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', color: textColor }}>
                              {label}
                            </span>
                            <span style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {fileName}
                            </span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <button
                              onClick={() => handleCopyText(cleanPath)}
                              title="Copy File Path"
                              style={{
                                background: 'rgba(255, 255, 255, 0.08)',
                                border: 'none',
                                color: 'var(--text-muted)',
                                padding: '6px 8px',
                                borderRadius: '6px',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                                fontSize: '0.75rem',
                              }}
                            >
                              <Copy size={13} />
                              <span>Copy</span>
                            </button>
                            <button
                              onClick={() => wsClient.openFile(cleanPath)}
                              title="Open Document Natively on Desktop"
                              style={{
                                background: badgeColor,
                                color: textColor,
                                border: `1px solid ${borderColor}`,
                                padding: '6px 10px',
                                borderRadius: '6px',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                                fontSize: '0.75rem',
                                fontWeight: 600,
                              }}
                            >
                              <ExternalLink size={13} />
                              <span>Open</span>
                            </button>
                          </div>
                        </div>
                      );
                    }

                    const safeHref = getSafeUrl(href);
                    if (!safeHref) return <span>{children}</span>;
                    return (
                      <a
                        href={safeHref}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: 'var(--accent-blue)', textDecoration: 'underline' }}
                        {...props}
                      >
                        {children}
                      </a>
                    );
                  },
                }}
              >
                {displayText}
              </ReactMarkdown>
            </MarkdownErrorBoundary>
          )}

          {isLongReport && onOpenCanvas && (
            <div
              style={{
                marginTop: '12px',
                padding: '10px 14px',
                borderRadius: '8px',
                backgroundColor: 'rgba(0, 210, 255, 0.08)',
                border: '1px solid rgba(0, 210, 255, 0.25)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '10px',
              }}
            >
              <span style={{ fontSize: '0.8rem', color: 'var(--text-primary)', fontWeight: 500 }}>
                Executive research report • Available in Canvas workspace
              </span>
              <button
                onClick={() =>
                  onOpenCanvas({
                    id: message.taskId || message.id,
                    title: 'Executive Research Report',
                    language: 'markdown',
                    content: message.text,
                  })
                }
                style={{
                  background: 'var(--accent-teal)',
                  color: '#04060f',
                  border: 'none',
                  padding: '5px 12px',
                  borderRadius: '5px',
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                📖 Open in Canvas →
              </button>
            </div>
          )}

            {message.isStreaming && (
              <span
                style={{
                  display: 'inline-block',
                  width: '8px',
                  height: '16px',
                  backgroundColor: 'var(--accent-blue)',
                  marginLeft: '4px',
                  animation: 'pulse 1s infinite',
                }}
              />
            )}
          </div>
        )}

        {!isUser && message.isStreaming && onStop && <div className="streaming-stop-row"><button onClick={() => onStop(message.taskId || message.id)}><VolumeX size={14} /> Stop generating</button></div>}

        {/* Action Controls for AI messages - Authentic Gemini Toolbar */}
        {!isUser && !message.isStreaming && Boolean(message.text?.trim()) && (
          <div style={{ display: 'flex', gap: '6px', marginTop: '12px', alignItems: 'center', position: 'relative' }}>
            <button
              onClick={() => handleFeedback(true)}
              title="Good response"
              style={{
                background: 'transparent',
                border: 'none',
                color: feedbackState === 'up' ? '#4caf50' : 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <ThumbsUp size={15} />
            </button>

            <button
              onClick={() => handleFeedback(false)}
              title="Bad response"
              style={{
                background: 'transparent',
                border: 'none',
                color: feedbackState === 'down' ? '#f44336' : 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <ThumbsDown size={15} />
            </button>

            {/* Modify Response button (Sliders) */}
            <div style={{ position: 'relative' }}>
              <button
                onClick={() => setShowModifyMenu(!showModifyMenu)}
                title="Modify response"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: showModifyMenu ? 'var(--accent-blue)' : 'var(--text-muted)',
                  cursor: 'pointer',
                  padding: '6px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                <Sliders size={15} />
              </button>

              {showModifyMenu && (
                <div
                  style={{
                    position: 'absolute',
                    top: '32px',
                    left: 0,
                    backgroundColor: 'var(--bg-secondary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '12px',
                    padding: '8px 0',
                    zIndex: 10,
                    width: '160px',
                    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.3)',
                  }}
                >
                  {['Shorter', 'Longer', 'Simpler', 'More Casual', 'More Professional'].map((opt) => (
                    <button
                      key={opt}
                      onClick={() => {
                        setShowModifyMenu(false);
                        if (onModifyResponse) onModifyResponse(message.id, opt);
                        else if (onRegenerate) onRegenerate(message.id);
                      }}
                      style={{
                        width: '100%',
                        textAlign: 'left',
                        padding: '8px 16px',
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--text-primary)',
                        fontSize: '0.82rem',
                        cursor: 'pointer',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
                      onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Double-Check Response (Google Search) */}
            <button
              onClick={handleGoogleCheck}
              title="Double-check response on Google"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <Search size={15} />
            </button>

            {/* Share */}
            <button
              onClick={handleShare}
              title="Share response"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <Share2 size={15} />
            </button>

            {/* Copy */}
            <button
              type="button"
              onClick={() => handleCopyText(message.text)}
              title="Copy text"
              aria-label="Copy message text"
              style={{
                background: 'transparent',
                border: 'none',
                color: copied ? 'var(--accent-blue)' : 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              {copied ? <Check size={15} /> : <Copy size={15} />}
            </button>

            {/* Read Aloud TTS button */}
            <button
              type="button"
              onClick={handleToggleSpeech}
              title={isSpeaking ? 'Stop speaking' : 'Read Aloud'}
              aria-label={isSpeaking ? 'Stop read aloud' : 'Read message aloud'}
              aria-pressed={isSpeaking}
              style={{
                background: 'transparent',
                border: 'none',
                color: isSpeaking ? 'var(--accent-blue)' : 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              {isSpeaking ? <VolumeX size={15} /> : <Volume2 size={15} />}
            </button>

            {onRegenerate && (
              <button
                type="button"
                onClick={() => onRegenerate(message.id)}
                title="Regenerate response"
                aria-label="Regenerate response"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  padding: '6px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                <RotateCcw size={15} />
              </button>
            )}
          </div>
        )}
      </div>

      <ImageViewerModal imageUrl={selectedImage} onClose={() => setSelectedImage(null)} />
    </motion.div>
  );
};
