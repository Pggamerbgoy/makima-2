import { useState, useEffect } from "react";
import {
  Mic,
  AudioLines,
  MicOff,
  Shield,
  FolderDown,
  Cpu,
  Keyboard,
  Volume2,
  Loader2,
} from "lucide-react";
import "./SettingsPage.css";

type VoiceMode = "ptt" | "wakeword" | "off";
type SettingsSection = "voice" | "llm" | "privacy" | "export";
type SaveState = "idle" | "saving" | "saved" | "error";

interface LLMBackend {
  id: string;
  name: string;
  model: string;
  enabled: boolean;
  keyEnv: string;
}

const LLM_BACKENDS: LLMBackend[] = [
  { id: "groq", name: "Groq", model: "llama-3.3-70b-versatile", enabled: true, keyEnv: "MAKIMA_GROQ_KEY" },
  { id: "gemini", name: "Gemini", model: "gemini-2.5-pro", enabled: true, keyEnv: "MAKIMA_GEMINI_KEY" },
  { id: "openrouter", name: "OpenRouter (free models)", model: "meta-llama/llama-3.3-70b-instruct:free", enabled: true, keyEnv: "MAKIMA_OPENROUTER_KEY" },
  { id: "gpt4o", name: "GPT-4o", model: "gpt-4o", enabled: true, keyEnv: "MAKIMA_OPENAI_KEY" },
  { id: "cerebras", name: "Cerebras", model: "llama3.1-70b", enabled: false, keyEnv: "MAKIMA_CEREBRAS_KEY" },
  { id: "ollama", name: "Ollama (Local)", model: "llama3.2", enabled: true, keyEnv: "" },
];

const SECTIONS: { id: SettingsSection; label: string; icon: typeof Mic }[] = [
  { id: "voice", label: "Voice Settings", icon: Volume2 },
  { id: "llm", label: "LLM Backends", icon: Cpu },
  { id: "privacy", label: "Privacy Mode", icon: Shield },
  { id: "export", label: "Export / Backup", icon: FolderDown },
];

const API_BASE = "http://127.0.0.1:8080";

function SaveButton({ state, onClick }: { state: SaveState; onClick: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16 }}>
      <button className="btn btn-primary" onClick={onClick} disabled={state === "saving"}>
        {state === "saving" ? <Loader2 size={14} className="spin" /> : state === "saved" ? "Saved \u2713" : "Save"}
      </button>
      {state === "error" && <span className="text-label text-error">Save failed -- is the brain running?</span>}
      {state === "saved" && <span className="text-label text-muted">Some settings need a restart to take effect.</span>}
    </div>
  );
}

