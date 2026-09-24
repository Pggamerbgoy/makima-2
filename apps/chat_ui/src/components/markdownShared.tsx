import React, { Component, Suspense, useState } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Copy, Check, FileSpreadsheet, Table as TableIcon } from 'lucide-react';
import { CodeBlockRunner } from './CodeBlockRunner';
import type { CanvasItem } from '../types/chat';

const LazyMermaidChart = React.lazy(() =>
  import('./MermaidChart').then((mod) => ({ default: mod.MermaidChart }))
);

export const MermaidFallback: React.FC = () => (
  <div style={{ padding: '12px', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
    Loading diagram…
  </div>
);

export const LazyMermaid: React.FC<{ chart: string; subType?: string }> = ({ chart, subType }) => (
  <Suspense fallback={<MermaidFallback />}>
    <LazyMermaidChart chart={chart} subType={subType} />
  </Suspense>
);

export const extractTextFromReactNode = (node: any): string => {
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

export const MarkdownTable: React.FC<{ children?: ReactNode; [key: string]: any }> = ({ children, ...props }) => {
  const [copied, setCopied] = useState(false);
  const [copiedCsv, setCopiedCsv] = useState(false);
  const tableRef = React.useRef<HTMLTableElement>(null);

  const copyTable = () => {
    if (!tableRef.current) return;
    const table = tableRef.current;
    const rows = Array.from(table.querySelectorAll('tr'));
    if (!rows.length) return;

    const matrix: string[][] = rows.map((row) =>
      Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent?.trim().replace(/\|/g, '\\|') || '')
    );

    if (!matrix.length || !matrix[0].length) return;

    const colWidths = matrix[0].map((_, colIdx) =>
      Math.max(...matrix.map((row) => (row[colIdx] || '').length), 3)
    );

    let md = '';
    const headerRow = matrix[0];
    md += '| ' + headerRow.map((cell, idx) => cell.padEnd(colWidths[idx])).join(' | ') + ' |\n';
    md += '| ' + colWidths.map((w) => '-'.repeat(w)).join(' | ') + ' |\n';
    for (let r = 1; r < matrix.length; r++) {
      md += '| ' + matrix[r].map((cell, idx) => (cell || '').padEnd(colWidths[idx])).join(' | ') + ' |\n';
    }

    navigator.clipboard?.writeText(md.trim());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const copyTableCsv = () => {
    if (!tableRef.current) return;
    const rows = Array.from(tableRef.current.querySelectorAll('tr'));
    if (!rows.length) return;
    const csv = rows
      .map((r) =>
        Array.from(r.querySelectorAll('th, td'))
          .map((c) => `"${(c.textContent?.trim() || '').replace(/"/g, '""')}"`)
          .join(',')
      )
      .join('\n');
    navigator.clipboard?.writeText(csv);
    setCopiedCsv(true);
    setTimeout(() => setCopiedCsv(false), 2000);
  };

  return (
    <div className="gpt-table-card">
      <div className="gpt-table-header">
        <div className="gpt-table-title">
          <TableIcon size={14} className="gpt-table-icon" />
          <span>Data Table</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button onClick={copyTableCsv} className="gpt-table-copy-btn" title="Copy table as CSV">
            {copiedCsv ? <Check size={12} style={{ color: '#54c58a' }} /> : <FileSpreadsheet size={12} />}
            <span>{copiedCsv ? 'CSV Copied' : 'CSV'}</span>
          </button>
          <button onClick={copyTable} className="gpt-table-copy-btn" title="Copy table as Markdown">
            {copied ? <Check size={12} style={{ color: '#54c58a' }} /> : <Copy size={12} />}
            <span>{copied ? 'Copied' : 'Markdown'}</span>
          </button>
        </div>
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

interface MarkdownErrorBoundaryState {
  hasError: boolean;
}

export class MarkdownErrorBoundary extends Component<MarkdownErrorBoundaryProps, MarkdownErrorBoundaryState> {
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

const getSafeHttpUrl = (value: string | undefined): string | null => {
  if (!value) return null;
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:' || parsed.protocol === 'blob:' || parsed.protocol === 'data:') {
      if (parsed.protocol === 'data:' && !parsed.pathname.startsWith('image/')) return null;
      return parsed.toString();
    }
  } catch {
    return null;
  }
  return null;
};

export interface CanvasMarkdownOptions {
  fallbackText: string;
  onOpenCanvas?: (item: CanvasItem) => void;
  onImageClick?: (src: string) => void;
}

export const buildCanvasMarkdownComponents = ({ onOpenCanvas, onImageClick }: CanvasMarkdownOptions) => ({
  table(props: any) {
    return <MarkdownTable {...props} />;
  },
  code({ node, inline, className, children, ...props }: any) {
    const match = /language-([^\s]+)(?:\s+(.*))?/.exec(className || '');
    const fullLang = match ? match[1].toLowerCase() : 'code';
    const isMermaid = fullLang.startsWith('mermaid');
    const subType = match && match[2] ? match[2].trim() : isMermaid ? fullLang.replace(/^mermaid/i, '').trim() : '';
    const lang = isMermaid ? 'mermaid' : fullLang;
    const rawCode =
      extractTextFromReactNode(children) ||
      (node && node.value ? String(node.value) : '') ||
      String(children || '');
    const codeString = rawCode.replace(/\n$/, '');
    const looksLikeBlock = !inline && (Boolean(className) || codeString.includes('\n'));

    if (looksLikeBlock) {
      if (lang === 'mermaid') {
        return <LazyMermaid chart={codeString} subType={subType} />;
      }
      return (
        <CodeBlockRunner language={lang} codeString={codeString} onOpenCanvas={onOpenCanvas} />
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
  img({ src, alt, ...props }: any) {
    const safeSrc = getSafeHttpUrl(typeof src === 'string' ? src : undefined);
    if (!safeSrc) return <span className="media-card-error">Image preview unavailable.</span>;
    return (
      <img
        src={safeSrc}
        alt={alt || 'Image'}
        loading="lazy"
        decoding="async"
        onClick={onImageClick ? () => onImageClick(src) : undefined}
        style={{
          maxWidth: '100%',
          borderRadius: '12px',
          cursor: onImageClick ? 'pointer' : 'default',
          margin: '8px 0',
          display: 'block',
        }}
        {...props}
      />
    );
  },
  a({ href, children, ...props }: any) {
    const safeHref = getSafeHttpUrl(href);
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
});
