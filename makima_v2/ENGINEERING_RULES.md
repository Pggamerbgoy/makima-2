# 📜 Makima v2 — Mandatory Engineering & Swarm Rules
> **Enforcement Level**: STRICT — Zero Exceptions.  
> **Applies To**: All agents, subagents, and developers working on `makima_v2`.

---

## 1. 🐝 Mandatory Ruflo Swarm Execution Protocol

Har bada task aur implementation Ruflo Swarm ke zariye coordinate aur execute hoga:
1. **Swarm Role Attribution**:
   - **`ruflo-orchestrator-lead`**: System sequencing, milestone planning, and task assignment.
   - **`ruflo-arch-auditor`**: Concurrency, lifecycle, connection pooling, and memory safety.
   - **`ruflo-code-auditor`**: Module interfaces, tool contracts, typing, and caller verification.
2. **Cognitive Swarm Workers**:
   - Native Antigravity Gemini subagents swarm workers ki tarah spawn hokar deep research, file creation, aur code auditing execute karenge.
3. **Consensus & Memory Ledger**:
   - Badi architectural decisions Raft consensus (`hive-mind_consensus`) ke through quorum vote se pass hongi.
   - Patterns aur audit logs Ruflo HNSW vector database (`memory_store`) me persist honge.

---

## 2. 📋 The 3-Step Pre-Implementation Gate (Har Module Banane Se Pehle)

Kisi bhi module, class, tool, ya feature ka code likhne se pehle ye 3 steps execute karna **mandatory** hai:

### Step 1: Explicit TODO Checklist
Har task shuru karne se pehle ek structured TODO list banayi jayegi:
- [ ] Sub-task 1: Exact capability and file to create/edit
- [ ] Sub-task 2: Unit interface and contract definitions
- [ ] Sub-task 3: Callers integration and wiring
- [ ] Sub-task 4: Live verification script execution

### Step 2: Data Flow & Call-Graph Mapping (Kaha Call Ho Raha Hai?)
Module ka code likhne se pehle uska **Data Flow diagram aur Caller-Callee mapping** document karna hoga:
- **Caller Modules**: Kaun-kaun se files/classes is module ko call karenge?
- **Callee Modules**: Ye module kin external libraries/tools ko call karega?
- **Input Parameters & Types**: Exact arguments, types, aur default values.
- **Output Return Format**: Return type (`dict`, `str`, `Pydantic model`) aur error structure.
- **Silent Regression Guard**: Kya signature change se kisi existing caller me silent `TypeError` ya `KeyError` create ho sakta hai?

### Step 3: Verification & Live Test Gate (Kaam Kar Raha Hai Ya Nahi?)
- Code likhne ke baad sirf "code looks clean" bol kar aage badhna **strictly banned** hai.
- Har module ke sath ek standalone runnable verification script (`tests/verify_<module>.py`) ya live execution command chalakar proof verify karna hoga:
  - Kya module actual runtime par execute ho raha hai?
  - Kya return values expected schema se match kar rahi hain?
  - Kya error handling aur fallbacks sahi kaam kar rahe hain?

---

## 3. 🔍 Callee + All Callers Mandate

- Jab bhi kisi function, tool, ya class ko banaya ya modify kiya jaye, **sirf uss file ko dekh kar rukna STRICTLY BANNED hai**.
- Codebase me jaha-jaha us module ko call kiya jata hai (har ek caller file aur block of code), unhe line-by-line open karke parameters aur return types cross-verify karna mandatory hai.

---

## 4. ⚡ Zero-Hardcoding & 100% Dynamic Mandate

- **Dynamic Tool Discovery**: Tools `@tool` decorator se auto-scan honge (`tools/**/`).
- **Dynamic Model Probing**: Models env vars aur Ollama `/api/tags` se runtime detect honge.
- **Dynamic Skills Engine**: `~/.makima/skills/*.md` se live Markdown load hoga.
- **Dynamic Paths**: `pathlib.Path.home()` aur `platform.system()` se OS paths resolve honge.
- **Dynamic Context**: Active model ki true context window (1M vs 128k vs 8k) dynamically calculate hogi.

---

## 5. 💰 Free-Tier & Zero-Cost First Guarantee

- **Primary Engine**: Google GenAI SDK (Gemini 2.5 Flash free tier, Gemini 3.8 Live audio).
- **OpenAI & Anthropic**: Sirf free tiers / free endpoints (OpenRouter Free `openrouter/free`, Groq Free, Cerebras Free, local Ollama).
- **Zero Paid Keys**: Kisi bhi paid credit ya mandatory credit card API subscription ki zaroorat nahi hogi.
