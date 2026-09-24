import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  X, Check, Sliders, Plug, Volume2, Terminal, Cpu,
  GitBranch, MessageSquare, Mail, Music, FileText, Database, Tv, Share2, Globe,
  Shield, VolumeX, Play, Trash2, Eye, EyeOff, Activity, CheckCircle2, RefreshCw,
  Zap, KeyRound, LoaderCircle, Server, Sparkles, Wrench, Plus
} from 'lucide-react';
import type { AppSettings, LLMProvider } from '../types/chat';
import { wsClient } from '../services/wsClient';
import {
  getLLMProviders, saveLLMProvider, testIntegration, oauthStatus, oauthDisconnect, oauthLoginUrl,
  listTools, setToolEnabled, listMcpServers, addMcpServer, deleteMcpServer, reloadMcpServers,
  type ToolEntry, type McpServerEntry,
} from '../services/brainApi';

interface ModelSelectorPanelProps {
  providers: LLMProvider[];
  providerId: string;
  model: string;
  apiKey: string;
  baseUrl: string;
  loading: boolean;
  saving: boolean;
  error: string;
  savedMessage: string;
  onProviderChange: (id: string) => void;
  onModelChange: (model: string) => void;
  onApiKeyChange: (key: string) => void;
  onBaseUrlChange: (url: string) => void;
  onRefresh: () => void;
  onSave: () => void;
}

const ModelSelectorPanel: React.FC<ModelSelectorPanelProps> = ({
  providers,
  providerId,
  model,
  apiKey,
  baseUrl,
  loading,
  saving,
  error,
  savedMessage,
  onProviderChange,
  onModelChange,
  onApiKeyChange,
  onBaseUrlChange,
  onRefresh,
  onSave,
}) => {
  const selected = providers.find((provider) => provider.id === providerId);

  return (
    <section className="model-selector-panel" aria-label="AI model selection">
      <div className="model-selector-hero">
        <div className="model-selector-icon"><Sparkles size={20} /></div>
        <div>
          <p className="eyebrow">Agent runtime</p>
          <h4>Choose your provider and model</h4>
          <p>Like an agentic IDE: bring your API key, pick a model, and apply it to Makima live.</p>
        </div>
        <button className="icon-button" onClick={onRefresh} title="Refresh providers" aria-label="Refresh providers">
          <RefreshCw size={16} className={loading ? 'spin' : ''} />
        </button>
      </div>

      {error && <div className="inline-alert error">{error}</div>}
      {savedMessage && <div className="inline-alert success"><CheckCircle2 size={15} /> {savedMessage}</div>}

      <div className="provider-grid">
        {providers.length === 0 && !loading && (
          <div className="empty-provider-state">No providers returned. Check that Makima Brain is running.</div>
        )}
        {providers.map((provider) => (
          <button
            key={provider.id}
            className={`provider-card ${provider.id === providerId ? 'selected' : ''}`}
            onClick={() => onProviderChange(provider.id)}
            type="button"
          >
            <span className="provider-card-mark"><Server size={16} /></span>
            <span className="provider-card-copy">
              <strong>{provider.name}</strong>
              <small>{provider.local ? 'Local / private' : provider.configured ? 'API key configured' : 'API key required'}</small>
            </span>
            {provider.id === providerId && <CheckCircle2 size={17} className="provider-check" />}
          </button>
        ))}
      </div>

      {selected?.models && selected.models.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', margin: '6px 0 10px' }}>
          <span style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Quick Select Models for {selected.name}:
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {selected.models.map((m) => {
              const isSelected = model === m;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => onModelChange(m)}
                  style={{
                    padding: '5px 12px',
                    borderRadius: 'var(--radius-full)',
                    fontSize: '0.75rem',
                    fontFamily: 'var(--font-mono)',
                    border: `1px solid ${isSelected ? 'var(--primary)' : 'var(--border-subtle)'}`,
                    background: isSelected ? 'var(--primary-subtle)' : 'var(--bg-canvas)',
                    color: isSelected ? 'var(--primary)' : 'var(--text-primary)',
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.borderColor = 'var(--primary-border)'; }}
                  onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.borderColor = 'var(--border-subtle)'; }}
                >
                  {m}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="model-form-grid">
        <label className="field-block">
          <span>Model (Dropdown)</span>
          <select value={model} onChange={(event) => onModelChange(event.target.value)} disabled={loading}>
            {(selected?.models || []).map((option) => <option value={option} key={option}>{option}</option>)}
            {model && !(selected?.models || []).includes(model) && <option value={model}>{model}</option>}
          </select>
        </label>
        <label className="field-block">
          <span>Custom model ID</span>
          <input value={model} onChange={(event) => onModelChange(event.target.value)} placeholder="provider/model-name" />
        </label>
      </div>
      {selected && <div className="capability-row" aria-label="Provider capabilities">
        {(['text', 'image', 'audio', 'video'] as const).map((capability) => <span key={capability} className={selected.capabilities?.[capability] ? 'capability enabled' : 'capability'}>{selected.capabilities?.[capability] ? '✓' : '—'} {capability}</span>)}
      </div>}

      {!selected?.local && (
        <label className="field-block">
          <span><KeyRound size={14} /> API key <small>{selected?.keyHint ? `(${selected.keyHint})` : '(stored securely by Makima)'}</small></span>
          <input type="password" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} placeholder={selected?.configured ? 'Leave blank to keep current key' : 'Paste provider API key'} autoComplete="new-password" />
        </label>
      )}

      {!selected?.local && (
        <details className="advanced-model-settings">
          <summary>Advanced endpoint</summary>
          <label className="field-block" style={{ marginTop: '8px' }}>
            <span>Base URL</span>
            <input value={baseUrl} onChange={(event) => onBaseUrlChange(event.target.value)} placeholder="https://api.provider.com/v1" />
          </label>
        </details>
      )}

      <div className="model-selector-footer">
        <span>{selected?.enabled ? 'Provider enabled' : 'Provider disabled'}</span>
        <button className="primary-action" onClick={onSave} disabled={saving || !providerId || !model.trim()} type="button">
          {saving ? <LoaderCircle size={16} className="spin" /> : <CheckCircle2 size={16} />}
          Apply model
        </button>
      </div>
    </section>
  );
};

interface SettingsModalProps {
  isOpen: boolean;
  settings: AppSettings;
  initialTab?: 'models' | 'general' | 'connectors' | 'voice' | 'developer' | 'tools';
  onClose: () => void;
  onSave: (newSettings: AppSettings) => void;
}

export interface AppConnectorConfig {
  id: string;
  name: string;
  category: 'developer' | 'productivity' | 'messaging' | 'media';
  desc: string;
  icon: any;
  authType: 'apiKey' | 'oauth' | 'phone' | 'webhook';
  placeholder: string;
}

