import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

interface MermaidChartProps {
  chart: string;
  subType?: string;
}

let mermaidInitialized = false;
let lastMermaidTheme: 'dark' | 'default' | null = null;

function currentMermaidTheme(): 'dark' | 'default' {
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr === 'light') return 'default';
  if (attr === 'dark') return 'dark';
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'default' : 'dark';
}

function ensureMermaidInit() {
  const theme = currentMermaidTheme();
  if (!mermaidInitialized || lastMermaidTheme !== theme) {
    mermaid.initialize({
      startOnLoad: false,
      theme,
      securityLevel: 'loose',
      fontFamily: 'Inter, sans-serif',
      suppressErrorRendering: true,
    });
    mermaidInitialized = true;
    lastMermaidTheme = theme;
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

  // 1. If subType was on fence line (e.g. ```mermaid timeline).
  //    A bare direction after ```mermaid graph joins up: "TD\n..." -> "graph TD\n...".
  if (subType && !text.toLowerCase().startsWith(subType.toLowerCase())) {
    const first = text.split('\n')[0].trim();
    if (/^graph$/i.test(subType) && /^(TD|LR|RL|BT|TB)\b/i.test(first)) {
      text = `graph ${text}`;
    } else {
      text = `${subType}\n${text}`;
    }
  }

  // Auto-heal for flowchart-family syntax: broken "|>" connectors and
  // unclosed node brackets like ["Start --> B. Runs BEFORE directive
  // detection so it never gets skipped by an early return.
  const healFlowchart = (t: string): string =>
    t
      .replace(/\|>/g, ' --> ')
      .replace(/\["([^"\]\n]+?)\s*(-->|---|==>)/g, '["$1"] --> ');

  // 2. Known directive at the top — heal flowchart-family content as-is.
  const firstLine = text.split('\n')[0].trim().toLowerCase();
  if (MERMAID_DIRECTIVES.some((d) => firstLine.startsWith(d))) {
    if (firstLine === 'timeline') {
      // Indent bare "1990s : event" body lines; leave title/blank lines alone.
      const lines = text.split('\n');
      const body = lines.slice(1).map((line) => {
        if (/^\d{2,4}s?\s*:/.test(line)) {
          const colonIdx = line.indexOf(':');
          return `    ${line.substring(0, colonIdx).trim()} : ${line.substring(colonIdx + 1).trim()}`;
        }
        return line;
      });
      return [lines[0], ...body].join('\n');
    }
    return /^(graph|flowchart)\b/.test(firstLine) ? healFlowchart(text) : text;
  }

  // 3. Timeline-like content (e.g. "1990s: ...") — no flowchart healing inside prose.
  if (/^\d{2,4}s?\s*:/m.test(text)) {
    const normalizedLines = text.split('\n').map((line) => {
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

  // 4. Graph-ish content (edges) or a bare direction line ("TD\n...") — heal + wrap.
  if (/-->|---|==>|subgraph|\|>/.test(text) || /^(TD|LR|RL|BT|TB)\b/i.test(text.split('\n')[0].trim())) {
    const healed = healFlowchart(text);
    const bareDirection = /^(TD|LR|RL|BT|TB)\b/i.test(healed.split('\n')[0].trim());
    return bareDirection ? `graph ${healed}` : `graph TD\n${healed}`;
  }

  return text;
}

export const MermaidChart: React.FC<MermaidChartProps> = ({ chart, subType }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svgContent, setSvgContent] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [themeTick, setThemeTick] = useState(0);

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
  }, [normalized, themeTick]);

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setThemeTick((tick) => tick + 1);
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  if (error) {
    return (
      <div style={{ 
        padding: '12px', 
        border: '1px solid var(--danger)', 
        borderRadius: '8px', 
        color: 'var(--danger)', 
        backgroundColor: 'var(--danger-subtle)', 
        fontSize: '0.85rem',
        margin: '12px 0'
      }}>
        <strong>Mermaid Syntax Error:</strong>
        <pre style={{ marginTop: '8px', whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.75rem', maxHeight: '120px', overflowY: 'auto' }}>{error}</pre>
        <details style={{ marginTop: '8px', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          <summary style={{ cursor: 'pointer' }}>Show raw chart</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', marginTop: '6px' }}>{chart}</pre>
        </details>
      </div>
    );
  }

  if (!svgContent) {
    return (
      <div style={{
        padding: '16px', borderRadius: '12px', margin: '12px 0',
        backgroundColor: 'var(--bg-tertiary)', color: 'var(--text-muted)',
        fontSize: '0.82rem', textAlign: 'center',
      }}>
        Rendering chart…
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
        overflowY: 'auto',
        maxHeight: '420px',
        boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.2)',
      }}
      dangerouslySetInnerHTML={{ __html: svgContent }}
    />
  );
};
