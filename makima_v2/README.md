# 👑 Makima v2 — Autonomous Multi-Platform AI Desktop Assistant
> Inspired by **Nous Research Hermes Agent** architecture with **Dynamic Zero-Cost Pareto Routing**.

---

## ⚡ Key Highlights

1. **Clean While-Loop Engine**: Zero monolithic 2,900-line SDK bridge wrappers. Transparent, debuggable tool execution loop.
2. **Dynamic Auto-Discovery Toolsets**: Tools in `tools/` self-register using the `@tool` decorator. No hardcoded tool arrays.
3. **Multi-Platform Gateway Daemon**: A single long-running background daemon (`makima gateway run`) connects simultaneously to Telegram, Discord, WhatsApp, and Email.
4. **Dual-Tier Memory**: Human-readable `MEMORY.md` and `USER.md` prompt injection + SQLite-vec on-device semantic search.
5. **Markdown Skills Engine**: Self-improving skills in `~/.makima/skills/*.md` with YAML frontmatter. Makima synthesizes and refines skills autonomously.
6. **Free-Tier First Policy**: 100% functional with Google GenAI SDK free tier, Groq free tier, OpenRouter free models, and offline Ollama. Zero mandatory paid API keys.
7. **Windows-Native Stability**: Pre-bundled PortAudio (`sounddevice`), ConPTY terminal (`pywinpty`), tzdata timezone safety, and concurrent-log-handler file locking.

---

## 📁 Directory Structure

```
makima_v2/
├── pyproject.toml              # Verified production dependencies
├── README.md                   # Project overview & quickstart
├── MASTER_PLAN.md              # 6-phase engineering build roadmap
├── RESOURCES_MANIFEST.md       # Complete SDK, tool & model catalog
├── configs/
│   └── settings.yaml           # Dynamic runtime configuration
│
├── makima/
│   ├── config.py               # Dynamic Pydantic settings & env loader
│   ├── core/                   # Brain: Engine while-loop & context compressor
│   ├── provider/               # ParetoRouter & provider adapters (Gemini, Groq, Ollama)
│   ├── tools/                  # Auto-discovering modular toolsets
│   │   ├── os/                 # Apps, windows, processes, system hardware
│   │   ├── filesystem/         # Workspace-clamped I/O & SAGA undo
│   │   ├── terminal/           # Subprocess runner with ConPTY
│   │   ├── browser/            # Patchright stealth & browser-use
│   │   ├── media/              # YouTube & Spotify controller
│   │   └── documents/          # Excel, Word, PowerPoint, PDF generator
│   ├── gateway/                # Background daemon for Telegram, Discord, WhatsApp
│   ├── memory/                 # MEMORY.md + aiosqlite + sqlite-vec
│   ├── skills/                 # Dynamic Markdown skill loader & synthesizer
│   ├── voice/                  # Gemini Live bidirectional audio & Edge-TTS
│   ├── api/                    # FastAPI server & WebSocket streaming bridge
│   └── cli/                    # Rich / Typer interactive terminal
│
└── tests/                      # Fast unit & integration test suite
```

---

## 🚀 Quickstart

```bash
# 1. Create and activate virtual environment with uv
uv venv --python 3.12
.venv\Scripts\activate

# 2. Install dependencies
uv pip install -e ".[dev]"

# 3. Configure environment
copy configs\settings.yaml.example configs\settings.yaml
# Add your GEMINI_API_KEY or GROQ_API_KEY (Free tiers) in .env

# 4. Launch interactive terminal chat
python -m makima.cli chat

# 5. Launch multi-platform gateway daemon
python -m makima.cli gateway run
```

---

## 📜 Documentation Links

- [Mandatory Engineering & Swarm Rules](ENGINEERING_RULES.md) (Strict Pre-Implementation Protocol)
- [Master Build Plan (Roadmap & Priorities)](MASTER_PLAN.md)
- [Complete Resources & Library Manifest](RESOURCES_MANIFEST.md)
