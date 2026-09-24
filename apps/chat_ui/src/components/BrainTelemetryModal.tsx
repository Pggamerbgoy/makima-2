import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  X,
  Activity,
  Cpu,
  Database,
  Radio,
  RefreshCw,
  CheckCircle2,
  Server,
  Sparkles,
  HardDrive,
  Gauge,
  Zap,
} from 'lucide-react';
import { wsClient } from '../services/wsClient';
import { getSystemStatus, type SystemStatusSnapshot } from '../services/brainApi';
import type { AppSettings } from '../types/chat';

interface BrainTelemetryModalProps {
  isOpen: boolean;
  onClose: () => void;
  isConnected: boolean;
  settings: AppSettings;
  sessionCount: number;
  totalMessagesCount: number;
}

interface AgentInfo {
  id: string;
  name: string;
  category: string;
  description: string;
  color: string;
}

const AGENT_FLEET: AgentInfo[] = [
  {
    id: 'commander',
    name: 'Commander Agent',
    category: 'Orchestration',
    description: 'DAG planning, sub-agent delegation, and cross-agent execution',
    color: '#8b5cf6',
  },
  {
    id: 'code',
    name: 'Code Agent',
    category: 'Engineering',
    description: 'Autonomous coding, refactoring, Qwen Coder routing, and tests',
    color: '#3b82f6',
  },
  {
    id: 'system',
    name: 'System Agent',
    category: 'Local OS',
    description: 'Windows app launching, focus, window snapping, cleanup, shell',
    color: '#10b981',
  },
  {
    id: 'browser',
    name: 'Browser Agent',
    category: 'Web Automation',
    description: 'Playwright automation, CDP attachment, scraping, visible browser',
    color: '#06b6d4',
  },
  {
    id: 'media',
    name: 'Media Agent',
    category: 'Entertainment',
    description: 'YouTube and Spotify web player control, volume, search navigation',
    color: '#ec4899',
  },
  {
    id: 'voice',
    name: 'Voice Agent',
    category: 'Speech',
    description: 'Gemini Live, Kokoro-ONNX / Edge-TTS synthesis, and Whisper STT',
    color: '#f59e0b',
  },
  {
    id: 'memory',
    name: 'Memory Agent',
    category: 'Storage',
    description: 'EternalMemory HNSW vector search, SQLite WAL integrity, recall',
    color: '#6366f1',
  },
  {
    id: 'research',
    name: 'Research Agent',
    category: 'Intelligence',
    description: 'Multi-hop web research, DuckDuckGo search, domain credibility',
    color: '#14b8a6',
  },
  {
    id: 'document',
    name: 'Document Agent',
    category: 'Productivity',
    description: 'Spreadsheet (.xlsx), Word (.docx), PDF, and slide deck generation',
    color: '#f97316',
  },
  {
    id: 'devops',
    name: 'DevOps Agent',
    category: 'Infrastructure',
    description: 'Docker containers, build scripts, GitHub Actions, environment',
    color: '#64748b',
  },
  {
    id: 'security',
    name: 'Security Agent',
    category: 'Audit',
    description: 'Vulnerability scans, hardcoded secrets leak detection, injection',
    color: '#ef4444',
  },
];

