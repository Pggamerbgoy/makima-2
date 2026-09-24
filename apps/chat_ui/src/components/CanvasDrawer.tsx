import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { X, Copy, Download, Code, Eye, Check, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import remarkEmoji from 'remark-emoji';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import type { CanvasItem } from '../types/chat';
import { MarkdownErrorBoundary, buildCanvasMarkdownComponents } from './markdownShared';

interface CanvasDrawerProps {
  item: CanvasItem | null;
  onClose: () => void;
}

export const CanvasDrawer: React.FC<CanvasDrawerProps> = ({ item, onClose }) => {
  const [activeTab, setActiveTab] = useState<'preview' | 'code' | 'report'>('preview');
  const [copied, setCopied] = useState(false);

  const isHtmlWeb = item ? (item.language === 'html' || item.language === 'xml' || item.content.includes('<html') || item.content.includes('<svg')) : false;
  const isMarkdown = item ? (item.language === 'markdown' || item.language === 'report' || item.language === 'md') : false;

  useEffect(() => {
    if (item) {
      if (item.language === 'markdown' || item.language === 'report' || item.language === 'md') {
        setActiveTab('report');
      } else if (item.language === 'html' || item.language === 'xml' || item.content.includes('<html') || item.content.includes('<svg')) {
        setActiveTab('preview');
      } else {
        setActiveTab('code');
      }
    }
  }, [item?.id, item?.language]);

  if (!item) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(item.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    const extMap: Record<string, string> = {
      python: 'py',
      javascript: 'js',
      typescript: 'ts',
      html: 'html',
      css: 'css',
      json: 'json',
      markdown: 'md',
      report: 'md',
      md: 'md',
    };
    const ext = extMap[item.language.toLowerCase()] || 'txt';
    const blob = new Blob([item.content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${item.title.replace(/\s+/g, '_').toLowerCase()}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <motion.div
      className="canvas-drawer"
      initial={{ x: 40, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 40, opacity: 0 }}
      transition={{ type: 'spring', stiffness: 380, damping: 34 }}
      style={{
        width: '50%',
        maxWidth: '720px',
        minWidth: '380px',
        height: '100%',
        backgroundColor: 'var(--bg-surface)',
        borderLeft: '1px solid var(--border-subtle)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 50,
        boxShadow: 'var(--shadow-lg)',
        position: 'relative',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '0 18px',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'var(--bg-surface-elevated)',
          height: '52px',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flex: 1 }}>
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--primary-subtle)',
              border: '1px solid var(--primary-border)',
              color: 'var(--primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {isMarkdown ? <FileText size={15} /> : <Code size={15} />}
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <h3 style={{ fontSize: '0.86rem', fontWeight: 600, color: 'var(--text-primary)', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.title}</h3>
            <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{item.language}</span>
          </div>
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          {isMarkdown && (
            <div
              style={{
                display: 'flex',
                backgroundColor: 'var(--bg-surface)',
                padding: '2px',
                borderRadius: 'var(--radius-full)',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <button
                onClick={() => setActiveTab('report')}
                style={{
                  border: 'none',
                  padding: '4px 10px',
                  borderRadius: 'var(--radius-full)',
                  fontSize: '0.74rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'report' ? 'var(--bg-surface-elevated)' : 'transparent',
                  color: activeTab === 'report' ? 'var(--primary)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '5px',
                  fontWeight: activeTab === 'report' ? 600 : 400,
                  boxShadow: activeTab === 'report' ? 'var(--shadow-sm)' : 'none',
                }}
              >
                <FileText size={12} /> Report
              </button>
              <button
                onClick={() => setActiveTab('code')}
                style={{
                  border: 'none',
                  padding: '4px 10px',
                  borderRadius: 'var(--radius-full)',
                  fontSize: '0.74rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'code' ? 'var(--bg-surface-elevated)' : 'transparent',
                  color: activeTab === 'code' ? 'var(--primary)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '5px',
                  fontWeight: activeTab === 'code' ? 600 : 400,
                  boxShadow: activeTab === 'code' ? 'var(--shadow-sm)' : 'none',
                }}
              >
                <Code size={12} /> Source
              </button>
            </div>
          )}

          {isHtmlWeb && (
            <div
              style={{
                display: 'flex',
                backgroundColor: 'var(--bg-surface)',
                padding: '2px',
                borderRadius: 'var(--radius-full)',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <button
                onClick={() => setActiveTab('preview')}
                style={{
                  border: 'none',
                  padding: '4px 10px',
                  borderRadius: 'var(--radius-full)',
                  fontSize: '0.74rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'preview' ? 'var(--bg-surface-elevated)' : 'transparent',
                  color: activeTab === 'preview' ? 'var(--primary)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '5px',
                  fontWeight: activeTab === 'preview' ? 600 : 400,
                  boxShadow: activeTab === 'preview' ? 'var(--shadow-sm)' : 'none',
                }}
              >
                <Eye size={12} /> Preview
              </button>
              <button
                onClick={() => setActiveTab('code')}
                style={{
                  border: 'none',
                  padding: '4px 10px',
                  borderRadius: 'var(--radius-full)',
                  fontSize: '0.74rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'code' ? 'var(--bg-surface-elevated)' : 'transparent',
                  color: activeTab === 'code' ? 'var(--primary)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '5px',
                  fontWeight: activeTab === 'code' ? 600 : 400,
                  boxShadow: activeTab === 'code' ? 'var(--shadow-sm)' : 'none',
                }}
              >
                <Code size={12} /> Code
              </button>
            </div>
          )}

          <button
            onClick={handleCopy}
            title="Copy content"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              color: copied ? 'var(--success)' : 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '6px 8px',
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              fontSize: '0.75rem',
              gap: '4px',
            }}
          >
            {copied ? <Check size={14} color="var(--success)" /> : <Copy size={14} />}
          </button>

          <button
            onClick={handleDownload}
            title="Download file"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '6px 8px',
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <Download size={14} />
          </button>

          <button
            onClick={onClose}
            title="Close Canvas"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              padding: '6px 8px',
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <X size={15} />
          </button>
        </div>
      </div>

      {/* Main Viewport */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {isMarkdown && activeTab === 'report' ? (
          <div
            className="markdown-content markdown-prose canvas-report"
            style={{
              width: '100%',
              height: '100%',
              overflow: 'auto',
              padding: '28px 32px 40px',
              backgroundColor: 'var(--bg-canvas)',
              lineHeight: 1.75,
            }}
          >
            <MarkdownErrorBoundary fallbackText={item.content}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath, remarkEmoji]}
                rehypePlugins={[rehypeHighlight, rehypeKatex]}
                components={buildCanvasMarkdownComponents({
                  fallbackText: item.content,
                  onOpenCanvas: (next) => {
                    // Replace drawer content when a nested snippet asks to open in canvas
                    window.dispatchEvent(
                      new CustomEvent('makima:canvas-replace', { detail: next })
                    );
                  },
                })}
              >
                {item.content}
              </ReactMarkdown>
            </MarkdownErrorBoundary>
          </div>
        ) : isHtmlWeb && activeTab === 'preview' ? (
          <iframe
            srcDoc={item.content}
            title="Canvas Live Preview"
            sandbox="allow-scripts"
            style={{ width: '100%', height: '100%', border: 'none', backgroundColor: '#ffffff' }}
          />
        ) : (
          <pre
            style={{
              width: '100%',
              height: '100%',
              overflow: 'auto',
              margin: 0,
              padding: '18px 20px',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.84rem',
              lineHeight: 1.6,
              color: 'var(--text-primary)',
              backgroundColor: 'var(--code-bg)',
            }}
          >
            <code>{item.content}</code>
          </pre>
        )}
      </div>
    </motion.div>
  );
};
