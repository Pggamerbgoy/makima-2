import { useState } from "react";
import {
  MessageSquare,
  Gauge,
  Plug,
  Settings,
  AppWindow,
} from "lucide-react";
import ChatPage from "./pages/ChatPage";
import ControlCenterPage from "./pages/ControlCenterPage";
import IntegrationsPage from "./pages/IntegrationsPage";
import SettingsPage from "./pages/SettingsPage";
import OverlayPage from "./pages/OverlayPage";
import { toggleTauriOverlay } from "./services/tauriEngine";
import "./styles/design-system.css";
import "./styles/layout.css";

type PageId = "chat" | "control" | "integrations" | "settings";

const NAV_ITEMS: { id: PageId; icon: typeof MessageSquare; label: string }[] = [
  { id: "chat", icon: MessageSquare, label: "Chat" },
  { id: "control", icon: Gauge, label: "Control Center" },
  { id: "integrations", icon: Plug, label: "Integrations" },
  { id: "settings", icon: Settings, label: "Settings" },
];

// The overlay window loads the same index.html/bundle as the main window
// (see src-tauri/tauri.conf.json: overlay window's url is
// "index.html?window=overlay"), so a plain synchronous query-param check
// here decides which UI to render before the first paint — avoids a flash
// of the full app shell inside the small overlay window while an async
// Tauri API call would still be resolving.
const isOverlayWindow = (() => {
  try {
    if (typeof window === "undefined") return false;
    const search = window.location.search || "";
    const href = window.location.href || "";
    const hash = window.location.hash || "";
    if (search.includes("window=overlay") || href.includes("window=overlay") || hash.includes("window=overlay")) {
      return true;
    }
    if (window.name === "MakimaOverlay") return true;
    return false;
  } catch {
    return false;
  }
})();

function MainApp() {
  const [activePage, setActivePage] = useState<PageId>("chat");

  const renderPage = () => {
    switch (activePage) {
      case "chat":         return <ChatPage />;
      case "control":      return <ControlCenterPage />;
      case "integrations": return <IntegrationsPage />;
      case "settings":     return <SettingsPage />;
    }
  };

  return (
    <div className="app-shell">
      {/* --- Nav Rail --- */}
      <nav className="nav-rail">
        <div className="nav-rail-logo">M</div>

        {NAV_ITEMS.map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            className={`nav-rail-item${activePage === id ? " active" : ""}`}
            onClick={() => setActivePage(id)}
            title={label}
          >
            <Icon size={20} />
          </button>
        ))}

        <div className="nav-rail-spacer" />

        <button
          className="nav-rail-item"
          onClick={() => toggleTauriOverlay()}
          title="Toggle Overlay (Ctrl+Shift+O)"
          style={{ marginBottom: 12 }}
        >
          <AppWindow size={20} />
        </button>

        {/* system health dot */}
        <div style={{ marginBottom: 8 }} title="System healthy">
          <span className="dot dot-green" />
        </div>
      </nav>

      {/* --- Main Content --- */}
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  );
}

export default function App() {
  return isOverlayWindow ? <OverlayPage /> : <MainApp />;
}