export const BrainTelemetryModal: React.FC<BrainTelemetryModalProps> = ({
  isOpen,
  onClose,
  isConnected,
  settings,
  sessionCount,
  totalMessagesCount,
}) => {
  const [latency, setLatency] = useState<number | null>(null);
  const [isPinging, setIsPinging] = useState(false);
  const [pingError, setPingError] = useState<string | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatusSnapshot | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const measurePing = async () => {
    if (!isConnected) {
      setLatency(null);
      setPingError('Brain WebSocket disconnected');
      return;
    }
    setIsPinging(true);
    setPingError(null);
    try {
      const rtt = await wsClient.ping();
      setLatency(rtt);
    } catch (err: any) {
      const msg = err?.message || 'Ping timed out';
      setPingError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — cannot measure latency.'
        : msg);
      setLatency(null);
    } finally {
      setIsPinging(false);
    }
  };

  const fetchMetrics = async () => {
    setStatusLoading(true);
    setStatusError(null);
    try {
      const data = await getSystemStatus(settings.wsUrl);
      setSystemStatus(data);
    } catch (err: any) {
      const msg = err?.message || 'Failed to fetch status';
      setStatusError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — system metrics unavailable.'
        : msg);
    } finally {
      setStatusLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      measurePing();
      fetchMetrics();
    }
  }, [isOpen, isConnected]);

  if (!isOpen) return null;

  return (
    <motion.div
      className="telemetry-modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
      onClick={onClose}
    >
      <motion.div
        className="telemetry-modal"
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 4 }}
        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="telemetry-modal-header">
          <div className="telemetry-header-title-box">
            <div className="telemetry-header-gem">
              <Activity size={18} />
            </div>
            <div>
              <h2 className="telemetry-modal-title">Makima Brain Telemetry & Health</h2>
              <p className="telemetry-modal-subtitle">Autonomous agent engine, WebSocket bridge & system diagnostic</p>
            </div>
          </div>
          <button className="telemetry-close-btn" onClick={onClose} aria-label="Close telemetry">
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="telemetry-modal-body">
          {/* Top Status Cards Grid */}
          <div className="telemetry-stats-grid">
            {/* WebSocket Connection Card */}
            <div className="telemetry-stat-card">
              <div className="telemetry-card-top">
                <span className="telemetry-stat-label">WebSocket Connection</span>
                <Radio size={15} color={isConnected ? 'var(--accent-emerald)' : 'var(--danger)'} />
              </div>
              <div className="telemetry-card-main">
                <span className={`telemetry-dot ${isConnected ? 'online' : 'offline'}`} />
                <span className="telemetry-stat-value">{isConnected ? 'ONLINE' : 'OFFLINE'}</span>
              </div>
              <span className="telemetry-card-sub">{settings.wsUrl}</span>
            </div>

            {/* Latency RTT Card */}
            <div className="telemetry-stat-card">
              <div className="telemetry-card-top">
                <span className="telemetry-stat-label">Round-Trip Latency</span>
                <button
                  className="telemetry-ping-btn"
                  onClick={measurePing}
                  disabled={isPinging || !isConnected}
                  title="Measure live round-trip latency"
                >
                  <RefreshCw size={12} className={isPinging ? 'spin' : ''} />
                  <span>Ping</span>
                </button>
              </div>
              <div className="telemetry-card-main">
                <span className="telemetry-stat-value">
                  {latency !== null ? `${latency} ms` : pingError ? 'Timeout' : 'Measuring...'}
                </span>
              </div>
              <span className="telemetry-card-sub">
                {latency !== null
                  ? latency < 50
                    ? 'Ultra-low latency (<50ms)'
                    : latency < 150
                    ? 'Normal responsive link'
                    : 'Elevated network latency'
                  : pingError || 'WebSocket keepalive heartbeat'}
              </span>
            </div>

            {/* Active AI Model Card */}
            <div className="telemetry-stat-card">
              <div className="telemetry-card-top">
                <span className="telemetry-stat-label">Active LLM Engine</span>
                <Sparkles size={15} color="var(--primary)" />
              </div>
              <div className="telemetry-card-main">
                <span className="telemetry-stat-value" style={{ textTransform: 'uppercase' }}>
                  {settings.llmProvider}
                </span>
              </div>
              <span className="telemetry-card-sub" title={settings.model}>
                {settings.model || 'Default provider model'}
              </span>
            </div>

            {/* Memory & DB Card */}
            <div className="telemetry-stat-card">
              <div className="telemetry-card-top">
                <span className="telemetry-stat-label">Storage & Memory</span>
                <Database size={15} color="var(--accent-indigo)" />
              </div>
              <div className="telemetry-card-main">
                <span className="telemetry-stat-value">{sessionCount} Chats</span>
              </div>
              <span className="telemetry-card-sub">
                {totalMessagesCount} messages · SQLite WAL + Vector
              </span>
            </div>
          </div>

          {/* Live Host Hardware Telemetry Section */}
          <div className="telemetry-section">
            <div className="telemetry-section-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Gauge size={16} color="var(--accent-cyan, #06b6d4)" />
                <h3 className="telemetry-section-title">Host Hardware Telemetry</h3>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button
                  className="telemetry-ping-btn"
                  onClick={fetchMetrics}
                  disabled={statusLoading}
                  title="Refresh hardware metrics from Makima Brain"
                >
                  <RefreshCw size={12} className={statusLoading ? 'spin' : ''} />
                  <span>{statusLoading ? 'Refreshing...' : 'Refresh'}</span>
                </button>
                <span
                  className="telemetry-fleet-badge"
                  style={{
                    color: !isConnected
                      ? 'var(--danger, #ef4444)'
                      : statusError
                      ? 'var(--accent-amber, #f59e0b)'
                      : 'var(--accent-emerald, #10b981)',
                  }}
                  title={statusError || undefined}
                >
                  {!isConnected ? 'OFFLINE' : statusError ? 'METRICS UNAVAILABLE' : 'LIVE BACKEND'}
                </span>
              </div>
            </div>

            <div className="telemetry-stats-grid">
              {/* CPU Card */}
              <div className="telemetry-stat-card">
                <div className="telemetry-card-top">
                  <span className="telemetry-stat-label">CPU Utilization</span>
                  <Cpu size={15} color="var(--primary)" />
                </div>
                <div className="telemetry-card-main">
                  <span className="telemetry-stat-value">
                    {systemStatus?.system_metrics?.cpu_percent !== undefined
                      ? `${systemStatus.system_metrics.cpu_percent}%`
                      : statusLoading
                      ? '...'
                      : 'N/A'}
                  </span>
                </div>
                <div style={{ height: 4, background: 'var(--border-subtle)', borderRadius: 2, overflow: 'hidden', margin: '4px 0 2px' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, systemStatus?.system_metrics?.cpu_percent || 0)}%`,
                      background: (systemStatus?.system_metrics?.cpu_percent || 0) > 85 ? 'var(--danger, #ef4444)' : 'var(--primary)',
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
                <span className="telemetry-card-sub">
                  {systemStatus?.system_metrics?.processes_count
                    ? `${systemStatus.system_metrics.processes_count} active OS processes`
                    : 'Supervised execution thread'}
                </span>
              </div>

              {/* RAM Card */}
              <div className="telemetry-stat-card">
                <div className="telemetry-card-top">
                  <span className="telemetry-stat-label">System Memory (RAM)</span>
                  <Activity size={15} color="var(--accent-indigo, #6366f1)" />
                </div>
                <div className="telemetry-card-main">
                  <span className="telemetry-stat-value">
                    {systemStatus?.system_metrics
                      ? `${systemStatus.system_metrics.ram_used_gb} / ${systemStatus.system_metrics.ram_total_gb} GB`
                      : statusLoading
                      ? '...'
                      : 'N/A'}
                  </span>
                </div>
                <div style={{ height: 4, background: 'var(--border-subtle)', borderRadius: 2, overflow: 'hidden', margin: '4px 0 2px' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, systemStatus?.system_metrics?.ram_percent || 0)}%`,
                      background:
                        (systemStatus?.system_metrics?.ram_percent || 0) > 85
                          ? 'var(--danger, #ef4444)'
                          : (systemStatus?.system_metrics?.ram_percent || 0) > 70
                          ? 'var(--accent-amber, #f59e0b)'
                          : 'var(--accent-indigo, #6366f1)',
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
                <span className="telemetry-card-sub">
                  {systemStatus?.system_metrics
                    ? `${systemStatus.system_metrics.ram_percent}% physical memory allocated`
                    : 'Dynamic heap allocation'}
                </span>
              </div>

              {/* GPU Card */}
              <div className="telemetry-stat-card">
                <div className="telemetry-card-top">
                  <span className="telemetry-stat-label">GPU Acceleration</span>
                  <Zap size={15} color="var(--accent-emerald, #10b981)" />
                </div>
                <div className="telemetry-card-main">
                  <span className="telemetry-stat-value">
                    {systemStatus?.system_metrics?.gpu?.name
                      ? `${systemStatus.system_metrics.gpu.gpu_percent || 0}%`
                      : systemStatus?.system_metrics?.gpu_percent !== undefined
                      ? `${systemStatus.system_metrics.gpu_percent}%`
                      : 'Active'}
                  </span>
                </div>
                <div style={{ height: 4, background: 'var(--border-subtle)', borderRadius: 2, overflow: 'hidden', margin: '4px 0 2px' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, systemStatus?.system_metrics?.gpu?.gpu_percent || systemStatus?.system_metrics?.gpu_percent || 0)}%`,
                      background: 'var(--accent-emerald, #10b981)',
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
                <span className="telemetry-card-sub" title={systemStatus?.system_metrics?.gpu?.name || 'Graphics Compute Engine'}>
                  {systemStatus?.system_metrics?.gpu?.name
                    ? `${systemStatus.system_metrics.gpu.name.slice(0, 24)}...`
                    : 'CUDA / DirectML / CPU Fallback'}
                </span>
              </div>

              {/* Disk Card */}
              <div className="telemetry-stat-card">
                <div className="telemetry-card-top">
                  <span className="telemetry-stat-label">Disk Storage</span>
                  <HardDrive size={15} color="var(--accent-amber, #f59e0b)" />
                </div>
                <div className="telemetry-card-main">
                  <span className="telemetry-stat-value">
                    {systemStatus?.system_metrics?.disk_percent !== undefined
                      ? `${systemStatus.system_metrics.disk_percent}%`
                      : statusLoading
                      ? '...'
                      : 'N/A'}
                  </span>
                </div>
                <div style={{ height: 4, background: 'var(--border-subtle)', borderRadius: 2, overflow: 'hidden', margin: '4px 0 2px' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, systemStatus?.system_metrics?.disk_percent || 0)}%`,
                      background: (systemStatus?.system_metrics?.disk_percent || 0) > 90 ? 'var(--danger, #ef4444)' : 'var(--accent-amber, #f59e0b)',
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
                <span className="telemetry-card-sub">
                  Root filesystem & vector index
                </span>
              </div>
            </div>
          </div>

          {/* Autonomous Agent Fleet Section */}
          <div className="telemetry-section">
            <div className="telemetry-section-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Cpu size={16} color="var(--primary)" />
                <h3 className="telemetry-section-title">Autonomous Agent Fleet (11 Specialized Agents)</h3>
              </div>
              <span className="telemetry-fleet-badge">
                {!systemStatus
                  ? 'Catalog — metrics pending'
                  : systemStatus.agents
                    ? `Live · ${String(systemStatus.agents.type || 'engine')} · tools ${systemStatus.agents.tool_count ?? '—'}`
                    : 'Agent metrics unavailable'}
              </span>
            </div>

            <div className="telemetry-agent-grid">
              {AGENT_FLEET.map((agent) => (
                <div key={agent.id} className="telemetry-agent-card">
                  <div className="telemetry-agent-card-header">
                    <div className="telemetry-agent-indicator-group">
                      <span className="telemetry-agent-dot" style={{ backgroundColor: agent.color }} />
                      <span className="telemetry-agent-name">{agent.name}</span>
                    </div>
                    <span className="telemetry-agent-category">{agent.category}</span>
                  </div>
                  <p className="telemetry-agent-desc">{agent.description}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Subsystem Integrity Checklist */}
          <div className="telemetry-section">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <Server size={16} color="var(--accent-emerald)" />
              <h3 className="telemetry-section-title">Engine Architecture & Protocol Guardrails</h3>
            </div>

            <div className="telemetry-guardrail-list">
              <div className="telemetry-guardrail-item">
                <CheckCircle2 size={15} color="var(--accent-emerald)" />
                <div>
                  <strong>Protocol v1 Message Validation:</strong> Client and server negotiate `v: 1` on every WS frame; unknown types are rejected.
                </div>
              </div>
              <div className="telemetry-guardrail-item">
                <CheckCircle2 size={15} color="var(--accent-emerald)" />
                <div>
                  <strong>Watchdog Stream Guard:</strong> Client-side timeout clears hung tasks; backend keeps its own stream watchdog.
                </div>
              </div>
              <div className="telemetry-guardrail-item">
                <CheckCircle2 size={15} color="var(--accent-emerald)" />
                <div>
                  <strong>Live backend only:</strong> Chat and tools talk to the local brain over `/ws` + REST — no cloud mock path in the UI.
                </div>
              </div>
              <div className="telemetry-guardrail-item">
                <CheckCircle2 size={15} color="var(--accent-emerald)" />
                <div>
                  <strong>Native Layer Integration:</strong> Windows UI automation, clipboard events, and system process supervision.
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="telemetry-modal-footer">
          <span className="telemetry-footer-status">
            {isConnected ? 'Connected to Makima Brain Runtime' : 'Waiting for Makima Brain connection...'}
          </span>
          <button className="telemetry-footer-close-btn" onClick={onClose}>
            Done
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
};

export default BrainTelemetryModal;
