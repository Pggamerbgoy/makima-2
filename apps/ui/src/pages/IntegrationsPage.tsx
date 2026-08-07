import { useState, useEffect } from "react";
import {
  MessageCircle,
  Mail,
  Globe,
  Bell,
  Cpu,
  Calendar,
  CheckCircle2,
  XCircle,
  Settings2,
  ChevronUp,
  Loader2,
  GitBranch,
  Link2,
  Link2Off,
} from "lucide-react";
import "./IntegrationsPage.css";

interface FieldDef {
  key: string;
  label: string;
  placeholder: string;
  type?: string;
}

interface Integration {
  id: string;
  name: string;
  icon: typeof MessageCircle;
  platform: string;
  connected: boolean;
  oauthProvider?: string; // if set, this card uses OAuth instead of manual fields
  fields: FieldDef[];
}

// ─── OAuth-based connectors (Wave 1) ────────────────────────────────────────
const OAUTH_INTEGRATIONS: Integration[] = [
  {
    id: "github",
    name: "GitHub",
    icon: GitBranch,
    platform: "OAuth 2.0",
    connected: false,
    oauthProvider: "github",
    fields: [],
  },
  {
    id: "google",
    name: "Google Workspace",
    icon: Calendar,
    platform: "OAuth 2.0 + PKCE",
    connected: false,
    oauthProvider: "google",
    fields: [],
  },
];

// ─── Manual / API-key integrations (existing) ───────────────────────────────
const MANUAL_INTEGRATIONS: Integration[] = [
  {
    id: "telegram", name: "Telegram", icon: MessageCircle, platform: "Bot API",
    connected: false,
    fields: [
      { key: "bot_token", label: "Bot Token", placeholder: "123456:ABC-DEF..." },
      { key: "chat_id", label: "Chat ID", placeholder: "-1001234567890" },
    ],
  },
  {
    id: "discord", name: "Discord", icon: MessageCircle, platform: "discord.py",
    connected: false,
    fields: [
      { key: "bot_token", label: "Bot Token", placeholder: "MTA5..." },
      { key: "channel_id", label: "Channel ID", placeholder: "1234567890" },
    ],
  },
  {
    id: "whatsapp", name: "WhatsApp", icon: MessageCircle, platform: "Web Bridge",
    connected: false,
    fields: [
      { key: "phone_number", label: "Phone Number", placeholder: "+91..." },
    ],
  },
  {
    id: "email", name: "Email", icon: Mail, platform: "SMTP/IMAP",
    connected: false,
    fields: [
      { key: "smtp_host", label: "SMTP Host", placeholder: "smtp.gmail.com" },
      { key: "email", label: "Email", placeholder: "user@gmail.com" },
      { key: "app_password", label: "App Password", placeholder: "••••••••", type: "password" },
    ],
  },
  {
    id: "browser", name: "Browser (CDP)", icon: Globe, platform: "Chrome DevTools",
    connected: false,
    fields: [{ key: "cdp_port", label: "CDP Port", placeholder: "9222" }],
  },
  {
    id: "ollama", name: "Ollama", icon: Cpu, platform: "Local Models",
    connected: false,
    fields: [
      { key: "host_url", label: "Host URL", placeholder: "http://localhost:11434" },
      { key: "default_model", label: "Default Model", placeholder: "llama3.2" },
    ],
  },
  {
    id: "notifications", name: "Windows Notifications", icon: Bell, platform: "gRPC / plyer",
    connected: false,
    fields: [],
  },
];

const ALL_INTEGRATIONS = [...OAUTH_INTEGRATIONS, ...MANUAL_INTEGRATIONS];
const API_BASE = "http://127.0.0.1:8080";

type FieldValues = Record<string, string>;
type FormState = Record<string, FieldValues>;
type SaveState = "idle" | "saving" | "saved" | "error";
type TestState = "idle" | "testing" | "pass" | "fail";

