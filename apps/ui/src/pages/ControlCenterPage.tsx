import { useState, useEffect } from "react";
import {
  Play,
  Square,
  Activity,
  Server,
  Cpu,
  HardDrive,
  Download,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { useMakimaWebSocket } from "../hooks/useMakimaWebSocket";
import { startTauriEngine, stopTauriEngine } from "../services/tauriEngine";
import "./ControlCenterPage.css";

interface NativeService {
  name: string;
  status: "online" | "degraded" | "offline";
  latency?: string;
}

interface OllamaModel {
  name: string;
  size: string;
  quantization: string;
}

const SERVICES: NativeService[] = [];
const MODELS: OllamaModel[] = [];
const statusDot = (s: string) =>
  s === "online" ? "dot-green" : s === "degraded" ? "dot-yellow" : "dot-red";

export default function ControlCenterPage() {
  const { connected, systemLogs, pullOllamaModel, deleteOllamaModel } = useMakimaWebSocket();
  const [engineRunning, setEngineRunning] = useState(connected);
  const [pullModelName, setPullModelName] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [services, setServices] = useState<NativeService[]>(SERVICES);
  const [models, setModels] = useState<OllamaModel[]>(MODELS);

  useEffect(() => {
    setEngineRunning(connected);
  }, [connected]);

  useEffect(() => {
    fetch("http://127.0.0.1:8080/status").then((r) => r.ok ? r.json() : Promise.reject(r.status)).then((data) => {
      const source = data.services || data.native_services || {};
      setServices(Object.entries(source).map(([name, value]) => ({ name, status: (value as {status?: string}).status === "ok" ? "online" : (value as {status?: string}).status === "degraded" ? "degraded" : "offline" })));
      setModels((data.local_models || []).map((model: {name: string; size_gb?: number}) => ({ name: model.name, size: `${model.size_gb ?? 0} GB`, quantization: "backend reported" })));
    }).catch(() => { setServices([]); setModels([]); });
  }, [connected]);

  const handleStartEngine = async () => {
    const res = await startTauriEngine();
    setStatusMessage(res);
    setEngineRunning(true);
  };

  const handleStopEngine = async () => {
    const res = await stopTauriEngine();
    setStatusMessage(res);
    setEngineRunning(false);
  };

  const handleRestartEngine = async () => {
    await stopTauriEngine();
    setTimeout(async () => {
      const res = await startTauriEngine();
      setStatusMessage(`Restarted: ${res}`);
      setEngineRunning(true);
    }, 1000);
  };

  const handlePullModel = () => {
    if (!pullModelName.trim()) return;
    pullOllamaModel(pullModelName);
    setPullModelName("");
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Control Center</h1>
          <p className="text-muted">Manage the Makima engine and services</p>
        </div>
        <div className="cc-engine-status">
          <span className={`dot ${engineRunning ? "dot-green" : "dot-red"}`} />
          <span className="badge badge-success">
            {engineRunning ? "Engine Running" : "Engine Disconnected"}
          </span>
        </div>
      </div>

      <div className="page-body">
        {/* Status Alert if any */}
        {statusMessage && (
          <div className="badge badge-primary" style={{ marginBottom: 12 }}>
            Ã¢â€žÂ¹Ã¯Â¸Â {statusMessage}
          </div>
        )}

        {/* Engine Controls */}
        <div className="cc-controls">
          <button
            className="btn btn-success cc-engine-btn"
            onClick={handleStartEngine}
            disabled={engineRunning}
          >
            <Play size={18} />
            Start Engine
          </button>
          <button
            className="btn btn-danger cc-engine-btn"
            onClick={handleStopEngine}
            disabled={!engineRunning}
          >
            <Square size={16} />
            Stop Engine
          </button>
          <button className="btn btn-secondary" onClick={handleRestartEngine} title="Restart Engine">
            <RefreshCw size={16} />
            Restart
          </button>
        </div>

        {/* Main Grid */}
        <div className="cc-grid">
          {/* Terminal */}
          <div className="cc-terminal card">
            <div className="cc-terminal-header">
              <Activity size={14} />
              <span className="text-label">Live System Logs</span>
            </div>
            <div className="cc-terminal-body">
              {systemLogs.length === 0 ? (
                <div className="cc-log-line text-code" style={{ opacity: 0.5 }}>
                  Waiting for backend events...
                </div>
              ) : (
                systemLogs.map((line, i) => (
                  <div key={i} className="cc-log-line text-code">
                    {line}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Services */}
          <div className="cc-services">
            <div className="cc-section-header">
              <Server size={14} />
              <span className="text-label">Native Services</span>
            </div>
            <div className="cc-services-list">
              {services.map((svc) => (
                <div key={svc.name} className="cc-service-card card">
                  <div className="cc-service-info">
                    <span className={`dot ${statusDot(svc.status)}`} />
                    <span className="cc-service-name">{svc.name}</span>
                  </div>
                  {svc.latency && (
                    <span className="text-label text-muted">{svc.latency}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Ollama Models */}
        <div className="cc-ollama">
          <div className="cc-section-header">
            <Cpu size={14} />
            <span className="text-label">Ollama Local Models</span>
          </div>

          <div className="cc-model-grid">
            {models.map((model) => (
              <div key={model.name} className="cc-model-card card">
                <div className="cc-model-info">
                  <HardDrive size={16} className="text-primary" />
                  <div>
                    <div className="cc-model-name">{model.name}</div>
                    <div className="text-label text-muted">
                      {model.size} Ã‚Â· {model.quantization}
                    </div>
                  </div>
                </div>
                <button
                  className="btn-icon"
                  title="Delete model"
                  onClick={() => deleteOllamaModel(model.name)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>

          <div className="cc-pull-model">
            <input
              className="input"
              placeholder="Model name (e.g., llama3.2)"
              value={pullModelName}
              onChange={(e) => setPullModelName(e.target.value)}
            />
            <button
              className="btn btn-primary"
              disabled={!pullModelName.trim()}
              onClick={handlePullModel}
            >
              <Download size={16} />
              Pull Model
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