export default function SettingsPage() {
  const [activeSection, setActiveSection] = useState<SettingsSection>("voice");
  const [voiceMode, setVoiceMode] = useState<VoiceMode>("wakeword");
  const [wakePhrase, setWakePhrase] = useState("hey makima");
  const [pttKey, setPttKey] = useState("ctrl+shift+space");
  const [ttsEngine, setTtsEngine] = useState("edge-tts");
  const [ttsVoice, setTtsVoice] = useState("en-US-AriaNeural");
  const [privacyMode, setPrivacyMode] = useState(false);
  const [backupPath, setBackupPath] = useState("~/Documents/makima_backups/");
  const [backends, setBackends] = useState(LLM_BACKENDS);
  const [saveStates, setSaveStates] = useState<Record<SettingsSection, SaveState>>({
    voice: "idle", llm: "idle", privacy: "idle", export: "idle",
  });
  const [exportMessage, setExportMessage] = useState("");

  // Load whatever was actually persisted last time -- previously this page
  // never read anything back from the backend at all.
  useEffect(() => {
    fetch(`${API_BASE}/settings`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => {
        if (data.voice?.wake_word_enabled !== undefined) {
          setVoiceMode(data.voice.wake_word_enabled ? "wakeword" : "ptt");
        }
        if (data.voice?.ptt_key) setPttKey(data.voice.ptt_key);
        const general = data.general || {};
        if (general.wake_phrase) setWakePhrase(general.wake_phrase);
        if (general.tts_engine) setTtsEngine(general.tts_engine);
        if (general.tts_voice) setTtsVoice(general.tts_voice);
        if (typeof general.privacy_mode === "boolean") setPrivacyMode(general.privacy_mode);
        if (general.backup_path) setBackupPath(general.backup_path);
        if (general.llm_backend_toggles) {
          setBackends((prev) =>
            prev.map((b) => ({
              ...b,
              enabled: general.llm_backend_toggles[b.id] ?? b.enabled,
            }))
          );
        }
      })
      .catch(() => {});
  }, []);

  const toggleBackend = (id: string) => {
    setBackends(backends.map((b) => (b.id === id ? { ...b, enabled: !b.enabled } : b)));
  };

  const saveSection = async (section: SettingsSection, payload: Record<string, unknown>) => {
    setSaveStates((s) => ({ ...s, [section]: "saving" }));
    try {
      const resp = await fetch(`${API_BASE}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setSaveStates((s) => ({ ...s, [section]: resp.ok ? "saved" : "error" }));
      setTimeout(() => setSaveStates((s) => ({ ...s, [section]: "idle" })), 2500);
    } catch {
      setSaveStates((s) => ({ ...s, [section]: "error" }));
    }
  };

  const handleExportNow = () => {
    // No backend export endpoint exists yet -- being honest about that in
    // the UI instead of pretending this button does something it doesn't.
    setExportMessage("Export automation isn't built yet -- the backup path is saved, but nothing runs on this click yet.");
    setTimeout(() => setExportMessage(""), 5000);
  };

  return (
    <div className="settings-layout">
      <aside className="settings-sidebar">
        <div className="settings-sidebar-header text-label text-muted">Settings</div>
        {SECTIONS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={`settings-nav-item${activeSection === id ? " active" : ""}`}
            onClick={() => setActiveSection(id)}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </aside>

      <div className="settings-content">
        {/* --- Voice Settings --- */}
        {activeSection === "voice" && (
          <div className="settings-section animate-in">
            <h2>Voice Settings</h2>
            <p className="text-muted">Configure Makima's voice input and output</p>

            <div className="settings-group">
              <label className="text-label">Voice Mode</label>
              <div className="settings-segmented">
                {([
                  { v: "ptt" as VoiceMode, icon: Mic, label: "Push-to-Talk" },
                  { v: "wakeword" as VoiceMode, icon: AudioLines, label: "Wake Word" },
                  { v: "off" as VoiceMode, icon: MicOff, label: "Off" },
                ]).map(({ v, icon: Icon, label }) => (
                  <button
                    key={v}
                    className={`settings-seg-btn${voiceMode === v ? " active" : ""}`}
                    onClick={() => setVoiceMode(v)}
                  >
                    <Icon size={16} />
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div className="settings-group">
              <label className="text-label">Wake Word Phrase</label>
              <input
                className="input"
                value={wakePhrase}
                onChange={(e) => setWakePhrase(e.target.value)}
                disabled={voiceMode !== "wakeword"}
              />
            </div>

            <div className="settings-group">
              <label className="text-label">
                <Keyboard size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
                PTT Key Binding
              </label>
              <input
                className="input"
                value={pttKey}
                onChange={(e) => setPttKey(e.target.value)}
                disabled={voiceMode !== "ptt"}
              />
            </div>

            <div className="settings-row">
              <div className="settings-group" style={{ flex: 1 }}>
                <label className="text-label">TTS Engine</label>
                <select className="input" value={ttsEngine} onChange={(e) => setTtsEngine(e.target.value)}>
                  <option value="edge-tts">edge-tts</option>
                  <option value="pyttsx3">pyttsx3</option>
                </select>
              </div>
              <div className="settings-group" style={{ flex: 1 }}>
                <label className="text-label">TTS Voice</label>
                <select className="input" value={ttsVoice} onChange={(e) => setTtsVoice(e.target.value)}>
                  <option value="en-US-AriaNeural">en-US-AriaNeural</option>
                  <option value="en-US-GuyNeural">en-US-GuyNeural</option>
                  <option value="hi-IN-SwaraNeural">hi-IN-SwaraNeural</option>
                </select>
              </div>
            </div>

            <SaveButton
              state={saveStates.voice}
              onClick={() =>
                saveSection("voice", {
                  wake_word_enabled: voiceMode === "wakeword",
                  ptt_key: pttKey,
                  wake_phrase: wakePhrase,
                  tts_engine: ttsEngine,
                  tts_voice: ttsVoice,
                })
              }
            />
          </div>
        )}

        {/* --- LLM Backends --- */}
        {activeSection === "llm" && (
          <div className="settings-section animate-in">
            <h2>LLM Backends</h2>
            <p className="text-muted">
              Enable/disable toggles here are saved, but take effect on the next brain restart --
              the running backend doesn't hot-reload this list yet.
            </p>

            <div className="settings-backends">
              {backends.map((b) => (
                <div key={b.id} className="settings-backend-card card">
                  <div className="settings-backend-header">
                    <div>
                      <span className="settings-backend-name">{b.name}</span>
                      <span className="text-label text-muted" style={{ marginLeft: 8 }}>
                        {b.model}
                      </span>
                    </div>
                    <button className={`toggle${b.enabled ? " active" : ""}`} onClick={() => toggleBackend(b.id)} />
                  </div>
                  {b.keyEnv && (
                    <p className="text-label text-muted" style={{ marginTop: 12 }}>
                      Key comes from <code>{b.keyEnv}</code> in <code>.env</code> -- not editable here for security;
                      edit that file directly.
                    </p>
                  )}
                </div>
              ))}
            </div>

            <SaveButton
              state={saveStates.llm}
              onClick={() =>
                saveSection("llm", {
                  llm_backend_toggles: Object.fromEntries(backends.map((b) => [b.id, b.enabled])),
                })
              }
            />
          </div>
        )}

        {/* --- Privacy Mode --- */}
        {activeSection === "privacy" && (
          <div className="settings-section animate-in">
            <h2>Privacy Mode</h2>
            <p className="text-muted">Control data sharing and cloud access</p>

            <div className="settings-privacy-card card">
              <div className="settings-privacy-toggle">
                <div>
                  <h3 style={{ fontSize: 16, fontWeight: 500 }}>
                    <Shield size={18} style={{ verticalAlign: "middle", marginRight: 8 }} />
                    Privacy Mode
                  </h3>
                  <p className="text-muted" style={{ marginTop: 4, fontSize: 13 }}>
                    When enabled: no cloud LLMs, no screen capture, no clipboard events, Ollama-only
                  </p>
                </div>
                <button
                  className={`toggle${privacyMode ? " active" : ""}`}
                  onClick={() => setPrivacyMode(!privacyMode)}
                  style={{ transform: "scale(1.2)" }}
                />
              </div>
              {privacyMode && (
                <div className="badge badge-warning" style={{ marginTop: 16 }}>
                  \u26a0\ufe0f Privacy mode active \u2014 cloud LLMs disabled, using Ollama only (takes effect after restart)
                </div>
              )}
            </div>

            <SaveButton state={saveStates.privacy} onClick={() => saveSection("privacy", { privacy_mode: privacyMode })} />
          </div>
        )}

        {/* --- Export / Backup --- */}
        {activeSection === "export" && (
          <div className="settings-section animate-in">
            <h2>Export / Backup</h2>
            <p className="text-muted">Back up your conversations and settings</p>

            <div className="settings-group">
              <label className="text-label">Backup Path</label>
              <input className="input" value={backupPath} onChange={(e) => setBackupPath(e.target.value)} />
            </div>

            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button className="btn btn-primary" onClick={() => saveSection("export", { backup_path: backupPath })} disabled={saveStates.export === "saving"}>
                {saveStates.export === "saving" ? <Loader2 size={14} className="spin" /> : saveStates.export === "saved" ? "Saved \u2713" : "Save Path"}
              </button>
              <button className="btn btn-secondary" onClick={handleExportNow}>
                <FolderDown size={16} />
                Export Now
              </button>
            </div>
            {exportMessage && <p className="text-label text-muted" style={{ marginTop: 8 }}>{exportMessage}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