const REAL_APP_CONNECTORS: AppConnectorConfig[] = [
  { id: 'whatsapp',  name: 'WhatsApp Web',        category: 'messaging',    desc: 'Send & receive messages automatically',           icon: MessageSquare, authType: 'phone',   placeholder: 'Phone Number (+1234567890)' },
  { id: 'github',    name: 'GitHub',               category: 'developer',   desc: 'Repo access, PRs, commits & issue tracking',     icon: GitBranch,     authType: 'apiKey',  placeholder: 'ghp_personal_access_token...' },
  { id: 'gdrive',    name: 'Google Drive & Docs',  category: 'productivity', desc: 'Search, read, and edit cloud documents',         icon: FileText,      authType: 'oauth',   placeholder: 'Google OAuth Client Token' },
  { id: 'gmail',     name: 'Gmail',                category: 'messaging',    desc: 'Inbox search, drafting & sending emails',        icon: Mail,          authType: 'apiKey',  placeholder: 'App Specific Password / SMTP Key' },
  { id: 'spotify',   name: 'Spotify',              category: 'media',        desc: 'Playback control, playlist search & volume',     icon: Music,         authType: 'apiKey',  placeholder: 'Spotify Client ID / Secret' },
  { id: 'slack',     name: 'Slack',                category: 'messaging',    desc: 'Channel notifications & direct messages',        icon: Share2,        authType: 'webhook', placeholder: 'https://hooks.slack.com/services/...' },
  { id: 'notion',    name: 'Notion',               category: 'productivity', desc: 'Sync notes, databases & workspace tasks',        icon: Database,      authType: 'apiKey',  placeholder: 'secret_notion_api_token...' },
  { id: 'discord',   name: 'Discord',              category: 'messaging',    desc: 'Server chat automation & voice channel bot',    icon: MessageSquare, authType: 'apiKey',  placeholder: 'Bot Token...' },
  { id: 'youtube',   name: 'YouTube Data API',     category: 'media',        desc: 'Video search, transcript extraction & analysis', icon: Tv,           authType: 'apiKey',  placeholder: 'YouTube Data API Key v3' },
  { id: 'figma',     name: 'Figma',                category: 'developer',   desc: 'Inspect design frames & export assets',          icon: Globe,         authType: 'apiKey',  placeholder: 'figd_personal_access_token...' },
];

// ── Toggle Switch (Gemini / iOS sleek pill) ───────────────────────────────────
const ToggleSwitch: React.FC<{ checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; onClick?: (e: any) => void }> = ({ checked, onChange, disabled, onClick }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    disabled={disabled}
    onClick={(e) => { if (onClick) { onClick(e); } else if (!disabled) { onChange(!checked); } }}
    style={{
      position: 'relative', width: '38px', height: '22px', borderRadius: '12px',
      border: `1px solid ${checked ? 'var(--primary)' : 'var(--border-subtle)'}`,
      background: checked ? 'var(--primary)' : 'var(--bg-canvas)',
      cursor: disabled ? 'not-allowed' : 'pointer', transition: 'all 0.2s', flexShrink: 0, outline: 'none',
    }}
  >
    <span style={{
      position: 'absolute', top: '2px', left: checked ? '18px' : '2px',
      width: '16px', height: '16px', borderRadius: '50%',
      background: '#ffffff',
      transition: 'left 0.2s',
      boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
    }} />
  </button>
);

// ── Section Label ─────────────────────────────────────────────────────────────
const SLabel: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <div style={{
    fontSize: '0.74rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
    color: 'var(--text-muted)', paddingBottom: '6px', borderBottom: '1px solid var(--border-subtle)',
    marginBottom: '8px', marginTop: '12px', fontFamily: 'var(--font-sans)',
    ...style,
  }}>{children}</div>
);

// ── Setting Row ───────────────────────────────────────────────────────────────
const SettingRow: React.FC<{ label: string; desc?: string; icon?: React.ReactNode; control: React.ReactNode }> = ({ label, desc, icon, control }) => (
  <div style={{
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '12px 14px', borderRadius: 'var(--radius-md)',
    border: '1px solid var(--border-subtle)', background: 'var(--bg-surface-elevated)', gap: '14px',
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0 }}>
      {icon && <span style={{ color: 'var(--primary)', flexShrink: 0 }}>{icon}</span>}
      <div>
        <div style={{ fontSize: '0.84rem', fontWeight: 600, color: 'var(--text-primary)' }}>{label}</div>
        {desc && <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '2px', lineHeight: 1.4 }}>{desc}</div>}
      </div>
    </div>
    {control}
  </div>
);

// ── Styled Input ──────────────────────────────────────────────────────────────
const SInput = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>((props, ref) => (
  <input
    ref={ref}
    {...props}
    style={{
      width: '100%', padding: '9px 12px', borderRadius: 'var(--radius-sm)',
      border: '1px solid var(--border-subtle)', background: 'var(--bg-canvas)',
      color: 'var(--text-primary)', fontSize: '0.84rem', fontFamily: 'inherit',
      outline: 'none', transition: 'border-color 0.15s, box-shadow 0.15s',
      ...props.style,
    }}
    onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--primary)'; props.onFocus?.(e); }}
    onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; props.onBlur?.(e); }}
  />
));

// ── Styled Select ─────────────────────────────────────────────────────────────
const SSelect: React.FC<React.SelectHTMLAttributes<HTMLSelectElement>> = ({ children, ...props }) => (
  <select
    {...props}
    style={{
      width: '100%', padding: '9px 12px', borderRadius: 'var(--radius-sm)',
      border: '1px solid var(--border-subtle)', background: 'var(--bg-canvas)',
      color: 'var(--text-primary)', fontSize: '0.84rem', outline: 'none', cursor: 'pointer',
      fontFamily: 'inherit',
      ...props.style,
    }}
  >{children}</select>
);

// ── Action Button ─────────────────────────────────────────────────────────────
const ActionBtn: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }> = ({ children, variant = 'ghost', style, disabled, ...props }) => {
  const v: Record<string, React.CSSProperties> = {
    primary: { border: '1px solid var(--primary)', background: 'var(--primary)', color: '#ffffff' },
    ghost:   { border: '1px solid var(--border-subtle)', background: 'var(--bg-surface-elevated)', color: 'var(--text-secondary)' },
    danger:  { border: '1px solid rgba(248, 113, 113, 0.4)', background: 'var(--danger-subtle)', color: 'var(--danger)' },
  };
  return (
    <button
      {...props}
      disabled={disabled}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '6px',
        padding: '8px 14px', borderRadius: 'var(--radius-sm)', cursor: disabled ? 'not-allowed' : 'pointer',
        fontSize: '0.78rem', fontWeight: 600,
        transition: 'all 0.15s', fontFamily: 'inherit',
        ...(v[variant] || v.ghost),
        ...(disabled ? { opacity: 0.4 } : {}),
        ...style,
      }}
    >{children}</button>
  );
};

