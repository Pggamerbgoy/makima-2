import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

interface MermaidChartProps {
  chart: string;
  subType?: string;
}

let mermaidInitialized = false;

function ensureMermaidInit() {
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'loose',
      fontFamily: 'Inter, sans-serif',
      suppressErrorRendering: true,
    });
    mermaidInitialized = true;
  }
}

const MERMAID_DIRECTIVES = [
  'graph', 'flowchart', 'sequencediagram', 'classdiagram', 'statediagram',
  'erdiagram', 'journey', 'gantt', 'pie', 'quadrantchart', 'requirementdiagram',
  'gitgraph', 'c4context', 'c4container', 'c4component', 'c4dynamic', 'c4deployment',
  'mindmap', 'timeline', 'zenuml', 'sankey-beta', 'block-beta', 'xychart-beta', 'architecture-beta'
];

export function normalizeMermaidChart(chart: string, subType?: string): string {
  let text = (chart || '').trim();
  if (!text) return text;

  // 1. If subType was on fence line (e.g. ```mermaid timeline)
  if (subType && !text.toLowerCase().startsWith(subType.toLowerCase())) {
    text = `${subType}\n${text}`;
  }

  // 2. Check if first word is a known directive
  const firstLine = text.split('\n')[0].trim().toLowerCase();
  const hasDirective = MERMAID_DIRECTIVES.some(d => firstLine.startsWith(d));

  if (!hasDirective) {
    // Check if it's a timeline format (e.g. "1990s: ...", "2000s: ...")
    const isTimelineLike = /^\d{2,4}s?\s*:/m.test(text);
    if (isTimelineLike) {
      const normalizedLines = text.split('\n').map(line => {
        const trimmed = line.trim();
        if (/^\d{2,4}s?\s*:/.test(trimmed)) {
          const colonIdx = trimmed.indexOf(':');
          const time = trimmed.substring(0, colonIdx).trim();
          const desc = trimmed.substring(colonIdx + 1).trim();
          return `    ${time} : ${desc}`;
        }
        return `    ${trimmed}`;
      }).join('\n');
      return `timeline\n${normalizedLines}`;
    }

    // Check if it's a graph/flowchart format (e.g. A --> B)
    if (/-->|---|==>|subgraph|\|>/.test(text)) {
      return `graph TD\n${text}`;
    }
  }

  // Auto-heal malformed connectors & unclosed node brackets
  text = text.replace(/\|>/g, ' --> ');
  text = text.replace(/\["([^"\]\n]+)(?:\s*(?:-->|---|==>))/g, '["$1"] --> ');

  return text;
}

export const MermaidChart: React.FC<MermaidChartProps> = ({ chart, subType }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svgContent, setSvgContent] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  const normalized = normalizeMermaidChart(chart, subType);

  useEffect(() => {
    let cancelled = false;
    const renderChart = async () => {
      try {
        ensureMermaidInit();
        setError(null);
        const id = `mermaid-${Math.random().toString(36).substring(2, 9)}`;
        const { svg } = await mermaid.render(id, normalized);
        if (!cancelled) {
          setSvgContent(svg);
        }
      } catch (err: any) {
        if (!cancelled) {
          console.error('Mermaid render error:', err);
          const msg = err?.message || String(err) || 'Failed to render Mermaid chart';
          setError(msg);
        }
      }
    };
    if (normalized) {
      renderChart();
    }
    return () => { cancelled = true; };
  }, [normalized]);

  if (error) {
    return (
      <div style={{ 
        padding: '12px', 
        border: '1px solid rgba(244, 67, 54, 0.4)', 
        borderRadius: '8px', 
        color: '#ff8a80', 
        backgroundColor: 'rgba(244, 67, 54, 0.08)', 
        fontSize: '0.85rem',
        margin: '12px 0'
      }}>
        <strong>Mermaid Syntax Error:</strong>
        <pre style={{ marginTop: '8px', whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.75rem', maxHeight: '120px', overflowY: 'auto' }}>{error}</pre>
        <details style={{ marginTop: '8px', fontSize: '0.7rem', color: '#aaa' }}>
          <summary style={{ cursor: 'pointer' }}>Show raw chart</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', marginTop: '6px' }}>{chart}</pre>
        </details>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="mermaid-chart"
      style={{
        display: 'flex',
        justifyContent: 'center',
        padding: '16px',
        backgroundColor: 'var(--bg-tertiary)',
        borderRadius: '12px',
        margin: '12px 0',
        overflowX: 'auto',
        boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.2)',
      }}
      dangerouslySetInnerHTML={{ __html: svgContent }}
    />
  );
};
