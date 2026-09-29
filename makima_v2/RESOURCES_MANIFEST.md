# 🌐 Makima v2 — Complete Resource, SDK & Model Manifest
> **Pattern**: Nous Research Hermes Agent + Makima Dynamic Pareto Routing  
> **Cost Policy**: Free-Tier & Zero-Cost First Guarantee  
> **Design Mandate**: Zero Hardcoding & 100% Dynamic Architecture  

---

## 1. 🏛️ Official AI SDKs & Standard Protocols

| SDK / Protocol | Exact Package | Key Capabilities & Architecture Role | Cost / Tier Policy |
|---|---|---|---|
| **OpenAI Python SDK** | `openai>=1.55.0` | Standard ChatCompletions, `strict: true` Structured Outputs, Tool Use protocol. Compatible with Groq, DeepSeek, Cerebras, Qwen, OpenRouter. | **Free Only**: OpenRouter Free (`openrouter/free`), Groq Free, Cerebras Free. Zero mandatory paid OpenAI credit. |
| **OpenAI Agents SDK** | `openai-agents>=0.1.0` | Handoff primitives, `@function_tool` auto-generation, multi-agent tracing. | Free-tier compatible with open-source backends. |
| **Google GenAI SDK** | `google-genai>=2.22.0` | **Primary Workhorse**: Gemini 2.5 Flash, Gemini 3.8 Live bidirectional audio over WebSockets (16kHz PCM in / 24kHz out) with native barge-in. | **Google AI Studio Free Tier**: 15 RPM / 1M TPM free quota. |
| **Anthropic Claude SDK** | `anthropic>=0.39.0` | Extended thinking (`<thinking>`), prompt caching, Computer Use protocol. | **Free Only**: Fallback via OpenRouter free tier or free trial keys. No paid credits required. |
| **Ollama Local SDK** | `ollama>=0.4.0` | Local offline execution of Qwen 2.5 Coder, Llama 3.2, DeepSeek-R1. | **100% Free**: Zero internet, zero API keys, on-device. |
| **Model Context Protocol (MCP)** | `mcp[cli]>=2.0.0`, `httpx2>=2.7.0` | Standard MCP client + server protocol. Seamlessly consumes GitHub MCP, Filesystem MCP, Postgres MCP. | **100% Free & Open Source Standard**. |

---

## 2. 💾 Pre-trained Local Weights & Offline Models

| Resource | Model Asset / Spec | Size | Role in Makima v2 |
|---|---|---|---|
| **Local Coding LLM** | `qwen2.5-coder:7b` (Ollama) | ~4.5 GB | Offline code generation, bug fixing, bash command translation. |
| **Local Fast Chat** | `llama3.2:3b` (Ollama) | ~2.0 GB | Ultra-fast offline conversation on CPU. |
| **Local Reasoning** | `deepseek-r1:8b` (Ollama) | ~4.9 GB | Offline deep reasoning with chain-of-thought. |
| **Vector Embeddings** | `BAAI/bge-small-en-v1.5` (`fastembed`) | ~130 MB | On-device text embeddings via ONNX Runtime without PyTorch. |
| **Local Neural TTS** | `kokoro-v1.0.onnx` + `voices-v1.0.bin` | ~82M params | Human-quality on-device speech synthesis (Zero network delay). |
| **Local STT** | `faster-whisper-small` (CTranslate2) | ~480 MB | Offline Speech-to-Text with Silero VAD silence suppression. |
| **Wake Word** | `openwakeword` / `pvporcupine` | ~5 MB | Always-listening wake word detection ("Hey Makima") on CPU with <1% usage. |

---

## 3. 🌍 Free Developer Web APIs & Live Services

| API Service | Endpoint / Integration | Authentication | Role in Makima v2 |
|---|---|---|---|
| **Jina Reader** | `https://r.jina.ai/<url>` | None (Free) | Converts any webpage into clean, ad-free Markdown for LLM consumption. |
| **DuckDuckGo Gateway** | `https://html.duckduckgo.com/html/` | None (Free) | Fast live web search without paid Google Search API keys. |
| **Open-Meteo Weather** | `https://api.open-meteo.com/v1/forecast` | None (Free) | Real-time weather, hourly forecast, and rain alerts. |
| **Yahoo Finance** | `yfinance` internal API | None (Free) | Live stock prices, cryptocurrency rates, and forex conversion. |

---

## 4. 🖥️ OS & Hardware Native Integration Assets (Windows & Cross-Platform)

| Component | Library / API | Role in Makima v2 |
|---|---|---|
| **Interactive Terminal** | `pywinpty` (ConPTY) | Windows Pseudoconsole for interactive CLI commands (`[y/N]` prompts) without pipe freeze. |
| **Active Browser CDP** | Chrome DevTools Protocol (`:9222`) | Attaches to user's existing Chrome window to use logged-in cookies and sessions. |
| **Stealth Browser** | `patchright` | Strips `--enable-automation` and CDP flags to bypass Cloudflare/Akamai bot detection. |
| **Autonomous Web** | `browser-use` | Goal-oriented visual web agent (DOM interactive tags + screenshots). |
| **Window & Process Control** | `pywin32`, `psutil`, `pyautogui` | Window focus, snap, minimize, Task Manager CPU/RAM monitor, mouse & keyboard automation. |
| **Audio I/O** | `sounddevice` | Pre-bundled PortAudio DLLs for crash-free microphone input and speaker streaming on Windows. |
| **Timezones** | `tzdata` | IANA timezone database for Windows, preventing Python `zoneinfo` crashes. |
| **File Locking Log** | `concurrent-log-handler` | Windows file-locking safe log rotation across multi-process daemons. |

---

## 5. 📱 Multi-Platform Messaging Gateway Assets

| Platform | Library | Connection Architecture |
|---|---|---|
| **Telegram** | `python-telegram-bot[webhooks]>=21.8` | Long-polling / webhook bot daemon with voice memo, photo, and command dispatch. |
| **Discord** | `discord.py[voice]>=2.4.0` | Discord bot integration with slash commands, thread persistence, and voice mode. |
| **Slack** | `slack-bolt>=1.21.0` | Slack Socket Mode runner (bina public URL ke local firewall ke piche chalta hai). |
| **WhatsApp** | Baileys / QR Web Pair Bridge | WhatsApp Web QR scan karke linked-device banega (bina Meta API fees ke). |
| **Email** | `aiosmtplib` + `aioimaplib` | Async IMAP listener (unread emails trigger tasks) + SMTP reply dispatcher. |
| **Cron Scheduling** | `apscheduler>=3.10.4` | Background cron jobs, recurring reminders, and periodic health pings. |

---

## 6. 📄 Document Generation & Office Libraries

| Format | Library | Capability |
|---|---|---|
| **Excel (`.xlsx`)** | `openpyxl>=3.1.5` | Formatted sheets, cell formulas, corporate color fills, native charts. |
| **Word (`.docx`)** | `python-docx>=1.1.2` | Structured reports with headings, styled tables, bullet lists. |
| **PowerPoint (`.pptx`)**| `python-pptx>=1.0.2` | Modern 16:9 widescreen presentation slide generation. |
| **PDF Export** | Playwright `page.pdf()` / `reportlab` | Zero-C-DLL clean PDF generation (Avoids WeasyPrint MSYS2/GTK roadblocks on Windows). |
| **PDF Extraction** | `pdfplumber>=0.11.4` | Precise tabular and textual extraction from scanned documents/invoices. |
