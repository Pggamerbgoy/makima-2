import React, { useCallback, useEffect, useState } from 'react';
import {
  X, Check, Sliders, Plug, Volume2, Terminal, Cpu,
  GitBranch, MessageSquare, Mail, Music, FileText, Database, Tv, Share2, Globe,
  Shield, VolumeX, Play, Trash2, Eye, EyeOff, Activity, CheckCircle2, RefreshCw,
  Zap, KeyRound, LoaderCircle, Server, Sparkles
} from 'lucide-react';
import type { AppSettings, LLMProvider } from '../types/chat';
import { wsClient } from '../services/wsClient';
import { getLLMProviders, saveLLMProvider } from '../services/brainApi';

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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', margin: '4px 0 8px' }}>
          <span style={{ fontSize: '0.68rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Quick Select Models for {selected.name}:
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {selected.models.map((m) => {
              const isSelected = model === m;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => onModelChange(m)}
                  style={{
                    padding: '4px 9px',
                    borderRadius: '4px',
                    fontSize: '0.72rem',
                    fontFamily: 'var(--font-mono)',
                    border: `1px solid ${isSelected ? 'rgba(0,210,255,0.6)' : 'rgba(0,180,220,0.15)'}`,
                    background: isSelected ? 'rgba(0,210,255,0.18)' : 'rgba(0,0,0,0.25)',
                    color: isSelected ? '#00d2ff' : 'var(--text-primary)',
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.borderColor = 'rgba(0,210,255,0.35)'; }}
                  onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.borderColor = 'rgba(0,180,220,0.15)'; }}
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
          <label className="field-block">
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
  initialTab?: 'models' | 'general' | 'connectors' | 'voice' | 'developer';
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

// ── Toggle Switch ─────────────────────────────────────────────────────────────
const ToggleSwitch: React.FC<{ checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; onClick?: (e: any) => void }> = ({ checked, onChange, disabled, onClick }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    disabled={disabled}
    onClick={(e) => { if (onClick) { onClick(e); } else if (!disabled) { onChange(!checked); } }}
    style={{
      position: 'relative', width: '36px', height: '19px', borderRadius: '10px',
      border: `1px solid ${checked ? 'rgba(0,210,255,0.45)' : 'rgba(255,255,255,0.08)'}`,
      background: checked ? 'rgba(0,210,255,0.15)' : 'rgba(255,255,255,0.04)',
      cursor: disabled ? 'not-allowed' : 'pointer', transition: 'all 0.2s', flexShrink: 0, outline: 'none',
    }}
  >
    <span style={{
      position: 'absolute', top: '2px', left: checked ? '17px' : '2px',
      width: '13px', height: '13px', borderRadius: '50%',
      background: checked ? '#00d2ff' : '#2a3f55',
      transition: 'left 0.2s, background 0.2s',
      boxShadow: checked ? '0 0 5px rgba(0,210,255,0.5)' : 'none',
    }} />
  </button>
);

// ── Section Label ─────────────────────────────────────────────────────────────
const SLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{
    fontSize: '0.56rem', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase',
    color: 'var(--text-muted)', paddingBottom: '6px', borderBottom: '1px solid rgba(0,180,220,0.1)',
    marginBottom: '6px', fontFamily: 'var(--font-display)',
  }}>{children}</div>
);

// ── Setting Row ───────────────────────────────────────────────────────────────
const SettingRow: React.FC<{ label: string; desc?: string; icon?: React.ReactNode; control: React.ReactNode }> = ({ label, desc, icon, control }) => (
  <div style={{
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '9px 11px', borderRadius: '4px',
    border: '1px solid rgba(0,180,220,0.1)', background: 'rgba(0,0,0,0.15)', gap: '12px',
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: '9px', flex: 1, minWidth: 0 }}>
      {icon && <span style={{ color: 'var(--accent-teal)', flexShrink: 0 }}>{icon}</span>}
      <div>
        <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-primary)' }}>{label}</div>
        {desc && <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginTop: '1px' }}>{desc}</div>}
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
      width: '100%', padding: '7px 10px', borderRadius: '4px',
      border: '1px solid rgba(0,180,220,0.14)', background: 'rgba(0,0,0,0.28)',
      color: 'var(--text-primary)', fontSize: '0.8rem', fontFamily: 'inherit',
      outline: 'none', transition: 'border-color 0.2s',
      ...props.style,
    }}
    onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(0,210,255,0.38)'; props.onFocus?.(e); }}
    onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(0,180,220,0.14)'; props.onBlur?.(e); }}
  />
));

