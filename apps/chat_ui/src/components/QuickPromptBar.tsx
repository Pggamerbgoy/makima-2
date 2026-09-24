import React, { useState } from 'react';
import {
  Sparkles,
  Code,
  Globe,
  Monitor,
  Music,
  FileText,
  ChevronDown,
  ChevronUp,
  Zap,
} from 'lucide-react';

interface QuickPrompt {
  id: string;
  category: 'code' | 'research' | 'system' | 'media' | 'analysis';
  icon: React.ReactNode;
  label: string;
  prompt: string;
}

const QUICK_PROMPTS: QuickPrompt[] = [
  {
    id: 'p1',
    category: 'code',
    icon: <Code size={13} color="var(--accent-purple)" />,
    label: 'Refactor Code',
    prompt: 'Refactor this code to follow clean architecture principles, add type hints, and robust error handling.',
  },
  {
    id: 'p2',
    category: 'code',
    icon: <Zap size={13} color="var(--accent-blue)" />,
    label: 'Unit Tests',
    prompt: 'Generate comprehensive unit tests using pytest covering happy paths, edge cases, and error conditions.',
  },
  {
    id: 'p3',
    category: 'research',
    icon: <Globe size={13} color="var(--accent-emerald)" />,
    label: 'Deep Web Search',
    prompt: 'Search the web for the latest developments in AI agents and synthesize an executive summary with cited sources.',
  },
  {
    id: 'p4',
    category: 'system',
    icon: <Monitor size={13} color="var(--accent-amber)" />,
    label: 'System Status',
    prompt: 'List active system processes and identify any apps consuming high CPU or memory.',
  },
  {
    id: 'p5',
    category: 'media',
    icon: <Music size={13} color="var(--accent-pink, #ec4899)" />,
    label: 'Lo-Fi Beats',
    prompt: 'Search and play relaxing lo-fi coding music on YouTube Web Player.',
  },
  {
    id: 'p6',
    category: 'analysis',
    icon: <FileText size={13} color="var(--primary)" />,
    label: 'Summarize Session',
    prompt: 'Summarize our conversation so far, highlighting key decisions, architecture points, and pending next steps.',
  },
];

interface QuickPromptBarProps {
  onSelectPrompt: (prompt: string) => void;
  disabled?: boolean;
}

export const QuickPromptBar: React.FC<QuickPromptBarProps> = ({
  onSelectPrompt,
  disabled = false,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="quick-prompt-bar-container">
      <div className="quick-prompt-bar-header">
        <button
          type="button"
          className="quick-prompt-toggle-btn"
          onClick={() => setIsExpanded(!isExpanded)}
          title={isExpanded ? 'Hide prompt suggestions' : 'Show quick agent prompt starters'}
        >
          <Sparkles size={13} color="var(--primary)" />
          <span className="quick-prompt-toggle-label">Agent Starters</span>
          {isExpanded ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
      </div>

      {isExpanded && (
        <div className="quick-prompt-chips-wrapper">
          {QUICK_PROMPTS.map((item) => (
            <button
              key={item.id}
              type="button"
              className="quick-prompt-chip"
              disabled={disabled}
              onClick={() => onSelectPrompt(item.prompt)}
              title={item.prompt}
            >
              <span className="quick-prompt-chip-icon">{item.icon}</span>
              <span className="quick-prompt-chip-label">{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default QuickPromptBar;