// ── Main Component ────────────────────────────────────────────────────────────
export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, settings, initialTab, onClose, onSave }) => {
  const [activeTab, setActiveTab] = useState<'models' | 'general' | 'connectors' | 'voice' | 'developer' | 'tools'>(initialTab || 'models');
  const [selectedConnector, setSelectedConnector] = useState<AppConnectorConfig | null>(null);
  const [connectorKeys, setConnectorKeys] = useState<Record<string, string>>(() => {
    const saved = localStorage.getItem('makima_connector_keys');
    return saved ? JSON.parse(saved) : {};
  });

  const [llmProvider, setLlmProvider] = useState(settings.llmProvider || 'groq');
  const [model, setModel]             = useState(settings.model);
  const [apiKey, setApiKey]           = useState('');
  const [baseUrl, setBaseUrl]         = useState('');
  const [providers, setProviders]     = useState<LLMProvider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(false);
  const [providersSaving, setProvidersSaving]   = useState(false);
  const [providerError, setProviderError]       = useState('');
  const [providerSaved, setProviderSaved]       = useState('');

  const [persona, setPersona]           = useState(settings.persona || 'general');
  const [systemPrompt, setSystemPrompt] = useState(settings.systemPrompt || '');
  const [privacyMode, setPrivacyMode]   = useState(settings.privacyMode || false);
  const [autonomyMode, setAutonomyMode] = useState<'off' | 'suggest' | 'auto'>(
    () => (localStorage.getItem('makima_autonomy_mode') as 'off' | 'suggest' | 'auto') || 'suggest'
  );

  useEffect(() => {
    const onAutonomy = (e: Event) => {
      const mode = (e as CustomEvent).detail?.mode;
      if (mode === 'off' || mode === 'suggest' || mode === 'auto') {
        setAutonomyMode(mode);
        localStorage.setItem('makima_autonomy_mode', mode);
      }
    };
    window.addEventListener('makima-autonomy', onAutonomy);
    return () => window.removeEventListener('makima-autonomy', onAutonomy);
  }, []);

  const [wsUrl, setWsUrl]               = useState(settings.wsUrl || 'ws://127.0.0.1:8080/ws');
  const [wsConnected, setWsConnected]   = useState(false);
  const [pingLatency, setPingLatency]   = useState<number | null>(null);
  const [pingLoading, setPingLoading]   = useState(false);
  const [pingError, setPingError]       = useState('');
  const [ollamaModelName, setOllamaModelName] = useState('');
  const [ollamaLoading, setOllamaLoading]     = useState(false);
  const [ollamaStatus, setOllamaStatus]       = useState('');

  useEffect(() => {
    const unsub = wsClient.onStatusChange((connected) => setWsConnected(connected));
    return unsub;
  }, []);

  const [wakeWordEnabled, setWakeWordEnabled]   = useState(settings.wakeWordEnabled);
  const [autoReadAloud, setAutoReadAloud]       = useState(settings.autoReadAloud || false);
  const [ttsSpeed, setTtsSpeed]                 = useState(settings.ttsSpeed || 1.0);
  const [followupTimeoutSeconds, setFollowupTimeoutSeconds] = useState(settings.followupTimeoutSeconds || 20);
  const [silenceTimeoutMs, setSilenceTimeoutMs] = useState(settings.silenceTimeoutMs || 850);
  const [maxUtteranceSeconds, setMaxUtteranceSeconds] = useState(settings.maxUtteranceSeconds || 30);
  const [voiceLanguage, setVoiceLanguage]       = useState(settings.voiceLanguage || 'auto');
  const [isTestingVoice, setIsTestingVoice]     = useState(false);

  const [connectors, setConnectors] = useState<Record<string, boolean>>({
    whatsapp: true, github: true, gdrive: true, gmail: true, spotify: true,
    slack: false, notion: false, discord: false, youtube: true, figma: false,
    ...settings.connectors,
  });

  // ── Tools & MCP state ──────────────────────────────────────────────────────
  const [toolsList, setToolsList] = useState<ToolEntry[]>([]);
  const [toolsCategories, setToolsCategories] = useState<string[]>([]);
  const [toolsCategoryFilter, setToolsCategoryFilter] = useState<string>('');
  const [toolsSearch, setToolsSearch] = useState('');
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsError, setToolsError] = useState('');
  const [toolsTotal, setToolsTotal] = useState(0);
  const [toolsEnabledCount, setToolsEnabledCount] = useState(0);

  const [mcpServers, setMcpServers] = useState<McpServerEntry[]>([]);
  const [mcpTotalTools, setMcpTotalTools] = useState(0);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpError, setMcpError] = useState('');
  const [mcpStatus, setMcpStatus] = useState('');
  const [mcpFormOpen, setMcpFormOpen] = useState(false);
  const [mcpFormName, setMcpFormName] = useState('');
  const [mcpFormCommand, setMcpFormCommand] = useState('');
  const [mcpFormUrl, setMcpFormUrl] = useState('');
  const [mcpFormTransport, setMcpFormTransport] = useState<'stdio' | 'http' | 'sse'>('stdio');
  const [mcpFormSaving, setMcpFormSaving] = useState(false);

  const refreshTools = useCallback(async () => {
    setToolsLoading(true); setToolsError('');
    try {
      const snap = await listTools(wsUrl);
      if (!snap.ok) { setToolsError(snap.error || 'Failed to load tools.'); return; }
      setToolsList(snap.tools || []);
      setToolsCategories(snap.categories || []);
      setToolsTotal(snap.total ?? (snap.tools || []).length);
      setToolsEnabledCount(snap.enabled_count ?? (snap.tools || []).filter((t) => t.enabled).length);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not load tools.';
      setToolsError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — start the backend to load tools.'
        : msg);
    } finally { setToolsLoading(false); }
  }, [wsUrl]);

  const refreshMcp = useCallback(async () => {
    setMcpLoading(true); setMcpError('');
    try {
      const snap = await listMcpServers(wsUrl);
      if (!snap.ok) { setMcpError(snap.error || 'Failed to load MCP servers.'); return; }
      setMcpServers(snap.servers || []);
      setMcpTotalTools(snap.total_mcp_tools ?? 0);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not load MCP servers.';
      setMcpError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — start the backend to manage MCP servers.'
        : msg);
    } finally { setMcpLoading(false); }
  }, [wsUrl]);

  useEffect(() => {
    if (!isOpen || activeTab !== 'tools') return;
    void refreshTools();
    void refreshMcp();
  }, [isOpen, activeTab, refreshTools, refreshMcp]);

  useEffect(() => {
    if (!mcpStatus) return;
    const t = window.setTimeout(() => setMcpStatus(''), 6000);
    return () => window.clearTimeout(t);
  }, [mcpStatus]);

  const handleToggleTool = async (tool: ToolEntry) => {
    const next = !tool.enabled;
    setToolsList((prev) => prev.map((t) => (t.name === tool.name ? { ...t, enabled: next } : t)));
    try {
      const res = await setToolEnabled(tool.name, next, wsUrl);
      if (!res.ok) {
        setToolsList((prev) => prev.map((t) => (t.name === tool.name ? { ...t, enabled: !next } : t)));
        const msg = res.error || 'Failed to update tool.';
        setToolsError(/failed to fetch|networkerror|load failed/i.test(msg)
          ? 'Brain is offline — tool toggle not saved.'
          : msg);
      } else {
        setToolsEnabledCount((c) => c + (next ? 1 : -1));
        setToolsError('');
      }
    } catch (err) {
      setToolsList((prev) => prev.map((t) => (t.name === tool.name ? { ...t, enabled: !next } : t)));
      const msg = err instanceof Error ? err.message : 'Failed to update tool.';
      setToolsError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — tool toggle not saved.'
        : msg);
    }
  };

  const handleAddMcp = async () => {
    const name = mcpFormName.trim();
    if (!name) { setMcpError('Server name is required.'); return; }
    const transport = mcpFormTransport;
    const payload: Parameters<typeof addMcpServer>[0] = { name, enabled: true, transport };
    if (transport === 'stdio') {
      const cmd = mcpFormCommand.trim();
      if (!cmd) { setMcpError('Command is required for stdio transport.'); return; }
      payload.command = cmd;
    } else {
      const url = mcpFormUrl.trim();
      if (!url) { setMcpError('URL is required for http/sse transport.'); return; }
      payload.url = url;
    }
    setMcpFormSaving(true); setMcpError(''); setMcpStatus('');
    try {
      const res = await addMcpServer(payload, wsUrl);
      if (!res.ok) { setMcpError(res.error || 'Failed to add MCP server.'); return; }
      setMcpStatus(`Added "${name}" — reloaded.`);
      setMcpFormOpen(false); setMcpFormName(''); setMcpFormCommand(''); setMcpFormUrl('');
      await refreshMcp();
      await refreshTools();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to add MCP server.';
      setMcpError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — cannot add MCP server.'
        : msg);
    } finally { setMcpFormSaving(false); }
  };

  const handleDeleteMcp = async (name: string) => {
    if (!window.confirm(`Remove MCP server "${name}"? It will be unregistered on reload.`)) return;
    setMcpError(''); setMcpStatus('');
    try {
      const res = await deleteMcpServer(name, wsUrl);
      if (!res.ok) { setMcpError(res.error || 'Failed to delete MCP server.'); return; }
      setMcpStatus(`Removed "${name}" — reloaded.`);
      await refreshMcp();
      await refreshTools();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to delete MCP server.';
      setMcpError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — cannot delete MCP server.'
        : msg);
    }
  };

  const handleReloadMcp = async () => {
    setMcpError(''); setMcpStatus(''); setMcpLoading(true);
    try {
      const res = await reloadMcpServers(wsUrl);
      if (!res.ok) { setMcpError(res.error || 'Reload failed.'); return; }
      setMcpStatus(`Reloaded: ${res.servers_enabled ?? 0}/${res.servers_configured ?? 0} servers, ${res.mcp_tools_registered ?? 0} tools.`);
      await refreshMcp();
      await refreshTools();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Reload failed.';
      setMcpError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Brain is offline — cannot reload MCP servers.'
        : msg);
    } finally { setMcpLoading(false); }
  };

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  const refreshProviders = useCallback(async (preferredId: string) => {
    setProvidersLoading(true); setProviderError('');
    try {
      const next = await getLLMProviders(wsUrl);
      setProviders(next);
      const active = next.find((p) => p.id === preferredId) || next[0];
      if (active) { setLlmProvider(active.id); setModel((c) => active.models.includes(c) ? c : active.model); setBaseUrl(active.baseUrl || ''); }
    } catch (err) { setProviderError(err instanceof Error ? err.message : 'Could not load providers.'); }
    finally { setProvidersLoading(false); }
  }, [wsUrl]);

  useEffect(() => {
    if (!isOpen) return;
    if (initialTab) setActiveTab(initialTab);
    setLlmProvider(settings.llmProvider || 'groq'); setModel(settings.model); setApiKey('');
    setPersona(settings.persona || 'general'); setSystemPrompt(settings.systemPrompt || '');
    setPrivacyMode(settings.privacyMode || false); setWsUrl(settings.wsUrl || 'ws://127.0.0.1:8080/ws');
    setWakeWordEnabled(settings.wakeWordEnabled); setAutoReadAloud(settings.autoReadAloud || false);
    setTtsSpeed(settings.ttsSpeed || 1.0); setFollowupTimeoutSeconds(settings.followupTimeoutSeconds || 20);
    setSilenceTimeoutMs(settings.silenceTimeoutMs || 850); setMaxUtteranceSeconds(settings.maxUtteranceSeconds || 30);
    setVoiceLanguage(settings.voiceLanguage || 'auto'); setConnectors({ ...settings.connectors });
    void refreshProviders(settings.llmProvider || 'groq');
  }, [isOpen, initialTab, refreshProviders, settings]);

  useEffect(() => { if (isOpen) { setProviderSaved(''); setPingLatency(null); setOllamaStatus(''); } }, [isOpen]);

  if (!isOpen) return null;

  const toggleConnector = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConnectors((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const handleSaveConnectorKey = (id: string, keyVal: string) => {
    const updated = { ...connectorKeys, [id]: keyVal };
    if (!keyVal.trim()) delete updated[id];
    setConnectorKeys(updated);
    localStorage.setItem('makima_connector_keys', JSON.stringify(updated));
    wsClient.saveCredential(id, { apiKey: keyVal });
    if (keyVal.trim()) setConnectors((prev) => ({ ...prev, [id]: true }));
    setSelectedConnector(null);
  };

  const handleProviderChange = (providerId: string) => {
    const p = providers.find((item) => item.id === providerId);
    setLlmProvider(providerId); setModel(p?.model || ''); setBaseUrl(p?.baseUrl || ''); setApiKey(''); setProviderSaved('');
  };

  const handleSaveProvider = async () => {
    setProvidersSaving(true); setProviderError(''); setProviderSaved('');
    try {
      const trimmedKey = apiKey.trim();
      const saved = await saveLLMProvider(llmProvider, {
        apiKey: trimmedKey || undefined, model: model.trim(),
        baseUrl: baseUrl.trim() || undefined,
        enabled: providers.find((p) => p.id === llmProvider)?.enabled ?? true,
      }, wsUrl);
      setProviders((cur) => cur.map((p) => p.id === saved.id ? saved : p));
      setModel(saved.model); setApiKey('');
      const nextApiKeys = { ...(settings.apiKeys || {}) };
      if (trimmedKey) {
        nextApiKeys[saved.id] = trimmedKey;
      }
      onSave({ ...settings, llmProvider: saved.id, model: saved.model, apiKeys: nextApiKeys });
      setProviderSaved(`${saved.name} · ${saved.model} is active.`);
    } catch (err) { setProviderError(err instanceof Error ? err.message : 'Could not save provider.'); }
    finally { setProvidersSaving(false); }
  };

  const handleTestPing = async () => {
    setPingLoading(true);
    setPingLatency(null);
    setPingError('');
    try {
      const rtt = await wsClient.ping();
      setPingLatency(rtt);
    } catch {
      setPingLatency(null);
      setPingError('Brain unreachable — check the WebSocket URL and start the backend.');
    } finally {
      setPingLoading(false);
    }
  };

  const handleTestVoice = () => {
    if (!('speechSynthesis' in window)) { setProviderError('Voice test not supported in this browser.'); return; }
    if (isTestingVoice) { window.speechSynthesis.cancel(); setIsTestingVoice(false); return; }
    window.speechSynthesis.cancel();
    const text = voiceLanguage === 'hi'
      ? 'नमस्ते! मैं माकिमा हूँ, आपका निजी कृत्रिम बुद्धिमत्ता सहायक।'
      : 'Hello! I am Makima, your autonomous AI assistant. All voice channels are operational.';
    const u = new SpeechSynthesisUtterance(text);
    u.rate = ttsSpeed;
    if (voiceLanguage === 'hi') u.lang = 'hi-IN'; else if (voiceLanguage === 'en') u.lang = 'en-US';
    u.onend = () => setIsTestingVoice(false); u.onerror = () => setIsTestingVoice(false);
    window.speechSynthesis.speak(u); setIsTestingVoice(true);
  };

  const handlePullOllamaModel = () => {
    if (!ollamaModelName.trim()) return;
    setOllamaLoading(true); setOllamaStatus(`Triggered pull for "${ollamaModelName}"...`);
    try {
      const socket = (wsClient as any).socket;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ v: 1, type: 'pull_ollama_model', payload: { model_name: ollamaModelName.trim() } }));
        setOllamaStatus(`Download started for ${ollamaModelName}. Check terminal for progress.`);
      } else { setOllamaStatus('WebSocket not connected. Connect to Makima Brain first.'); }
    } catch (e) { setOllamaStatus(`Failed: ${e}`); }
    finally { setOllamaLoading(false); }
  };

  const handleResetDefaults = () => {
    if (window.confirm('Reset all settings to default values? This will reload the page.')) {
      localStorage.removeItem('makima_settings'); localStorage.removeItem('makima_connector_keys');
      window.location.reload();
    }
  };

  const handleSave = () => {
    onSave({ ...settings, llmProvider, model, persona, systemPrompt, privacyMode, wsUrl, wakeWordEnabled, autoReadAloud, ttsSpeed, followupTimeoutSeconds, silenceTimeoutMs, maxUtteranceSeconds, voiceLanguage, connectors });
    onClose();
  };

  const TABS = [
    { id: 'models',     label: 'AI Models',  icon: Cpu      },
    { id: 'connectors', label: 'Connectors', icon: Plug     },
    { id: 'general',    label: 'General',    icon: Sliders  },
    { id: 'voice',      label: 'Voice',      icon: Volume2  },
    { id: 'tools',      label: 'Tools & MCP', icon: Wrench  },
    { id: 'developer',  label: 'Developer',  icon: Terminal },
  ] as const;

  const tabTitles: Record<string, string> = {
    models: 'AI Models & Provider Keys', connectors: 'App Connectors Hub',
    general: 'General & System Persona', voice: 'Voice Pipeline & Speech',
    tools: 'Registered Tools & MCP Servers',
    developer: 'Developer & Network',
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0, 0, 0, 0.7)', backdropFilter: 'blur(10px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px',
      }}
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 6 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 4 }}
        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
        style={{
          width: '100%', maxWidth: '960px',
          height: 'min(720px, calc(100vh - 40px))',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)', display: 'flex', overflow: 'hidden',
          boxShadow: '0 25px 60px -15px rgba(0, 0, 0, 0.85)',
          position: 'relative',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── SIDEBAR ── */}
        <div style={{
          width: '210px', flexShrink: 0,
          background: 'var(--bg-canvas)', borderRight: '1px solid var(--border-subtle)',
          padding: '20px 12px', display: 'flex', flexDirection: 'column', gap: '4px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '20px', paddingLeft: '6px' }}>
            <div style={{ width: 28, height: 28, borderRadius: 'var(--radius-sm)', background: 'var(--primary-subtle)', border: '1px solid var(--primary-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary)', flexShrink: 0 }}>
              <Sliders size={15} />
            </div>
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-primary)' }}>Settings</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Makima Intelligence</div>
            </div>
          </div>

          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '10px',
                  padding: '9px 12px', borderRadius: 'var(--radius-md)', border: 'none',
                  background: active ? 'var(--primary-subtle)' : 'transparent',
                  color: active ? 'var(--primary)' : 'var(--text-secondary)',
                  fontWeight: active ? 600 : 500, fontSize: '0.82rem',
                  cursor: 'pointer', textAlign: 'left', transition: 'all 0.15s',
                  fontFamily: 'inherit',
                }}
              >
                <Icon size={16} /><span>{tab.label}</span>
              </button>
            );
          })}

          <div style={{ marginTop: 'auto', borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>
            <ActionBtn variant="danger" onClick={handleResetDefaults} style={{ width: '100%', justifyContent: 'center', fontSize: '0.75rem', padding: '8px' }}>
              <Trash2 size={13} /> Reset Defaults
            </ActionBtn>
          </div>
        </div>

        {/* ── CONTENT ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, position: 'relative' }}>
          {/* Header bar */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 24px', height: '54px', flexShrink: 0,
            borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-surface)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)' }}>
              {tabTitles[activeTab]}
            </div>
            <button
              onClick={onClose}
              style={{ background: 'transparent', border: '1px solid var(--border-subtle)', color: 'var(--text-muted)', cursor: 'pointer', padding: '6px', borderRadius: 'var(--radius-sm)', display: 'flex', transition: 'all 0.15s' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>

            {/* MODELS */}
            {activeTab === 'models' && (
              <ModelSelectorPanel
                providers={providers} providerId={llmProvider} model={model}
                apiKey={apiKey} baseUrl={baseUrl} loading={providersLoading}
                saving={providersSaving} error={providerError} savedMessage={providerSaved}
                onProviderChange={handleProviderChange} onModelChange={setModel}
                onApiKeyChange={setApiKey} onBaseUrlChange={setBaseUrl}
                onRefresh={() => void refreshProviders(llmProvider)}
                onSave={() => void handleSaveProvider()}
              />
            )}

            {/* CONNECTORS */}
            {activeTab === 'connectors' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <h4 style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px' }}>App Connectors & Integrations</h4>
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0 }}>Authorize external platforms and apps to enable autonomous agent execution.</p>
                </div>
                {(['messaging', 'developer', 'productivity', 'media'] as const).map((cat) => {
                  const items = REAL_APP_CONNECTORS.filter((c) => c.category === cat);
                  const catLabel: Record<string, string> = { messaging: 'Messaging & Chat', developer: 'Developer & Git', productivity: 'Productivity & Workspace', media: 'Media & Playback' };
                  return (
                    <div key={cat} style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <SLabel>{catLabel[cat]}</SLabel>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '10px' }}>
                        {items.map((conn) => {
                          const Icon = conn.icon;
                          const isEnabled = connectors[conn.id] ?? false;
                          const hasKey = Boolean(connectorKeys[conn.id]);
                          return (
                            <div
                              key={conn.id}
                              onClick={() => setSelectedConnector(conn)}
                              style={{
                                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                padding: '12px 14px', borderRadius: 'var(--radius-md)', cursor: 'pointer',
                                border: `1px solid ${isEnabled ? 'var(--primary-border)' : 'var(--border-subtle)'}`,
                                background: isEnabled ? 'var(--primary-subtle)' : 'var(--bg-surface-elevated)',
                                transition: 'all 0.15s',
                              }}
                              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-hover)'; }}
                              onMouseLeave={(e) => { e.currentTarget.style.borderColor = isEnabled ? 'var(--primary-border)' : 'var(--border-subtle)'; }}
                            >
                              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0, flex: 1 }}>
                                <div style={{
                                  width: 32, height: 32, borderRadius: 'var(--radius-sm)', flexShrink: 0,
                                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  background: 'var(--bg-canvas)',
                                  border: '1px solid var(--border-subtle)',
                                  color: isEnabled ? 'var(--primary)' : 'var(--text-muted)',
                                }}>
                                  <Icon size={16} />
                                </div>
                                <div style={{ minWidth: 0, flex: 1 }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                    <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{conn.name}</span>
                                    {hasKey && <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--success-subtle)', color: 'var(--success)', fontWeight: 600 }}>KEYED</span>}
                                  </div>
                                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '2px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{conn.desc}</div>
                                </div>
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, marginLeft: '8px' }}>
                                <button
                                  type="button"
                                  onClick={(e) => { e.stopPropagation(); setSelectedConnector(conn); }}
                                  style={{
                                    padding: '4px 8px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-canvas)',
                                    border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)', fontSize: '0.7rem',
                                    fontWeight: 500, cursor: 'pointer',
                                  }}
                                >
                                  Config
                                </button>
                                <ToggleSwitch checked={isEnabled} onChange={() => {}} onClick={(e) => toggleConnector(conn.id, e)} />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* GENERAL */}
            {activeTab === 'general' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <h4 style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px' }}>General & System Configuration</h4>
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0 }}>Configure system-level instructions, agent persona, and proactive behavior.</p>
                </div>

                <SLabel>Agent Persona & Behavior</SLabel>
                <div>
                  <div style={{ fontSize: '0.78rem', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>System Prompt Mode</div>
                  <SSelect value={persona} onChange={(e) => setPersona(e.target.value as any)}>
                    <option value="general">General Assistant — Balanced reasoning & multi-tasking</option>
                    <option value="coder">Master Software Architect — Deep code gen, refactoring & debug</option>
                    <option value="researcher">Executive Research Analyst — Multi-hop web search & synthesis</option>
                    <option value="creative">Creative Strategist — Storytelling, ideation & copywriting</option>
                  </SSelect>
                </div>

                <SLabel>Custom Instructions</SLabel>
                <div>
                  <textarea
                    rows={4} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)}
                    placeholder="e.g. Always write clean TypeScript with comments. Prefer concise bullet-point explanations..."
                    style={{
                      width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-sm)', resize: 'vertical',
                      background: 'var(--bg-canvas)', border: '1px solid var(--border-subtle)',
                      color: 'var(--text-primary)', fontSize: '0.84rem', fontFamily: 'inherit',
                      outline: 'none', lineHeight: 1.55, transition: 'border-color 0.15s',
                    }}
                    onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--primary)'; }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; }}
                  />
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                    Appended to every prompt sent to Makima's reasoning engine.
                  </div>
                </div>

                <SLabel>Privacy & Ephemeral Mode</SLabel>
                <SettingRow
                  label="Privacy Mode — Ephemeral Sessions"
                  desc="Chat turns are NOT saved to long-term memory or SQLite conversation database."
                  icon={<Shield size={16} />}
                  control={<ToggleSwitch checked={privacyMode} onChange={setPrivacyMode} />}
                />

                <SLabel>Autonomy — Proactive Execution</SLabel>
                <SettingRow
                  label="Autonomous Action Execution"
                  desc="Off = never. Suggest = propose ideas only. Auto = safe tools execute autonomously; risky ones always confirm."
                  icon={<Zap size={16} />}
                  control={
                    <div style={{ display: 'flex', gap: '6px' }}>
                      {(['off', 'suggest', 'auto'] as const).map((m) => (
                        <button
                          key={m}
                          type="button"
                          onClick={() => { setAutonomyMode(m); localStorage.setItem('makima_autonomy_mode', m); wsClient.setAutonomyMode(m); }}
                          style={{
                            padding: '5px 12px', fontSize: '0.75rem', fontWeight: autonomyMode === m ? 600 : 500,
                            borderRadius: 'var(--radius-sm)', cursor: 'pointer', textTransform: 'capitalize',
                            border: `1px solid ${autonomyMode === m ? 'var(--primary)' : 'var(--border-subtle)'}`,
                            background: autonomyMode === m ? 'var(--primary-subtle)' : 'var(--bg-canvas)',
                            color: autonomyMode === m ? 'var(--primary)' : 'var(--text-secondary)',
                            transition: 'all 0.15s',
                          }}
                        >{m}</button>
                      ))}
                    </div>
                  }
                />
              </div>
            )}

            {/* VOICE */}
            {activeTab === 'voice' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <h4 style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px' }}>Voice & Speech Pipeline</h4>
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0 }}>Configure speech recognition (STT), speech synthesis (TTS), and wake word listening.</p>
                </div>

                <SLabel>Hands-Free Activation</SLabel>
                <SettingRow label={'Wake Word — "Hey Makima"'} desc="Continuously listens via local OpenWakeWord engine on device." icon={<Zap size={16} />} control={<ToggleSwitch checked={wakeWordEnabled} onChange={setWakeWordEnabled} />} />
                <SettingRow label="Auto Read Aloud AI Responses" desc="Automatically synthesize speech when assistant finishes streaming." icon={<Volume2 size={16} />} control={<ToggleSwitch checked={autoReadAloud} onChange={setAutoReadAloud} />} />

                <SLabel>Speech Synthesis (TTS)</SLabel>
                <div style={{ padding: '14px 16px', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)', background: 'var(--bg-surface-elevated)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <span style={{ fontSize: '0.82rem', fontWeight: 500, color: 'var(--text-secondary)' }}>TTS Playback Speed</span>
                    <span style={{ fontSize: '0.82rem', color: 'var(--primary)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{ttsSpeed.toFixed(1)}×</span>
                  </div>
                  <input type="range" min="0.5" max="2.0" step="0.1" value={ttsSpeed}
                    onChange={(e) => setTtsSpeed(parseFloat(e.target.value))}
                    style={{ width: '100%', cursor: 'pointer', accentColor: 'var(--primary)' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>0.5× (Slow)</span>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>2.0× (Fast)</span>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '10px' }}>
                  {[
                    { label: 'Follow-up Window', unit: 's',  val: followupTimeoutSeconds, set: setFollowupTimeoutSeconds, min: 5,   max: 120, step: 1  },
                    { label: 'Silence End',       unit: 'ms', val: silenceTimeoutMs,       set: setSilenceTimeoutMs,       min: 400, max: 3000, step: 50 },
                    { label: 'Max Turn',          unit: 's',  val: maxUtteranceSeconds,     set: setMaxUtteranceSeconds,    min: 3,   max: 60,  step: 1  },
                  ].map((f) => (
                    <div key={f.label} style={{ display: 'flex', flexDirection: 'column', gap: '5px', minWidth: 0 }}>
                      <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>{f.label} ({f.unit})</div>
                      <SInput type="number" min={f.min} max={f.max} step={f.step} value={f.val}
                        onChange={(e) => {
                          const n = Number(e.target.value);
                          f.set(Number.isFinite(n) ? Math.min(f.max, Math.max(f.min, n)) : f.min);
                        }} />
                    </div>
                  ))}
                </div>

                <SLabel>Language & Test</SLabel>
                <div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: '5px' }}>Voice Language</div>
                  <SSelect value={voiceLanguage} onChange={(e) => setVoiceLanguage(e.target.value as any)}>
                    <option value="auto">Auto (Hindi / English)</option>
                    <option value="hi">Hindi (हिन्दी)</option>
                    <option value="en">English (US / UK)</option>
                  </SSelect>
                </div>
                <ActionBtn variant={isTestingVoice ? 'danger' : 'primary'} onClick={handleTestVoice} style={{ alignSelf: 'flex-start' }}>
                  {isTestingVoice ? <VolumeX size={14} /> : <Play size={14} />}
                  {isTestingVoice ? 'Stop Test' : 'Test Voice Output'}
                </ActionBtn>
              </div>
            )}

            {/* TOOLS & MCP */}
            {activeTab === 'tools' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                <div>
                  <h4 style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px' }}>Registered Tools & MCP Servers</h4>
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0 }}>
                    Inspect live ToolRegistry, toggle tools on/off, and manage external MCP servers.
                  </p>
                </div>

                {toolsError && <div className="inline-alert error">{toolsError}</div>}
                {mcpError && <div className="inline-alert error">{mcpError}</div>}
                {mcpStatus && <div className="inline-alert success"><CheckCircle2 size={15} /> {mcpStatus}</div>}

                {/* ── Tools section ── */}
                <section style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    gap: '10px', flexWrap: 'wrap',
                    paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)',
                  }}>
                    <div style={{ fontSize: '0.74rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--text-muted)', fontFamily: 'var(--font-sans)', display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
                      <span>Tools · {toolsEnabledCount}/{toolsTotal} enabled</span>
                      {toolsLoading && <LoaderCircle size={13} className="spin" />}
                    </div>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                      <SInput
                        type="text" placeholder="Search tools…" value={toolsSearch}
                        onChange={(e) => setToolsSearch(e.target.value)}
                        style={{ width: '180px', fontSize: '0.78rem', padding: '6px 10px' }}
                      />
                      <SSelect
                        value={toolsCategoryFilter}
                        onChange={(e) => setToolsCategoryFilter(e.target.value)}
                        style={{ width: '150px', fontSize: '0.78rem', padding: '6px 8px' }}
                      >
                        <option value="">All categories</option>
                        {toolsCategories.map((c) => <option key={c} value={c}>{c}</option>)}
                      </SSelect>
                      <ActionBtn variant="ghost" onClick={() => { void refreshTools(); void refreshMcp(); }} title="Refresh" style={{ padding: '6px 10px' }}>
                        <RefreshCw size={13} className={toolsLoading ? 'spin' : ''} />
                      </ActionBtn>
                    </div>
                  </div>

                  <div style={{
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--border-subtle)',
                    background: 'var(--bg-surface-elevated)',
                    overflow: 'hidden',
                  }}>
                    {(() => {
                      const q = toolsSearch.trim().toLowerCase();
                      const filtered = toolsList.filter((t) =>
                        (!toolsCategoryFilter || t.category === toolsCategoryFilter) &&
                        (!q || t.name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q))
                      );
                      if (filtered.length === 0) {
                        return (
                          <div style={{ padding: '20px 16px', fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                            {toolsLoading ? 'Loading tools…' : toolsError ? 'Waiting for tool registry…' : 'No tools match.'}
                          </div>
                        );
                      }
                      return filtered.map((t, i) => (
                        <div key={t.name} style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
                          padding: '8px 12px',
                          borderBottom: i < filtered.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                          background: t.enabled ? 'transparent' : 'var(--bg-canvas)',
                          opacity: t.enabled ? 1 : 0.65,
                        }}>
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: t.enabled ? 'var(--text-primary)' : 'var(--text-muted)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{t.name}</span>
                              <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--bg-canvas)', border: '1px solid var(--border-subtle)', color: 'var(--text-muted)' }}>{t.category}</span>
                              {t.source === 'mcp' && <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--primary-subtle)', color: 'var(--primary)' }}>MCP</span>}
                              {t.is_destructive && <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--danger-subtle)', color: 'var(--danger)' }}>RISKY</span>}
                            </div>
                            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>{t.description || '—'}</div>
                          </div>
                          <ToggleSwitch checked={t.enabled} onChange={() => void handleToggleTool(t)} />
                        </div>
                      ));
                    })()}
                  </div>
                </section>

                {/* ── MCP section ── */}
                <section style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    gap: '10px', flexWrap: 'wrap',
                    paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)',
                  }}>
                    <div style={{ fontSize: '0.74rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--text-muted)', fontFamily: 'var(--font-sans)', display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0, flexWrap: 'wrap' }}>
                      <span>MCP Servers · {mcpServers.length} configured · {mcpTotalTools} tools</span>
                      {mcpLoading && <LoaderCircle size={13} className="spin" />}
                    </div>
                    <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                      <ActionBtn variant="ghost" onClick={() => void handleReloadMcp()} disabled={mcpLoading} title="Hot-reload MCP servers" style={{ padding: '6px 12px' }}>
                        <RefreshCw size={13} className={mcpLoading ? 'spin' : ''} /> Reload
                      </ActionBtn>
                      <ActionBtn variant="primary" onClick={() => setMcpFormOpen((v) => !v)} style={{ padding: '6px 12px' }}>
                        <Plus size={13} /> {mcpFormOpen ? 'Close' : 'Add Server'}
                      </ActionBtn>
                    </div>
                  </div>

                  {mcpFormOpen && (
                    <div style={{
                      display: 'flex', flexDirection: 'column', gap: '10px', padding: '12px 14px',
                      borderRadius: 'var(--radius-md)', border: '1px solid var(--primary-border)',
                      background: 'var(--primary-subtle)',
                    }}>
                      <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-primary)' }}>Add external MCP server</div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '10px' }}>
                        <div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Name</div>
                          <SInput value={mcpFormName} onChange={(e) => setMcpFormName(e.target.value)} placeholder="e.g. filesystem" />
                        </div>
                        <div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Transport</div>
                          <SSelect value={mcpFormTransport} onChange={(e) => setMcpFormTransport(e.target.value as any)}>
                            <option value="stdio">stdio (local command)</option>
                            <option value="http">http (streamable)</option>
                            <option value="sse">sse</option>
                          </SSelect>
                        </div>
                      </div>
                      {mcpFormTransport === 'stdio' ? (
                        <div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Command</div>
                          <SInput value={mcpFormCommand} onChange={(e) => setMcpFormCommand(e.target.value)} placeholder='npx -y @modelcontextprotocol/server-filesystem C:/Users' style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }} />
                        </div>
                      ) : (
                        <div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>URL</div>
                          <SInput value={mcpFormUrl} onChange={(e) => setMcpFormUrl(e.target.value)} placeholder="https://example.com/mcp" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }} />
                        </div>
                      )}
                      <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                        <ActionBtn variant="ghost" onClick={() => setMcpFormOpen(false)}>Cancel</ActionBtn>
                        <ActionBtn variant="primary" onClick={() => void handleAddMcp()} disabled={mcpFormSaving}>
                          {mcpFormSaving ? <LoaderCircle size={13} className="spin" /> : <Plus size={13} />} Add & Reload
                        </ActionBtn>
                      </div>
                    </div>
                  )}

                  <div style={{
                    borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)',
                    background: 'var(--bg-surface-elevated)', overflow: 'hidden',
                  }}>
                    {mcpServers.length === 0 && !mcpLoading && (
                      <div style={{ padding: '20px 16px', fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                        {mcpError ? 'MCP list unavailable.' : 'No MCP servers configured. Add one above (stdio command or http URL).'}
                      </div>
                    )}
                    {mcpServers.map((s, i) => (
                      <div key={s.name} style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
                        padding: '10px 14px',
                        borderBottom: i < mcpServers.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                      }}>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                            <span style={{ fontSize: '0.84rem', fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{s.name}</span>
                            <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--bg-canvas)', border: '1px solid var(--border-subtle)', color: 'var(--text-muted)' }}>{s.transport}</span>
                            {s.live
                              ? <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--success-subtle)', color: 'var(--success)', fontWeight: 600 }}>LIVE · {s.tools_registered}</span>
                              : s.enabled
                                ? <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--warning-subtle)', color: 'var(--warning)', fontWeight: 600 }}>OFFLINE</span>
                                : <span style={{ fontSize: '0.62rem', padding: '1px 6px', borderRadius: 'var(--radius-full)', background: 'var(--bg-canvas)', border: '1px solid var(--border-subtle)', color: 'var(--text-muted)' }}>DISABLED</span>}
                          </div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
                            {s.command ? (Array.isArray(s.command) ? s.command.join(' ') : s.command) : s.url || '—'}
                          </div>
                        </div>
                        <ActionBtn variant="ghost" onClick={() => void handleDeleteMcp(s.name)} title="Remove server" style={{ padding: '6px 10px', flexShrink: 0 }}>
                          <Trash2 size={13} />
                        </ActionBtn>
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            )}

            {/* DEVELOPER */}
            {activeTab === 'developer' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <h4 style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px' }}>Developer Diagnostics & WebSocket</h4>
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0 }}>Inspect network latency, manage local models, and verify server protocols.</p>
                </div>

                <SLabel>WebSocket Connectivity</SLabel>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>Makima Brain WebSocket URL</div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <SInput type="text" value={wsUrl} onChange={(e) => setWsUrl(e.target.value)} style={{ flex: 1, minWidth: '180px', fontFamily: 'var(--font-mono)', fontSize: '0.82rem' }} />
                    <ActionBtn variant="primary" onClick={handleTestPing} disabled={pingLoading} style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
                      <Activity size={13} className={pingLoading ? 'spin' : ''} />
                      {pingLoading ? 'Pinging...' : 'Ping'}
                    </ActionBtn>
                  </div>
                  {pingError && <div className="inline-alert error">{pingError}</div>}
                  {pingLatency !== null && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.78rem', color: 'var(--success)' }}>
                      <CheckCircle2 size={14} /> Pong received · <strong style={{ fontFamily: 'var(--font-mono)' }}>{pingLatency}ms latency</strong>
                    </div>
                  )}
                </div>

                <SLabel>Protocol Status</SLabel>
                <div style={{ padding: '12px 14px', borderRadius: 'var(--radius-md)', background: 'var(--bg-surface-elevated)', border: '1px solid var(--border-subtle)', fontFamily: 'var(--font-mono)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: wsConnected ? 'var(--success)' : 'var(--status-red, #ef4444)',
                        display: 'inline-block',
                      }}
                    />
                    <span style={{ fontSize: '0.78rem', color: 'var(--text-primary)', fontWeight: 600 }}>
                      {wsConnected ? 'WebSocket connected (protocol v1)' : 'WebSocket not connected'}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    Endpoints on brain: /ws · /health · /llm/providers · /media · /status — use Ping above for RTT
                  </div>
                </div>

                <SLabel>Local Ollama Model Manager</SLabel>
                <div style={{ padding: '14px 16px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', background: 'var(--bg-surface-elevated)' }}>
                  <div style={{ fontSize: '0.84rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '3px' }}>Pull Local Model into Ollama</div>
                  <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginBottom: '10px' }}>
                    Available models: <code style={{ color: 'var(--primary)' }}>llama3.2</code>, <code style={{ color: 'var(--primary)' }}>qwen2.5-coder:7b</code>, <code style={{ color: 'var(--primary)' }}>deepseek-r1:1.5b</code>
                  </div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <SInput type="text" placeholder="e.g. llama3.2, deepseek-r1:1.5b" value={ollamaModelName}
                      onChange={(e) => setOllamaModelName(e.target.value)}
                      style={{ flex: 1, minWidth: '180px', fontFamily: 'var(--font-mono)', fontSize: '0.82rem' }}
                      onKeyDown={(e) => { if (e.key === 'Enter') handlePullOllamaModel(); }}
                    />
                    <ActionBtn variant="primary" onClick={handlePullOllamaModel} disabled={ollamaLoading || !ollamaModelName.trim()} style={{ flexShrink: 0 }}>
                      <RefreshCw size={13} className={ollamaLoading ? 'spin' : ''} /> Pull
                    </ActionBtn>
                  </div>
                  {ollamaStatus && (
                    <div style={{ marginTop: '8px', fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>› {ollamaStatus}</div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px',
            padding: '12px 24px', flexShrink: 0,
            borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-surface)',
          }}>
            <ActionBtn variant="ghost" onClick={onClose}>Cancel</ActionBtn>
            <ActionBtn variant="primary" onClick={handleSave}><Check size={13} /> Save Changes</ActionBtn>
          </div>

          {/* Connector sub-modal */}
          {selectedConnector && (
            <ConnectorConfigSubModal
              connector={selectedConnector}
              initialKey={connectorKeys[selectedConnector.id] || ''}
              wsUrl={wsUrl}
              onClose={() => setSelectedConnector(null)}
              onSaveKey={(val) => handleSaveConnectorKey(selectedConnector.id, val)}
              onClearKey={() => handleSaveConnectorKey(selectedConnector.id, '')}
            />
          )}
        </div>
      </motion.div>
    </motion.div>
  );
};