// ── Styled Select ─────────────────────────────────────────────────────────────
const SSelect: React.FC<React.SelectHTMLAttributes<HTMLSelectElement>> = ({ children, ...props }) => (
  <select
    {...props}
    style={{
      width: '100%', padding: '7px 10px', borderRadius: '4px',
      border: '1px solid rgba(0,180,220,0.14)', background: 'rgba(0,0,0,0.28)',
      color: 'var(--text-primary)', fontSize: '0.8rem', outline: 'none', cursor: 'pointer',
      ...props.style,
    }}
  >{children}</select>
);

// ── Action Button ─────────────────────────────────────────────────────────────
const ActionBtn: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }> = ({ children, variant = 'ghost', style, disabled, ...props }) => {
  const v: Record<string, React.CSSProperties> = {
    primary: { border: '1px solid rgba(0,210,255,0.4)', background: 'rgba(0,210,255,0.1)', color: '#00d2ff' },
    ghost:   { border: '1px solid rgba(0,180,220,0.14)', background: 'transparent', color: 'var(--text-secondary)' },
    danger:  { border: '1px solid rgba(255,68,68,0.28)', background: 'rgba(255,68,68,0.07)', color: '#ff4444' },
  };
  return (
    <button
      {...props}
      disabled={disabled}
      style={{
        display: 'flex', alignItems: 'center', gap: '5px',
        padding: '7px 13px', borderRadius: '4px', cursor: disabled ? 'not-allowed' : 'pointer',
        fontSize: '0.73rem', fontWeight: 600, letterSpacing: '0.03em',
        transition: 'all 0.18s', fontFamily: 'inherit',
        ...(v[variant] || v.ghost),
        ...(disabled ? { opacity: 0.38 } : {}),
        ...style,
      }}
    >{children}</button>
  );
};