export default function IntegrationsPage() {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [statuses, setStatuses] = useState<Record<string, boolean>>({});
  // OAuth provider statuses (from /auth/status)
  const [oauthStatuses, setOauthStatuses] = useState<Record<string, boolean>>({});
  const [formState, setFormState] = useState<FormState>({});
  const [saveState, setSaveState] = useState<Record<string, SaveState>>({});
  const [testState, setTestState] = useState<Record<string, TestState>>({});
  const [testMessage, setTestMessage] = useState<Record<string, string>>({});
  const [oauthConnecting, setOauthConnecting] = useState<Record<string, boolean>>({});

  // Fetch legacy integrations status
  useEffect(() => {
    fetch(`${API_BASE}/integrations`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => {
        const items: { id: string; status: string; fields?: FieldValues }[] = data.integrations || [];
        setStatuses(Object.fromEntries(items.map((item) => [item.id, item.status === "ok" || item.status === "online"])));
        const initialForm: FormState = {};
        for (const item of items) {
          if (item.fields) initialForm[item.id] = item.fields;
        }
        setFormState(initialForm);
      })
      .catch(() => setStatuses({}));
  }, []);

  // Fetch OAuth status
  const refreshOAuthStatus = () => {
    fetch(`${API_BASE}/auth/status`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setOauthStatuses(data.providers || {}))
      .catch(() => {});
  };

  useEffect(() => {
    refreshOAuthStatus();
    // Poll every 3s so the UI updates after the browser tab closes
    const interval = setInterval(refreshOAuthStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  const setField = (integrationId: string, key: string, value: string) => {
    setFormState((prev) => ({
      ...prev,
      [integrationId]: { ...(prev[integrationId] || {}), [key]: value },
    }));
  };

  const handleSave = async (integrationId: string) => {
    setSaveState((s) => ({ ...s, [integrationId]: "saving" }));
    try {
      const resp = await fetch(`${API_BASE}/integrations/${integrationId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fields: formState[integrationId] || {} }),
      });
      const data = await resp.json();
      setSaveState((s) => ({ ...s, [integrationId]: data.ok ? "saved" : "error" }));
      setTimeout(() => setSaveState((s) => ({ ...s, [integrationId]: "idle" })), 2500);
    } catch {
      setSaveState((s) => ({ ...s, [integrationId]: "error" }));
    }
  };

  const handleTest = async (integrationId: string) => {
    setTestState((s) => ({ ...s, [integrationId]: "testing" }));
    try {
      const resp = await fetch(`${API_BASE}/integrations/${integrationId}/test`, { method: "POST" });
      const data = await resp.json();
      setTestState((s) => ({ ...s, [integrationId]: data.ok ? "pass" : "fail" }));
      setTestMessage((m) => ({ ...m, [integrationId]: data.message || "" }));
    } catch {
      setTestState((s) => ({ ...s, [integrationId]: "fail" }));
      setTestMessage((m) => ({ ...m, [integrationId]: "Request failed." }));
    }
  };

  const handleOAuthConnect = (provider: string) => {
    setOauthConnecting((s) => ({ ...s, [provider]: true }));
    // Open the login URL in the user's default browser
    window.open(`${API_BASE}/auth/login/${provider}`, "_blank");
    // Reset the connecting state after a few seconds
    setTimeout(() => setOauthConnecting((s) => ({ ...s, [provider]: false })), 5000);
  };

  const handleOAuthDisconnect = async (provider: string) => {
    await fetch(`${API_BASE}/auth/disconnect/${provider}`, { method: "DELETE" });
    refreshOAuthStatus();
  };

  const isConnected = (intg: Integration): boolean => {
    if (intg.oauthProvider) return oauthStatuses[intg.oauthProvider] ?? false;
    return statuses[intg.id] ?? intg.connected;
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Integrations</h1>
          <p className="text-muted">Connect your apps and services to Makima</p>
        </div>
        <span className="badge badge-primary">
          {ALL_INTEGRATIONS.filter((i) => isConnected(i)).length} Connected
        </span>
      </div>

      <div className="page-body">

        {/* ── OAuth Wave 1 Section ── */}
        <div className="int-section-title">
          <span>🔐 OAuth Connected Apps</span>
          <span className="text-label text-muted">One-click secure login · No API keys needed</span>
        </div>
        <div className="int-grid int-grid-oauth">
          {OAUTH_INTEGRATIONS.map((intg) => {
            const Icon = intg.icon;
            const connected = isConnected(intg);
            const connecting = oauthConnecting[intg.oauthProvider!] ?? false;

            return (
              <div key={intg.id} className={`int-card int-card-oauth card ${connected ? "card-connected" : ""}`}>
                <div className="int-card-header">
                  <div className="int-card-icon">
                    <Icon size={22} />
                  </div>
                  <div className="int-card-info">
                    <span className="int-card-name">{intg.name}</span>
                    <span className="text-label text-muted">{intg.platform}</span>
                  </div>
                  <span className={`badge ${connected ? "badge-success" : "badge-neutral"}`}>
                    {connected ? <><CheckCircle2 size={12} /> Connected</> : <><XCircle size={12} /> Not Connected</>}
                  </span>
                </div>

                <div className="int-card-oauth-actions">
                  {connected ? (
                    <button
                      className="btn btn-secondary btn-disconnect"
                      onClick={() => handleOAuthDisconnect(intg.oauthProvider!)}
                    >
                      <Link2Off size={14} />
                      Disconnect
                    </button>
                  ) : (
                    <button
                      className="btn btn-primary btn-connect"
                      onClick={() => handleOAuthConnect(intg.oauthProvider!)}
                      disabled={connecting}
                    >
                      {connecting ? <Loader2 size={14} className="spin" /> : <Link2 size={14} />}
                      {connecting ? "Opening browser..." : `Connect ${intg.name}`}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* ── Manual / API-key integrations ── */}
        <div className="int-section-title" style={{ marginTop: 32 }}>
          <span>🔑 API Key Integrations</span>
          <span className="text-label text-muted">Enter credentials manually</span>
        </div>
        <div className="int-grid">
          {MANUAL_INTEGRATIONS.map((intg) => {
            const Icon = intg.icon;
            const expanded = expandedId === intg.id;
            const saving = saveState[intg.id] === "saving";
            const testing = testState[intg.id] === "testing";

            return (
              <div key={intg.id} className={`int-card card ${expanded ? "card-active" : ""}`}>
                <div className="int-card-header">
                  <div className="int-card-icon">
                    <Icon size={20} />
                  </div>
                  <div className="int-card-info">
                    <span className="int-card-name">{intg.name}</span>
                    <span className="text-label text-muted">{intg.platform}</span>
                  </div>
                  <div className="int-card-actions">
                    <span className={`badge ${isConnected(intg) ? "badge-success" : "badge-neutral"}`}>
                      {isConnected(intg) ? (
                        <><CheckCircle2 size={12} /> Connected</>
                      ) : (
                        <><XCircle size={12} /> Disconnected</>
                      )}
                    </span>
                    {intg.fields.length > 0 && (
                      <button
                        className="btn-icon"
                        onClick={() => setExpandedId(expanded ? null : intg.id)}
                        title="Configure"
                      >
                        {expanded ? <ChevronUp size={16} /> : <Settings2 size={16} />}
                      </button>
                    )}
                  </div>
                </div>

                {expanded && intg.fields.length > 0 && (
                  <div className="int-card-expanded animate-in">
                    <div className="int-fields">
                      {intg.fields.map((field) => (
                        <div key={field.key} className="int-field">
                          <label className="text-label">{field.label}</label>
                          <input
                            className="input"
                            type={field.type || "text"}
                            placeholder={field.placeholder}
                            value={formState[intg.id]?.[field.key] ?? ""}
                            onChange={(e) => setField(intg.id, field.key, e.target.value)}
                          />
                        </div>
                      ))}
                    </div>

                    {testMessage[intg.id] && (
                      <p className={`text-label ${testState[intg.id] === "pass" ? "text-success" : "text-error"}`}>
                        {testMessage[intg.id]}
                      </p>
                    )}

                    <div className="int-card-buttons">
                      <button className="btn btn-secondary" onClick={() => handleTest(intg.id)} disabled={testing}>
                        {testing ? <Loader2 size={14} className="spin" /> : "Test Connection"}
                      </button>
                      <button className="btn btn-primary" onClick={() => handleSave(intg.id)} disabled={saving}>
                        {saving ? <Loader2 size={14} className="spin" /> : saveState[intg.id] === "saved" ? "Saved ✓" : "Save"}
                      </button>
                    </div>
                    {saveState[intg.id] === "saved" && (
                      <p className="text-label text-muted">Saved. Restart Makima's brain for it to take effect.</p>
                    )}
                    {saveState[intg.id] === "error" && (
                      <p className="text-label text-error">Save failed -- check the brain is running.</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