// Connector id → backend OAuth provider (auth/oauth_manager.PROVIDERS keys)
const OAUTH_PROVIDER_BY_CONNECTOR: Record<string, string> = {
  gdrive: 'google',
  gmail: 'google',
  github: 'github',
};

// ── Connector Config Sub-Modal ────────────────────────────────────────────────
const ConnectorConfigSubModal: React.FC<{
  connector: AppConnectorConfig;
  initialKey: string;
  wsUrl?: string;
  onClose: () => void;
  onSaveKey: (val: string) => void;
  onClearKey: () => void;
}> = ({ connector, initialKey, wsUrl, onClose, onSaveKey, onClearKey }) => {
  const [val, setVal]           = useState(initialKey);
  const [showKey, setShowKey]   = useState(false);
  const [testing, setTesting]   = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const Icon                    = connector.icon;

  const oauthProvider = OAUTH_PROVIDER_BY_CONNECTOR[connector.id];
  const [oauthConnected, setOauthConnected] = useState(false);
  const [oauthBusy, setOauthBusy] = useState(false);
  const oauthPollRef = React.useRef<number | null>(null);

  const clearOauthPoll = useCallback(() => {
    if (oauthPollRef.current !== null) {
      window.clearInterval(oauthPollRef.current);
      oauthPollRef.current = null;
    }
    setOauthBusy(false);
  }, []);

  const refreshOauth = useCallback(async () => {
    if (!oauthProvider) return;
    try {
      const providers = await oauthStatus(wsUrl);
      setOauthConnected(Boolean(providers[oauthProvider]));
    } catch {
      /* brain offline — keep last known state */
    }
  }, [oauthProvider, wsUrl]);

  useEffect(() => {
    void refreshOauth();
    return clearOauthPoll;
  }, [refreshOauth, clearOauthPoll]);

  const handleOauthConnect = () => {
    if (!oauthProvider) return;
    if (oauthPollRef.current !== null) return;
    setOauthBusy(true);
    window.open(oauthLoginUrl(oauthProvider, wsUrl), 'makima_oauth', 'width=540,height=720');
    const startedAt = Date.now();
    oauthPollRef.current = window.setInterval(async () => {
      try {
        const providers = await oauthStatus(wsUrl);
        if (providers[oauthProvider]) {
          setOauthConnected(true);
          clearOauthPoll();
        } else if (Date.now() - startedAt > 60000) {
          clearOauthPoll();
        }
      } catch {
        if (Date.now() - startedAt > 60000) clearOauthPoll();
      }
    }, 2000);
  };

  const handleOauthDisconnect = async () => {
    if (!oauthProvider) return;
    try {
      const res = await oauthDisconnect(oauthProvider, wsUrl);
      if (res.ok) setOauthConnected(false);
    } catch {
      /* ignore — brain offline */
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      if (val.trim()) {
        wsClient.saveCredential(connector.id, { apiKey: val.trim() });
      }
      const res = await testIntegration(connector.id, wsUrl);
      setTestResult({ ok: res.ok, message: res.message });
    } catch (err: any) {
      setTestResult({ ok: false, message: err?.message || 'Connection test failed or brain offline' });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div style={{
      position: 'absolute', inset: 0, background: 'rgba(0, 0, 0, 0.7)',
      backdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center',
      justifyContent: 'center', zIndex: 110, padding: '24px',
    }}>
      <div style={{
        width: '100%', maxWidth: '440px', maxHeight: 'calc(100vh - 80px)', overflowY: 'auto',
        background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)', padding: '24px', position: 'relative',
        boxShadow: '0 20px 45px rgba(0, 0, 0, 0.8)',
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{ width: 34, height: 34, borderRadius: 'var(--radius-sm)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--primary-subtle)', border: '1px solid var(--primary-border)', color: 'var(--primary)' }}>
              <Icon size={18} />
            </div>
            <div>
              <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)' }}>Configure {connector.name}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'capitalize' }}>{connector.category} · {connector.authType}</div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex' }}><X size={16} /></button>
        </div>

        <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: 1.5 }}>
          {connector.desc}. Enter credentials to authorize Makima OS:
        </p>

        {oauthProvider && (
          <div
            style={{
              marginBottom: '16px',
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              background: 'var(--bg-surface-elevated)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
              <div>
                <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                  OAuth · {oauthProvider}
                </div>
                <div style={{ fontSize: '0.72rem', color: oauthConnected ? 'var(--success)' : 'var(--text-muted)' }}>
                  {oauthConnected ? 'Connected — token stored by Makima Brain' : 'Not connected'}
                </div>
              </div>
              {oauthConnected ? (
                <ActionBtn variant="danger" onClick={() => void handleOauthDisconnect()}>
                  Disconnect
                </ActionBtn>
              ) : (
                <ActionBtn variant="primary" onClick={handleOauthConnect} disabled={oauthBusy}>
                  {oauthBusy ? <LoaderCircle size={13} className="spin" /> : null}
                  {oauthBusy ? 'Waiting…' : 'Sign in with OAuth'}
                </ActionBtn>
              )}
            </div>
          </div>
        )}

        <div style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: 500 }}>Credentials / API Key</div>
          <div style={{ position: 'relative' }}>
            <SInput
              type={showKey ? 'text' : 'password'}
              placeholder={connector.placeholder}
              value={val}
              onChange={(e) => {
                setVal(e.target.value);
                setTestResult(null);
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') onSaveKey(val); }}
              style={{ paddingRight: '36px', fontFamily: 'var(--font-mono)', fontSize: '0.82rem' }}
            />
            <button type="button" onClick={() => setShowKey(!showKey)}
              style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex' }}>
              {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
        </div>

        {testResult && (
          <div
            style={{
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              marginBottom: '16px',
              fontSize: '0.78rem',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: testResult.ok ? 'var(--success-subtle)' : 'var(--danger-subtle)',
              border: `1px solid ${testResult.ok ? 'var(--success)' : 'var(--danger)'}`,
              color: testResult.ok ? 'var(--success)' : 'var(--danger)',
            }}
          >
            {testResult.ok ? <CheckCircle2 size={14} /> : <X size={14} />}
            <span style={{ flex: 1, wordBreak: 'break-word' }}>{testResult.message}</span>
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          {initialKey ? (
            <ActionBtn variant="danger" onClick={() => { if (window.confirm(`Remove saved key for "${connector.name}"?`)) onClearKey(); }}>
              <Trash2 size={13} /> Remove Key
            </ActionBtn>
          ) : <div />}
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <ActionBtn
              variant="ghost"
              onClick={handleTestConnection}
              disabled={testing || (!val.trim() && !initialKey)}
              style={{ border: '1px solid var(--border-subtle)' }}
              title="Verify credential validity with Makima Brain"
            >
              {testing ? <RefreshCw size={13} className="spin" /> : <Activity size={13} />}
              {testing ? 'Testing...' : 'Test Connection'}
            </ActionBtn>
            <ActionBtn variant="ghost" onClick={onClose}>Cancel</ActionBtn>
            <ActionBtn variant="primary" onClick={() => onSaveKey(val)}><Check size={13} /> Save</ActionBtn>
          </div>
        </div>
      </div>
    </div>
  );
};