// ── Main Component ────────────────────────────────────────────────────────────
export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, settings, initialTab, onClose, onSave }) => {
  const [activeTab, setActiveTab] = useState<'models' | 'general' | 'connectors' | 'voice' | 'developer'>(initialTab || 'models');
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
  const [pingLatency, setPingLatency]   = useState<number | null>(null);
  const [pingLoading, setPingLoading]   = useState(false);
  const [ollamaModelName, setOllamaModelName] = useState('');
  const [ollamaLoading, setOllamaLoading]     = useState(false);
  const [ollamaStatus, setOllamaStatus]       = useState('');

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
      const saved = await saveLLMProvider(llmProvider, {
        apiKey: apiKey.trim() || undefined, model: model.trim(),
        baseUrl: baseUrl.trim() || undefined,
        enabled: providers.find((p) => p.id === llmProvider)?.enabled ?? true,
      }, wsUrl);
      setProviders((cur) => cur.map((p) => p.id === saved.id ? saved : p));
      setModel(saved.model); setApiKey('');
      onSave({ ...settings, llmProvider: saved.id, model: saved.model });
      setProviderSaved(`${saved.name} · ${saved.model} is active.`);
    } catch (err) { setProviderError(err instanceof Error ? err.message : 'Could not save provider.'); }
    finally { setProvidersSaving(false); }
  };

  const handleTestPing = () => {
    setPingLoading(true); setPingLatency(null);
    const start = performance.now();
    const unsub = wsClient.onMessage((data) => {
      if (data?.type === 'pong') { setPingLatency(Math.round(performance.now() - start)); setPingLoading(false); unsub(); }
    });
    try { wsClient.sendMessage('__ping__', 'test_ping'); } catch { setPingLoading(false); }
    setTimeout(() => { setPingLoading((l) => { if (l) unsub(); return false; }); }, 4000);
  };

  const handleTestVoice = () => {
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
    { id: 'developer',  label: 'Developer',  icon: Terminal },
  ] as const;

  const tabTitles: Record<string, string> = {
    models: 'AI Models & Provider Keys', connectors: 'App Connectors Hub',
    general: 'General & System Persona', voice: 'Voice Pipeline & Speech',
    developer: 'Developer & Network',
  };

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.84)', backdropFilter: 'blur(14px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: '100%', maxWidth: '900px',
          height: 'min(660px, calc(100vh - 32px))',
          background: 'var(--bg-secondary)',
          border: '1px solid rgba(0,210,255,0.18)',
          borderRadius: '6px', display: 'flex', overflow: 'hidden',
          boxShadow: '0 0 80px rgba(0,0,0,0.9), 0 0 30px rgba(0,210,255,0.05)',
          position: 'relative',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Corner accents */}
        <div style={{ position: 'absolute', top: 0, left: 0, width: 20, height: 20, borderTop: '1px solid rgba(0,210,255,0.55)', borderLeft: '1px solid rgba(0,210,255,0.55)', pointerEvents: 'none', zIndex: 2 }} />
        <div style={{ position: 'absolute', bottom: 0, right: 0, width: 20, height: 20, borderBottom: '1px solid rgba(124,92,191,0.45)', borderRight: '1px solid rgba(124,92,191,0.45)', pointerEvents: 'none', zIndex: 2 }} />

        {/* ── SIDEBAR ── */}
        <div style={{
          width: '170px', flexShrink: 0,
          background: 'rgba(4,6,15,0.85)', borderRight: '1px solid rgba(0,180,220,0.1)',
          padding: '14px 8px', display: 'flex', flexDirection: 'column', gap: '2px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '7px', marginBottom: '16px', paddingLeft: '4px' }}>
            <div style={{ width: 20, height: 20, borderRadius: 3, background: 'linear-gradient(135deg,#00d2ff,#7c5cbf)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.62rem', fontWeight: 800, color: '#fff', fontFamily: 'var(--font-display)', flexShrink: 0 }}>M</div>
            <div>
              <div style={{ fontSize: '0.54rem', fontWeight: 700, color: 'var(--accent-teal)', letterSpacing: '0.14em', textTransform: 'uppercase', fontFamily: 'var(--font-display)' }}>Settings</div>
              <div style={{ fontSize: '0.5rem', color: 'var(--text-muted)' }}>Makima OS</div>
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
                  display: 'flex', alignItems: 'center', gap: '8px',
                  padding: '8px 9px', borderRadius: '3px', border: 'none',
                  background: active ? 'rgba(0,210,255,0.08)' : 'transparent',
                  color: active ? 'var(--accent-teal)' : 'var(--text-muted)',
                  fontWeight: active ? 600 : 400, fontSize: '0.74rem',
                  cursor: 'pointer', textAlign: 'left', transition: 'all 0.14s',
                  borderLeft: active ? '2px solid var(--accent-teal)' : '2px solid transparent',
                  fontFamily: 'inherit',
                }}
              >
                <Icon size={13} /><span>{tab.label}</span>
              </button>
            );
          })}

          <div style={{ marginTop: 'auto', borderTop: '1px solid rgba(0,180,220,0.09)', paddingTop: '9px' }}>
            <ActionBtn variant="danger" onClick={handleResetDefaults} style={{ width: '100%', justifyContent: 'flex-start', fontSize: '0.66rem', padding: '6px 8px' }}>
              <Trash2 size={11} /> Reset Defaults
            </ActionBtn>
          </div>
        </div>

        {/* ── CONTENT ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, position: 'relative' }}>
          {/* Header bar */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 14px', height: '38px', flexShrink: 0,
            borderBottom: '1px solid rgba(0,180,220,0.1)', background: 'rgba(0,0,0,0.22)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontFamily: 'var(--font-display)', fontSize: '0.56rem', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--accent-teal)' }}>
              <Zap size={9} /> {tabTitles[activeTab]}
            </div>
            <button
              onClick={onClose}
              style={{ background: 'transparent', border: '1px solid rgba(0,180,220,0.13)', color: 'var(--text-muted)', cursor: 'pointer', padding: '3px', borderRadius: '3px', display: 'flex', transition: 'all 0.15s' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = '#ff4444'; e.currentTarget.style.borderColor = 'rgba(255,68,68,0.32)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'rgba(0,180,220,0.13)'; }}
            >
              <X size={13} />
            </button>
          </div>

          {/* Body */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '14px', display: 'flex', flexDirection: 'column', gap: '12px' }}>

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
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <SLabel>Available Integrations</SLabel>
                <div style={{ fontSize: '0.67rem', color: 'var(--text-muted)', marginBottom: '2px' }}>
                  Click any card to configure credentials. Use toggle to enable/disable.
                </div>
                {(['messaging', 'developer', 'productivity', 'media'] as const).map((cat) => {
                  const items = REAL_APP_CONNECTORS.filter((c) => c.category === cat);
                  const catLabel: Record<string, string> = { messaging: 'Messaging', developer: 'Developer', productivity: 'Productivity', media: 'Media' };
                  return (
                    <div key={cat}>
                      <div style={{ fontSize: '0.54rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'rgba(0,180,220,0.5)', marginBottom: '5px' }}>{catLabel[cat]}</div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
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
                                padding: '8px 11px', borderRadius: '4px', cursor: 'pointer',
                                border: `1px solid ${isEnabled ? 'rgba(0,210,255,0.2)' : 'rgba(0,180,220,0.08)'}`,
                                background: isEnabled ? 'rgba(0,210,255,0.04)' : 'rgba(0,0,0,0.12)',
                                transition: 'all 0.14s',
                              }}
                              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'rgba(0,210,255,0.28)'; }}
                              onMouseLeave={(e) => { e.currentTarget.style.borderColor = isEnabled ? 'rgba(0,210,255,0.2)' : 'rgba(0,180,220,0.08)'; }}
                            >
                              <div style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
                                <div style={{
                                  width: 26, height: 26, borderRadius: 3, flexShrink: 0,
                                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  background: isEnabled ? 'rgba(0,210,255,0.08)' : 'rgba(255,255,255,0.04)',
                                  border: `1px solid ${isEnabled ? 'rgba(0,210,255,0.18)' : 'rgba(255,255,255,0.06)'}`,
                                  color: isEnabled ? 'var(--accent-teal)' : 'var(--text-muted)',
                                }}>
                                  <Icon size={13} />
                                </div>
                                <div>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                    <span style={{ fontSize: '0.74rem', fontWeight: 600, color: 'var(--text-primary)' }}>{conn.name}</span>
                                    {hasKey && <span style={{ fontSize: '0.52rem', padding: '1px 5px', borderRadius: '2px', background: 'rgba(0,230,118,0.09)', color: '#00e676', border: '1px solid rgba(0,230,118,0.18)', fontWeight: 700, letterSpacing: '0.06em' }}>KEYED</span>}
                                  </div>
                                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: '1px' }}>{conn.desc}</div>
                                </div>
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                                <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)' }}>Configure</span>
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
              <div style={{ display: 'flex', flexDirection: 'column', gap: '11px' }}>
                <SLabel>Agent Persona</SLabel>
                <div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-secondary)', marginBottom: '5px' }}>System Prompt Mode</div>
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
                      width: '100%', padding: '9px 11px', borderRadius: '4px', resize: 'vertical',
                      background: 'rgba(0,0,0,0.28)', border: '1px solid rgba(0,180,220,0.14)',
                      color: 'var(--text-primary)', fontSize: '0.8rem', fontFamily: 'inherit',
                      outline: 'none', lineHeight: 1.55,
                    }}
                    onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(0,210,255,0.35)'; }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(0,180,220,0.14)'; }}
                  />
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: '3px' }}>
                    Appended to every prompt sent to Makima's reasoning engine.
                  </div>
                </div>

                <SLabel>Privacy & Session State</SLabel>
                <SettingRow label="Privacy Mode — Ephemeral Sessions" desc="Chat turns are NOT saved to long-term memory or SQLite." icon={<Shield size={13} />} control={<ToggleSwitch checked={privacyMode} onChange={setPrivacyMode} />} />

                <SLabel>Autonomy — Proactive Actions</SLabel>
                <SettingRow
                  label="Let Makima act on its own"
                  desc="Off = never. Suggest = ideas only. Auto = safe actions run themselves; risky ones always ask first."
                  icon={<Zap size={13} />}
                  control={
                    <div style={{ display: 'flex', gap: '4px' }}>
                      {(['off', 'suggest', 'auto'] as const).map((m) => (
                        <button
                          key={m}
                          type="button"
                          onClick={() => { setAutonomyMode(m); localStorage.setItem('makima_autonomy_mode', m); wsClient.setAutonomyMode(m); }}
                          style={{
                            padding: '3px 9px', fontSize: '0.6rem', fontWeight: autonomyMode === m ? 700 : 500,
                            borderRadius: '4px', cursor: 'pointer', textTransform: 'capitalize',
                            border: `1px solid ${autonomyMode === m ? 'rgba(0,210,255,0.5)' : 'rgba(255,255,255,0.08)'}`,
                            background: autonomyMode === m ? 'rgba(0,210,255,0.15)' : 'rgba(255,255,255,0.03)',
                            color: autonomyMode === m ? 'var(--accent-teal)' : 'var(--text-muted)',
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
              <div style={{ display: 'flex', flexDirection: 'column', gap: '11px' }}>
                <SLabel>Voice Activation</SLabel>
                <SettingRow label={'Wake Word — "Hey Makima"'} desc="Continuously listens via local openwakeword engine." icon={<Zap size={13} />} control={<ToggleSwitch checked={wakeWordEnabled} onChange={setWakeWordEnabled} />} />
                <SettingRow label="Auto Read Aloud AI Responses" desc="Synthesize speech when assistant finishes streaming." icon={<Volume2 size={13} />} control={<ToggleSwitch checked={autoReadAloud} onChange={setAutoReadAloud} />} />

                <SLabel>Speech Settings</SLabel>
                <div style={{ padding: '11px 13px', border: '1px solid rgba(0,180,220,0.1)', borderRadius: '4px', background: 'rgba(0,0,0,0.14)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '7px' }}>
                    <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>TTS Playback Speed</span>
                    <span style={{ fontSize: '0.74rem', color: 'var(--accent-teal)', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{ttsSpeed.toFixed(1)}×</span>
                  </div>
                  <input type="range" min="0.5" max="2.0" step="0.1" value={ttsSpeed}
                    onChange={(e) => setTtsSpeed(parseFloat(e.target.value))}
                    style={{ width: '100%', cursor: 'pointer', accentColor: '#00d2ff' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '2px' }}>
                    <span style={{ fontSize: '0.54rem', color: 'var(--text-muted)' }}>0.5×</span>
                    <span style={{ fontSize: '0.54rem', color: 'var(--text-muted)' }}>2.0×</span>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                  {[
                    { label: 'Follow-up Window', unit: 's',  val: followupTimeoutSeconds, set: setFollowupTimeoutSeconds, min: 5,   max: 120, step: 1  },
                    { label: 'Silence End',       unit: 'ms', val: silenceTimeoutMs,       set: setSilenceTimeoutMs,       min: 400, max: 3000, step: 50 },
                    { label: 'Max Turn',          unit: 's',  val: maxUtteranceSeconds,     set: setMaxUtteranceSeconds,    min: 3,   max: 60,  step: 1  },
                  ].map((f) => (
                    <div key={f.label} style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <div style={{ fontSize: '0.62rem', color: 'var(--text-secondary)' }}>{f.label} ({f.unit})</div>
                      <SInput type="number" min={f.min} max={f.max} step={f.step} value={f.val}
                        onChange={(e) => f.set(Number(e.target.value))} />
                    </div>
                  ))}
                </div>

                <SLabel>Language & Testing</SLabel>
                <div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Voice Language</div>
                  <SSelect value={voiceLanguage} onChange={(e) => setVoiceLanguage(e.target.value as any)}>
                    <option value="auto">Auto (Hindi / English)</option>
                    <option value="hi">Hindi (हिन्दी)</option>
                    <option value="en">English (US / UK)</option>
                  </SSelect>
                </div>
                <ActionBtn variant={isTestingVoice ? 'danger' : 'primary'} onClick={handleTestVoice} style={{ alignSelf: 'flex-start' }}>
                  {isTestingVoice ? <VolumeX size={12} /> : <Play size={12} />}
                  {isTestingVoice ? 'Stop Test' : 'Test Voice Output'}
                </ActionBtn>
              </div>
            )}

            {/* DEVELOPER */}
            {activeTab === 'developer' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '11px' }}>
                <SLabel>WebSocket Connection</SLabel>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-secondary)' }}>Makima Brain WebSocket URL</div>
                  <div style={{ display: 'flex', gap: '7px' }}>
                    <SInput type="text" value={wsUrl} onChange={(e) => setWsUrl(e.target.value)} style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: '0.76rem' }} />
                    <ActionBtn variant="primary" onClick={handleTestPing} disabled={pingLoading} style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
                      <Activity size={11} className={pingLoading ? 'spin' : ''} />
                      {pingLoading ? 'Pinging...' : 'Ping'}
                    </ActionBtn>
                  </div>
                  {pingLatency !== null && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '0.7rem', color: '#00e676' }}>
                      <CheckCircle2 size={12} /> Pong received · <strong style={{ fontFamily: 'var(--font-mono)' }}>{pingLatency}ms</strong>
                    </div>
                  )}
                </div>

                <SLabel>Protocol Status</SLabel>
                <div style={{ padding: '10px 13px', borderRadius: '4px', background: 'rgba(4,6,15,0.8)', border: '1px solid rgba(0,180,220,0.1)', fontFamily: 'var(--font-mono)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '7px', marginBottom: '5px' }}>
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#00e676', display: 'inline-block', boxShadow: '0 0 5px rgba(0,230,118,0.6)' }} />
                    <span style={{ fontSize: '0.68rem', color: '#00e676' }}>Protocol v1.0 — Makima OS Core WebSocket</span>
                  </div>
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>Endpoints: /ws · /health · /settings · /media · /auth</div>
                </div>

                <SLabel>Local Ollama Model Manager</SLabel>
                <div style={{ padding: '11px 13px', borderRadius: '4px', border: '1px solid rgba(0,180,220,0.1)', background: 'rgba(0,0,0,0.13)' }}>
                  <div style={{ fontSize: '0.76rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '3px' }}>Pull Open-Weight Model</div>
                  <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginBottom: '9px' }}>
                    Download into Ollama: <code style={{ color: 'var(--accent-teal)' }}>llama3.2</code>, <code style={{ color: 'var(--accent-teal)' }}>qwen2.5-coder:7b</code>, <code style={{ color: 'var(--accent-teal)' }}>deepseek-r1:1.5b</code>
                  </div>
                  <div style={{ display: 'flex', gap: '7px' }}>
                    <SInput type="text" placeholder="e.g. llama3.2, deepseek-r1:1.5b" value={ollamaModelName}
                      onChange={(e) => setOllamaModelName(e.target.value)}
                      style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: '0.76rem' }}
                      onKeyDown={(e) => { if (e.key === 'Enter') handlePullOllamaModel(); }}
                    />
                    <ActionBtn variant="primary" onClick={handlePullOllamaModel} disabled={ollamaLoading || !ollamaModelName.trim()} style={{ flexShrink: 0 }}>
                      <RefreshCw size={11} className={ollamaLoading ? 'spin' : ''} /> Pull
                    </ActionBtn>
                  </div>
                  {ollamaStatus && (
                    <div style={{ marginTop: '7px', fontSize: '0.64rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>› {ollamaStatus}</div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '7px',
            padding: '9px 14px', flexShrink: 0,
            borderTop: '1px solid rgba(0,180,220,0.1)', background: 'rgba(0,0,0,0.2)',
          }}>
            <ActionBtn variant="ghost" onClick={onClose}>Cancel</ActionBtn>
            <ActionBtn variant="primary" onClick={handleSave}><Check size={12} /> Save Changes</ActionBtn>
          </div>

          {/* Connector sub-modal */}
          {selectedConnector && (
            <ConnectorConfigSubModal
              connector={selectedConnector}
              initialKey={connectorKeys[selectedConnector.id] || ''}
              onClose={() => setSelectedConnector(null)}
              onSaveKey={(val) => handleSaveConnectorKey(selectedConnector.id, val)}
              onClearKey={() => handleSaveConnectorKey(selectedConnector.id, '')}
            />
          )}
        </div>
      </div>
    </div>
  );
};

// ── Connector Config Sub-Modal ────────────────────────────────────────────────
const ConnectorConfigSubModal: React.FC<{
  connector: AppConnectorConfig;
  initialKey: string;
  onClose: () => void;
  onSaveKey: (val: string) => void;
  onClearKey: () => void;
}> = ({ connector, initialKey, onClose, onSaveKey, onClearKey }) => {
  const [val, setVal]           = useState(initialKey);
  const [showKey, setShowKey]   = useState(false);
  const Icon                    = connector.icon;

  return (
    <div style={{
      position: 'absolute', inset: 0, background: 'rgba(4,6,15,0.9)',
      backdropFilter: 'blur(12px)', display: 'flex', alignItems: 'center',
      justifyContent: 'center', zIndex: 110, padding: '24px',
    }}>
      <div style={{
        width: '100%', maxWidth: '420px',
        background: 'var(--bg-secondary)', border: '1px solid rgba(0,210,255,0.22)',
        borderRadius: '6px', padding: '18px', position: 'relative',
        boxShadow: '0 0 50px rgba(0,0,0,0.9)',
      }}>
        <div style={{ position: 'absolute', top: 0, left: 0, width: 16, height: 16, borderTop: '1px solid rgba(0,210,255,0.5)', borderLeft: '1px solid rgba(0,210,255,0.5)', pointerEvents: 'none' }} />
        <div style={{ position: 'absolute', bottom: 0, right: 0, width: 16, height: 16, borderBottom: '1px solid rgba(124,92,191,0.45)', borderRight: '1px solid rgba(124,92,191,0.45)', pointerEvents: 'none' }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '13px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: 26, height: 26, borderRadius: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,210,255,0.09)', border: '1px solid rgba(0,210,255,0.2)', color: 'var(--accent-teal)' }}>
              <Icon size={13} />
            </div>
            <div>
              <div style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-primary)' }}>Configure {connector.name}</div>
              <div style={{ fontSize: '0.58rem', color: 'var(--text-muted)', textTransform: 'capitalize' }}>{connector.category} · {connector.authType}</div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex' }}><X size={13} /></button>
        </div>

        <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '13px', lineHeight: 1.5 }}>
          {connector.desc}. Enter credentials to authorize Makima OS:
        </p>

        <div style={{ marginBottom: '14px' }}>
          <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginBottom: '5px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Credentials / API Key</div>
          <div style={{ position: 'relative' }}>
            <SInput
              type={showKey ? 'text' : 'password'}
              placeholder={connector.placeholder}
              value={val}
              onChange={(e) => setVal(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') onSaveKey(val); }}
              style={{ paddingRight: '34px', fontFamily: 'var(--font-mono)', fontSize: '0.76rem' }}
            />
            <button type="button" onClick={() => setShowKey(!showKey)}
              style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex' }}>
              {showKey ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {initialKey ? (
            <ActionBtn variant="danger" onClick={onClearKey} style={{ fontSize: '0.66rem', padding: '6px 11px' }}>
              <Trash2 size={11} /> Remove Key
            </ActionBtn>
          ) : <div />}
          <div style={{ display: 'flex', gap: '7px' }}>
            <ActionBtn variant="ghost" onClick={onClose}>Cancel</ActionBtn>
            <ActionBtn variant="primary" onClick={() => onSaveKey(val)}><Check size={11} /> Save</ActionBtn>
          </div>
        </div>
      </div>
    </div>
  );
};
