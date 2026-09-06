import React, { useState, useEffect } from 'react';
import { X, Copy, Download, Code, Eye, Check, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import remarkEmoji from 'remark-emoji';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import type { CanvasItem } from '../types/chat';

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
    <div
      className="canvas-drawer"
      style={{
        width: '50%',
        maxWidth: '720px',
        minWidth: '380px',
        height: '100%',
        backgroundColor: 'var(--bg-secondary)',
        borderLeft: '1px solid rgba(0, 180, 220, 0.18)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 50,
        boxShadow: '-6px 0 30px rgba(0, 0, 0, 0.7)',
        position: 'relative',
      }}
    >
      {/* Top corner accent */}
      <div style={{ position: 'absolute', top: 0, left: 0, width: 16, height: 16, borderTop: '1px solid rgba(0,210,255,0.6)', borderLeft: '1px solid rgba(0,210,255,0.6)', pointerEvents: 'none' }} />

      {/* Header */}
      <div
        style={{
          padding: '10px 16px',
          borderBottom: '1px solid rgba(0, 180, 220, 0.12)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'rgba(0, 0, 0, 0.3)',
          height: '44px',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div style={{ width: 26, height: 26, borderRadius: '3px', backgroundColor: 'rgba(0, 210, 255, 0.1)', border: '1px solid rgba(0, 210, 255, 0.22)', color: 'var(--accent-teal)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {isMarkdown ? <FileText size={14} /> : <Code size={14} />}
          </div>
          <div>
            <h3 style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{item.title}</h3>
            <span style={{ fontSize: '0.58rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{item.language}</span>
          </div>
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {isMarkdown && (
            <div style={{ display: 'flex', backgroundColor: 'rgba(0,0,0,0.25)', padding: '2px', borderRadius: '3px', border: '1px solid rgba(0, 180, 220, 0.12)' }}>
              <button
                onClick={() => setActiveTab('report')}
                style={{
                  border: 'none',
                  padding: '3px 8px',
                  borderRadius: '2px',
                  fontSize: '0.72rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'report' ? 'rgba(0, 210, 255, 0.12)' : 'transparent',
                  color: activeTab === 'report' ? 'var(--accent-teal)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontWeight: activeTab === 'report' ? 600 : 400,
                }}
              >
                <FileText size={12} /> Report
              </button>
              <button
                onClick={() => setActiveTab('code')}
                style={{
                  border: 'none',
                  padding: '3px 8px',
                  borderRadius: '2px',
                  fontSize: '0.72rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'code' ? 'rgba(0, 210, 255, 0.12)' : 'transparent',
                  color: activeTab === 'code' ? 'var(--accent-teal)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontWeight: activeTab === 'code' ? 600 : 400,
                }}
              >
                <Code size={12} /> Source
              </button>
            </div>
          )}

          {isHtmlWeb && (
            <div style={{ display: 'flex', backgroundColor: 'rgba(0,0,0,0.25)', padding: '2px', borderRadius: '3px', border: '1px solid rgba(0, 180, 220, 0.12)' }}>
              <button
                onClick={() => setActiveTab('preview')}
                style={{
                  border: 'none',
                  padding: '3px 8px',
                  borderRadius: '2px',
                  fontSize: '0.72rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'preview' ? 'rgba(0, 210, 255, 0.12)' : 'transparent',
                  color: activeTab === 'preview' ? 'var(--accent-teal)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontWeight: activeTab === 'preview' ? 600 : 400,
                }}
              >
                <Eye size={12} /> Preview
              </button>
              <button
                onClick={() => setActiveTab('code')}
                style={{
                  border: 'none',
                  padding: '3px 8px',
                  borderRadius: '2px',
                  fontSize: '0.72rem',
                  cursor: 'pointer',
                  backgroundColor: activeTab === 'code' ? 'rgba(0, 210, 255, 0.12)' : 'transparent',
                  color: activeTab === 'code' ? 'var(--accent-teal)' : 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontWeight: activeTab === 'code' ? 600 : 400,
                }}
              >
                <Code size={12} /> Code
              </button>
            </div>
          )}

          <button
            onClick={handleCopy}
            title="Copy content"
            style={{ background: 'transparent', border: '1px solid rgba(0, 180, 220, 0.12)', color: copied ? 'var(--accent-teal)' : 'var(--text-muted)', cursor: 'pointer', padding: '4px 6px', borderRadius: '3px', display: 'flex', alignItems: 'center' }}
          >
            {copied ? <Check size={13} color="var(--accent-teal)" /> : <Copy size={13} />}
          </button>

          <button
            onClick={handleDownload}
            title="Download file"
            style={{ background: 'transparent', border: '1px solid rgba(0, 180, 220, 0.12)', color: 'var(--text-muted)', cursor: 'pointer', padding: '4px 6px', borderRadius: '3px', display: 'flex', alignItems: 'center' }}
          >
            <Download size={13} />
          </button>

          <button
            onClick={onClose}
            title="Close Canvas"
            style={{ background: 'transparent', border: '1px solid rgba(0, 180, 220, 0.12)', color: 'var(--text-muted)', cursor: 'pointer', padding: '4px 6px', borderRadius: '3px', display: 'flex', alignItems: 'center' }}
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Main Viewport */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {isMarkdown && activeTab === 'report' ? (
          <div
            className="markdown-content"
            style={{
              width: '100%',
              height: '100%',
              overflow: 'auto',
              padding: '24px 28px',
              backgroundColor: 'var(--bg-primary)',
              lineHeight: 1.7,
            }}
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath, remarkEmoji]}
              rehypePlugins={[rehypeHighlight, rehypeKatex]}
            >
              {item.content}
            </ReactMarkdown>
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
              padding: '16px',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.82rem',
              lineHeight: 1.6,
              color: 'var(--text-primary)',
              backgroundColor: 'rgba(4, 6, 15, 0.95)',
            }}
          >
            <code>{item.content}</code>
          </pre>
        )}
      </div>
    </div>
  );
};
