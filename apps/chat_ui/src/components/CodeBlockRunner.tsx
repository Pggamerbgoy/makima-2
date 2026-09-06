import React, { useState, useMemo } from 'react';
import { Play, Copy, Check, Layout, Terminal, RefreshCw } from 'lucide-react';
import hljs from 'highlight.js';
import type { CanvasItem } from '../types/chat';

interface CodeBlockRunnerProps {
  language: string;
  codeString: string;
  onOpenCanvas?: (canvasItem: CanvasItem) => void;
}

export const CodeBlockRunner: React.FC<CodeBlockRunnerProps> = ({
  language,
  codeString,
  onOpenCanvas,
}) => {
  const [copied, setCopied] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  const cleanString = typeof codeString === 'string' ? codeString : String(codeString || '');

  const highlightedHtml = useMemo(() => {
    try {
      const lang = (language || '').toLowerCase().trim();
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(cleanString, { language: lang, ignoreIllegals: true }).value;
      }
      return hljs.highlightAuto(cleanString).value;
    } catch {
      return '';
    }
  }, [cleanString, language]);

  const handleCopy = () => {
    navigator.clipboard.writeText(codeString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRunCode = () => {
    setIsRunning(true);
    setOutput(null);

    const lang = language.toLowerCase();
    if (lang === 'javascript' || lang === 'js' || lang === 'typescript' || lang === 'ts') {
      const workerScript = `
        self.onmessage = function(e) {
          const logs = [];
          const customConsole = {
            log: (...args) => logs.push(args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')),
            error: (...args) => logs.push('[ERROR] ' + args.map(a => String(a)).join(' ')),
            warn: (...args) => logs.push('[WARN] ' + args.map(a => String(a)).join(' ')),
          };
          try {
            const runFn = new Function('console', e.data);
            runFn(customConsole);
            self.postMessage({ success: true, logs });
          } catch (err) {
            self.postMessage({ success: false, error: err.message || String(err), logs });
          }
        };
      `;
      let worker: Worker | null = null;
      let blobUrl = '';
      try {
        const blob = new Blob([workerScript], { type: 'application/javascript' });
        blobUrl = URL.createObjectURL(blob);
        worker = new Worker(blobUrl);

        const timer = setTimeout(() => {
          if (worker) {
            worker.terminate();
            URL.revokeObjectURL(blobUrl);
            setOutput('[Runtime Timeout]: Code execution exceeded 3000ms limit.');
            setIsRunning(false);
          }
        }, 3000);

        worker.onmessage = (e) => {
          clearTimeout(timer);
          const { success, error, logs } = e.data;
          if (success) {
            setOutput(logs.length > 0 ? logs.join('\n') : 'Code executed successfully (no console output).');
          } else {
            const outputText = logs.length > 0 ? `${logs.join('\n')}\n[Runtime Error]: ${error}` : `[Runtime Error]: ${error}`;
            setOutput(outputText);
          }
          if (worker) worker.terminate();
          URL.revokeObjectURL(blobUrl);
          setIsRunning(false);
        };

        worker.onerror = (err) => {
          clearTimeout(timer);
          setOutput(`[Worker Error]: ${err.message || 'Execution failed'}`);
          if (worker) worker.terminate();
          URL.revokeObjectURL(blobUrl);
          setIsRunning(false);
        };

        worker.postMessage(codeString);
      } catch (err: any) {
        setOutput(`[Initialization Error]: ${err.message || String(err)}`);
        if (worker) worker.terminate();
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        setIsRunning(false);
      }
    } else if (lang === 'html') {
      setOutput('HTML preview rendered. Click "Open Canvas" to view full live interactive preview.');
      setIsRunning(false);
    } else if (lang === 'python' || lang === 'py') {
      setOutput(`[Python 3.12 Sandboxed]\nNote: Python code execution runs through Makima's backend Code Agent. Paste the snippet or ask Makima in chat to execute it directly.`);
      setIsRunning(false);
    } else {
      setOutput(`[Compiler]\nExecution for '${language}' is supported via backend Code Agent in chat.`);
      setIsRunning(false);
    }
  };

  return (
    <div className="code-block-wrapper">
      {/* Header bar */}
      <div className="code-block-header">
        <span>{language}</span>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {/* Run Code Button */}
          <button
            onClick={handleRunCode}
            disabled={isRunning}
            style={{
              color: 'var(--accent-blue)',
              fontWeight: 600,
              backgroundColor: 'rgba(66, 133, 244, 0.12)',
            }}
            title="Run Code In Browser"
          >
            {isRunning ? <RefreshCw size={12} className="spin" /> : <Play size={12} />} Run Code
          </button>

          {onOpenCanvas && (
            <button
              onClick={() =>
                onOpenCanvas({
                  id: 'canvas_' + Math.random().toString(36).substring(2, 9),
                  title: `${language.toUpperCase()} Snippet`,
                  language: language,
                  content: codeString,
                })
              }
              title="Open Canvas Side Panel"
            >
              <Layout size={12} /> Canvas
            </button>
          )}

          <button onClick={handleCopy} title="Copy Code">
            {copied ? <Check size={12} color="var(--accent-blue)" /> : <Copy size={12} />} {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>

      {/* Code body */}
      <pre>
        {highlightedHtml ? (
          <code
            className={`hljs language-${language}`}
            dangerouslySetInnerHTML={{ __html: highlightedHtml }}
          />
        ) : (
          <code className={`hljs language-${language}`}>{cleanString}</code>
        )}
      </pre>

      {/* Live Terminal Output Box */}
      {output !== null && (
        <div
          style={{
            borderTop: '1px solid #2a2a2a',
            backgroundColor: '#050505',
            padding: '10px 14px',
            fontSize: '0.82rem',
            fontFamily: 'monospace',
            color: '#4caf50',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '6px' }}>
            <Terminal size={12} /> Console Output
          </div>
          <pre style={{ margin: 0, padding: 0, whiteSpace: 'pre-wrap', color: output.includes('[Runtime Error]') ? '#f44336' : '#e0e0e0' }}>
            {output}
          </pre>
        </div>
      )}
    </div>
  );
};
