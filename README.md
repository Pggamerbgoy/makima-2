# ⚡ Makima — Autonomous AI Desktop Assistant

> **A high-performance, multi-agent AI assistant for Windows built with Python, Rust, and Tauri React.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Rust](https://img.shields.io/badge/Rust-1.75%2B-orange.svg)](https://www.rust-lang.org/)
[![Tauri](https://img.shields.io/badge/Tauri-v2-FFC131.svg)](https://tauri.app/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#license)

---

## 🌟 Overview

**Makima** is an advanced autonomous desktop assistant engineered for deep OS integration, real-time multi-agent execution, local memory persistence, and voice control. It pairs a **Python multi-agent orchestrator** with a high-performance **Rust core engine** and a modern **Tauri + React UI**.

---

## ✨ Core Features

- 🤖 **Specialized Multi-Agent Swarm**:
  - **Commander Agent**: DAG task decomposition & parallel sub-agent dispatch.
  - **Code Agent**: Refactoring, syntax debugging, and Qwen Coder routing.
  - **Browser Agent**: Web automation, page scraping, and form filling.
  - **DevOps Agent**: CI/CD diagnostics, build scripts, and git workflows.
  - **Security Agent**: Hardcoded secret scanning, SQL injection checks, and static vulnerability audits.
  - **Media Agent**: YouTube & Spotify media playback controls.
  - **Messaging Agent**: WhatsApp, Telegram, Discord, and Email drafting.
  - **Memory Agent**: EternalMemory vector index & HNSW vector search.
  - **Voice Agent**: Kokoro-ONNX / Edge-TTS speech synthesis & Whisper STT.

- ⚡ **High-Speed Rust Core (`makima-core`)**:
  - Triple-store knowledge graph for entity relationships.
  - HNSW vector index for instant semantic memory lookup.
  - State checkpointer with atomic SQLite WAL durability.

- 🖥️ **Sleek Tauri Desktop UI**:
  - Responsive dark-mode interface built with React & TypeScript.
  - Real-time WebSocket event bridge (`ws://127.0.0.1:8080/ws`).
  - Overlay floating window mode & Control Center.

---

## 🏗️ Architecture

```
makima/
├── apps/
│   ├── brain/             # Python Multi-Agent Core Engine & WebSockets
│   └── ui/                # Tauri + React + TypeScript Desktop Interface
├── crates/
│   └── makima-core/       # Rust native backend (Vector Index & Triple Store)
├── native/                # C++ native OS bridges (Screen, Audio, Clipboard)
├── configs/               # System & Agent configuration files
├── proto/                 # gRPC protocol buffer definitions
├── scripts/               # Utility & verification scripts
└── requirements.txt       # Unified Python dependencies
```

---

## 🚀 Quick Start

### Prerequisites

Ensure you have the following installed on your machine:
- **Python**: 3.11+
- **Node.js**: 18+ & `npm`
- **Rust**: 1.75+ & `cargo` (for building native core)

### 1. Installation

Clone the repository and install all Python dependencies:

```bash
git clone https://github.com/Pggamerbgoy/makima-2.git
cd makima-2

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Frontend Setup (Tauri UI)

```bash
cd apps/ui
npm install
cd ../..
```

### 3. Configuration

Set up your API keys and configuration in `configs/default.yaml` or set environment variables:

```bash
# Optional environment variables
set MAKIMA_OPENROUTER_KEY=your_openrouter_api_key
set MAKIMA_GITHUB_KEY=your_github_pat_token
```

---

## 🏃 Running Makima

### Start the Brain Server (Backend)

```bash
python -m apps.brain.main
```
*The backend server starts on `http://127.0.0.1:8080` and opens WebSocket endpoint at `ws://127.0.0.1:8080/ws`.*

### Start Desktop UI (Frontend)

```bash
cd apps/ui
npm run tauri dev
```

Or simply run the shortcut launcher:
```cmd
StartMakima.bat
```

---

## 🛡️ Security & Privacy

Makima is built with strict privacy and security standards:
- **Local Memory**: Conversation history and vector memory are stored locally in SQLite database files.
- **Secret Protection**: Automated secret scanning prevents accidental leakage of API keys or credentials.
- **Environment Isolation**: Execution paths are strictly scoped within local workspace boundaries.

---

## 📜 License

This project is licensed under the **MIT License**.
