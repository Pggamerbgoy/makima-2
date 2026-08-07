#!/usr/bin/env python3
"""
Makima - Qwen Coder Chat Interface  |  scripts/qwen_chat.py

Single-file Python HTTP server (port 7788) + embedded premium dark chat UI.
Features:
  - ChatGPT/Gemini-style dark-mode streaming chat
  - Thinking chain display (collapsible)
  - Persistent history  ~/.makima/qwen_chat.json
  - Multi-model fallback same as qwen_session.py
  - File tools: READ_FILE / LIST_DIR / GREP / code write + py_compile
  - Model switch dropdown, Reset, Export chat

Run:
    python scripts/qwen_chat.py
Open: http://localhost:7788
"""

import glob, json, os, py_compile, re, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT       = 7788
API_URL    = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
SESSION_F  = Path.home() / ".makima" / "qwen_chat.json"
WORKSPACE  = Path(".")

MODELS = [
    "qwen3.7-max-preview",
    "qwen3.7-max-2026-05-20",
    "kimi-k2.7-code",
    "qwen-coder-plus",
    "qwen-plus",
    "qwen-max",
    "qwen3.7-max-2026-06-08",
    "qwen3-coder-plus",
]

SYS_PROMPT = """You are an Autonomous AI Agent Coder & Systems Architect for Makima OS.
Maintain full stateful context across tasks. To use tools output:
  <<READ_FILE: path>>
  <<LIST_DIR: path>>
  <<GREP: pattern>>
  ```python:path
  code
  ```
Output <<DONE>> when finished. Never ask the user to manually edit code."""

# ── Session ──────────────────────────────────────────────────────────────────
_lock = threading.Lock()

def load_session():
    if SESSION_F.exists():
        try:
            d = json.loads(SESSION_F.read_text("utf-8"))
            if isinstance(d, list) and d: return d
        except Exception: pass
    return [{"role":"system","content":SYS_PROMPT}]

def save_session(msgs):
    try:
        SESSION_F.parent.mkdir(parents=True, exist_ok=True)
        SESSION_F.write_text(json.dumps(msgs, indent=2, ensure_ascii=False), "utf-8")
    except Exception as e:
        print(f"[warn] session save: {e}")

def api_key():
    return (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip()

# ── Tools ────────────────────────────────────────────────────────────────────
def run_tools(content):
    out = []
    for p in re.findall(r"<<READ_FILE:\s*([^>]+)>>", content):
        p=p.strip()
        try: out.append(f"[READ_FILE {p}]\n{(WORKSPACE/p).read_text('utf-8',errors='replace')[:8000]}")
        except Exception as e: out.append(f"[READ_FILE {p}] ERR:{e}")
    for p in re.findall(r"<<LIST_DIR:\s*([^>]+)>>", content):
        p=p.strip()
        try:
            ents=list((WORKSPACE/p).iterdir())
            out.append(f"[LIST_DIR {p}]\n"+"\n".join(e.name+("/" if e.is_dir() else "") for e in ents[:200]))
        except Exception as e: out.append(f"[LIST_DIR {p}] ERR:{e}")
    for pat in re.findall(r"<<GREP:\s*([^>]+)>>", content):
        pat=pat.strip(); hits=[]
        for ext in ["**/*.py","**/*.ts","**/*.tsx","**/*.json"]:
            for fp in WORKSPACE.glob(ext):
                try:
                    for i,ln in enumerate(fp.read_text("utf-8",errors="replace").splitlines(),1):
                        if pat.lower() in ln.lower(): hits.append(f"{fp}:{i}: {ln.rstrip()}")
                        if len(hits)>=60: break
                except Exception: pass
                if len(hits)>=60: break
        out.append(f"[GREP '{pat}']\n"+("\n".join(hits[:60]) if hits else "No results"))
    for lang,path,code in re.findall(r"```(\w+):([^\n]+)\n(.*?)```", content, re.DOTALL):
        path=path.strip(); full=WORKSPACE/path
        try:
            full.parent.mkdir(parents=True,exist_ok=True); full.write_text(code,"utf-8")
            if lang=="python":
                try: py_compile.compile(str(full),doraise=True); out.append(f"[WRITE {path}] OK (syntax verified)")
                except py_compile.PyCompileError as ce: out.append(f"[WRITE {path}] Written but SYNTAX ERR: {ce}")
            else: out.append(f"[WRITE {path}] Written OK")
        except Exception as e: out.append(f"[WRITE {path}] ERR: {e}")
    return "\n\n".join(out)

# ── Stream ───────────────────────────────────────────────────────────────────
def stream_response(messages, model_pref=""):
    key = api_key()
    if not key:
        yield json.dumps({"error":"DASHSCOPE_API_KEY not set in environment"}) + "\n"; return
    ml = MODELS[:]
    if model_pref and model_pref in ml: ml.remove(model_pref); ml.insert(0, model_pref)
    full=""; used=""
    for idx,model in enumerate(ml):
        try:
            payload={"model":model,"messages":messages,"stream":True,"enable_thinking":True}
            req=urllib.request.Request(API_URL,data=json.dumps(payload).encode(),
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json","Accept":"text/event-stream"},
                method="POST")
            full=""
            with urllib.request.urlopen(req,timeout=120) as r:
                for line in r:
                    ls=line.decode("utf-8").strip()
                    if not ls or ls=="data: [DONE]": continue
                    if ls.startswith("data: "):
                        d=json.loads(ls[6:]); ch=d.get("choices",[])
                        if ch:
                            delta=ch[0].get("delta",{})
                            thinking=delta.get("reasoning_content") or delta.get("reasoning") or ""
                            content=delta.get("content") or ""
                            if thinking: yield json.dumps({"thinking":thinking})+"\n"
                            if content: full+=content; yield json.dumps({"content":content})+"\n"
            used=model; break
        except Exception as e:
            yield json.dumps({"system":f"Model {model} failed: {e}. Trying next..."})+"\n"
            if idx==len(ml)-1: yield json.dumps({"error":"All models exhausted"})+"\n"; return
    tool_out=run_tools(full)
    if tool_out:
        yield json.dumps({"tool_results":tool_out})+"\n"
        messages.append({"role":"assistant","content":full})
        messages.append({"role":"user","content":f"[TOOL RESULTS]\n{tool_out}\n\nContinue."})
        ext=""
        for m in ml:
            try:
                p2={"model":m,"messages":messages,"stream":True}
                r2=urllib.request.Request(API_URL,data=json.dumps(p2).encode(),
                    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json","Accept":"text/event-stream"},
                    method="POST")
                with urllib.request.urlopen(r2,timeout=120) as rr:
                    for ln in rr:
                        ls=ln.decode("utf-8").strip()
                        if not ls or ls=="data: [DONE]": continue
                        if ls.startswith("data: "):
                            d=json.loads(ls[6:]); ch=d.get("choices",[])
                            if ch:
                                c2=ch[0].get("delta",{}).get("content") or ""
                                if c2: ext+=c2; yield json.dumps({"content":c2})+"\n"
                break
            except Exception: pass
        full+="\n"+ext
    with _lock:
        messages.append({"role":"assistant","content":full})
        save_session(messages)
    yield json.dumps({"done":True,"model":used})+"\n"

# ── HTML ─────────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Qwen Coder - Makima</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0d0d;--sb:#111;--surf:#1a1a1a;--bdr:#262626;--acc:#a78bfa;--acc2:#7c3aed;--txt:#f0f0f0;--muted:#777;--user-bg:#1e1b4b;--think-bg:#0c0f15;--code-bg:#0a0a0a;--green:#22c55e;--yellow:#eab308;--red:#ef4444;--blue:#38bdf8}
html,body{height:100%;background:var(--bg);color:var(--txt);font-family:'Inter',sans-serif;overflow:hidden}
#app{display:flex;height:100vh}
#sidebar{width:256px;flex-shrink:0;background:var(--sb);border-right:1px solid var(--bdr);display:flex;flex-direction:column;padding:16px;gap:10px;overflow-y:auto}
#sidebar h1{font-size:16px;font-weight:600;display:flex;align-items:center;gap:8px;padding-bottom:4px}
.sel{width:100%;padding:8px 10px;background:var(--surf);border:1px solid var(--bdr);border-radius:8px;color:var(--txt);font-size:12.5px;font-family:inherit;outline:none;cursor:pointer}
.sel:focus{border-color:var(--acc)}
.sbtn{width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--bdr);background:var(--surf);color:var(--txt);font-size:12.5px;cursor:pointer;font-family:inherit;transition:.15s;text-align:left;display:flex;align-items:center;gap:8px}
.sbtn:hover{background:#1f1f1f;border-color:var(--acc)}
.sbtn.red:hover{border-color:var(--red);color:var(--red)}
.div{height:1px;background:var(--bdr)}
.pill{padding:6px 10px;border-radius:6px;font-size:11px;color:var(--muted);background:var(--surf);border:1px solid var(--bdr);display:flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green)}
.dot.thinking{background:var(--yellow);animation:pu .7s infinite}
.dot.streaming{background:var(--acc);animation:pu .4s infinite}
@keyframes pu{0%,100%{opacity:1}50%{opacity:.3}}
.sinfo{font-size:11px;color:var(--muted);line-height:1.8}
#main{flex:1;display:flex;flex-direction:column;overflow:hidden}
#msgs{flex:1;overflow-y:auto;padding:20px 0;display:flex;flex-direction:column;scroll-behavior:smooth}
#msgs::-webkit-scrollbar{width:4px}
#msgs::-webkit-scrollbar-thumb{background:#222;border-radius:2px}
.row{display:flex;padding:12px 24px;gap:12px;animation:fi .2s ease}
@keyframes fi{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}
.row:hover{background:rgba(255,255,255,.016)}
.row.user{flex-direction:row-reverse}
.av{width:30px;height:30px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:13px;background:linear-gradient(135deg,var(--acc2),var(--acc));color:#fff;font-weight:600;align-self:flex-start;margin-top:2px}
.av.u{background:linear-gradient(135deg,#1e40af,#3b82f6)}
.mbody{max-width:800px;min-width:0}
.mmeta{font-size:11px;color:var(--muted);margin-bottom:4px;font-weight:500}
.mcontent{font-size:14px;line-height:1.72;color:var(--txt)}
.mcontent p{margin:0 0 8px}.mcontent p:last-child{margin:0}
.think{background:var(--think-bg);border:1px solid var(--bdr);border-left:3px solid var(--yellow);border-radius:8px;padding:9px 13px;margin-bottom:9px;cursor:pointer}
.think-h{font-size:10.5px;font-weight:600;color:var(--yellow);display:flex;align-items:center;gap:6px;letter-spacing:.5px;text-transform:uppercase;user-select:none}
.think-b{font-size:12px;color:#888;line-height:1.6;font-family:'JetBrains Mono',monospace;max-height:200px;overflow:hidden;margin-top:7px;transition:max-height .3s}
.think.exp .think-b{max-height:2000px}
.tools{background:#080e14;border:1px solid #1a3a5c;border-left:3px solid var(--blue);border-radius:8px;padding:9px 13px;margin-bottom:9px}
.tools-h{font-size:10.5px;font-weight:600;color:var(--blue);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.tools pre{font-size:11.5px;color:#7ea;white-space:pre-wrap;word-break:break-word;max-height:260px;overflow-y:auto}
.cb{background:var(--code-bg);border:1px solid #2d2d2d;border-radius:8px;overflow:hidden;margin:8px 0}
.cb-h{display:flex;align-items:center;justify-content:space-between;padding:7px 13px;background:#0f0f0f;border-bottom:1px solid #2d2d2d}
.cb-l{font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace}
.cb-cp{font-size:11px;color:var(--muted);background:none;border:none;cursor:pointer;padding:2px 7px;border-radius:4px;transition:.15s;font-family:inherit}
.cb-cp:hover{background:#1f1f1f;color:var(--acc)}
.cb pre{padding:13px;overflow-x:auto;font-size:12.5px;font-family:'JetBrains Mono',monospace;color:#c9d1d9;line-height:1.55}
code{background:#1e1e1e;padding:1px 5px;border-radius:4px;font-family:'JetBrains Mono',monospace;font-size:13px}
.sysmsg{margin:4px 24px;padding:7px 13px;background:#111;border:1px solid var(--bdr);border-radius:8px;font-size:12px;color:var(--muted);text-align:center}
#inp-area{padding:16px 24px;border-top:1px solid var(--bdr);background:var(--bg)}
#inp-box{width:100%;display:flex;gap:10px;background:var(--surf);border:1px solid var(--bdr);border-radius:12px;padding:11px 13px;align-items:flex-end;transition:border-color .2s}
#inp-box:focus-within{border-color:var(--acc);box-shadow:0 0 0 3px rgba(167,139,250,.1)}
#utxt{flex:1;background:none;border:none;color:var(--txt);font-size:14px;font-family:'Inter',sans-serif;resize:none;outline:none;min-height:22px;max-height:160px;line-height:1.5}
#utxt::placeholder{color:var(--muted)}
#sbtn{width:34px;height:34px;border-radius:8px;border:none;cursor:pointer;flex-shrink:0;background:linear-gradient(135deg,var(--acc2),var(--acc));color:#fff;display:flex;align-items:center;justify-content:center;transition:.15s;font-size:15px}
#sbtn:hover{transform:scale(1.07);box-shadow:0 4px 12px rgba(124,58,237,.4)}
#sbtn:disabled{opacity:.35;cursor:not-allowed;transform:none}
#hint{font-size:11px;color:var(--muted);margin-top:6px;text-align:center}
#welcome{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:40px;text-align:center}
#welcome h2{font-size:22px;font-weight:600;background:linear-gradient(135deg,var(--acc),#c4b5fd);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
#welcome p{font-size:13.5px;color:var(--muted);max-width:400px;line-height:1.7}
.chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:4px}
.chip{padding:7px 15px;background:var(--surf);border:1px solid var(--bdr);border-radius:20px;font-size:12.5px;color:var(--txt);cursor:pointer;transition:.15s}
.chip:hover{border-color:var(--acc);color:var(--acc)}
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h1><span>🤖</span> Qwen Coder</h1>
    <div class="pill"><div class="dot" id="sdot"></div><span id="stxt">Ready</span></div>
    <div class="div"></div>
    <div style="font-size:10.5px;color:var(--muted);font-weight:600;letter-spacing:.5px">MODEL</div>
    <select class="sel" id="msel">
      <option value="">Auto (Fallback Chain)</option>
      <option value="qwen3.7-max-preview">qwen3.7-max-preview ⚡</option>
      <option value="qwen3.7-max-2026-05-20">qwen3.7-max-2026-05-20</option>
      <option value="kimi-k2.7-code">kimi-k2.7-code</option>
      <option value="qwen-coder-plus">qwen-coder-plus</option>
      <option value="qwen-plus">qwen-plus</option>
      <option value="qwen-max">qwen-max</option>
      <option value="qwen3-coder-plus">qwen3-coder-plus</option>
    </select>
    <div class="div"></div>
    <div style="font-size:10.5px;color:var(--muted);font-weight:600;letter-spacing:.5px">ACTIONS</div>
    <button class="sbtn" onclick="exportChat()">💾 Export Chat</button>
    <button class="sbtn red" onclick="resetSession()">🗑 Reset Session</button>
    <div class="div"></div>
    <div class="sinfo" id="sinfo">Turns: 0</div>
  </div>
  <div id="main">
    <div id="msgs">
      <div id="welcome">
        <h2>Qwen Coder Chat</h2>
        <p>Premium ChatGPT-style interface for Qwen Coder with file tools, streaming thinking chain, and persistent session history.</p>
        <div class="chips">
          <div class="chip" onclick="sendQ('List all agents in apps/brain/agents/')">📦 List Agents</div>
          <div class="chip" onclick="sendQ('Read apps/brain/command_router.py and summarize routing logic')">📄 Read Router</div>
          <div class="chip" onclick="sendQ('What models are available in ai_handler.py?')">🤖 AI Models</div>
          <div class="chip" onclick="sendQ('Show the overall architecture of this Makima OS codebase')">🏗 Architecture</div>
          <div class="chip" onclick="sendQ('GREP: class BaseAgent')">🔍 Find BaseAgent</div>
        </div>
      </div>
    </div>
    <div id="inp-area">
      <div id="inp-box">
        <textarea id="utxt" placeholder="Message Qwen Coder... (Shift+Enter for newline)" rows="1" onkeydown="onKey(event)"></textarea>
        <button id="sbtn" onclick="send()" title="Send">➤</button>
      </div>
      <div id="hint">Enter to send · Shift+Enter newline · File tools auto-run</div>
    </div>
  </div>
</div>
<script>
let hist=[], streaming=false;
const ta=document.getElementById('utxt');
ta.addEventListener('input',()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,160)+'px'});
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}
function sendQ(t){ta.value=t;send()}
function setStatus(s,t){document.getElementById('sdot').className='dot '+s;document.getElementById('stxt').textContent=t}
function updateInfo(){
  const u=hist.filter(m=>m.role==='user').length;
  document.getElementById('sinfo').innerHTML=`Turns: <b>${u}</b><br>Messages: <b>${hist.filter(m=>m.role!=='system').length}</b><br>Model: <b>${document.getElementById('msel').value||'Auto'}</b>`
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function md(t){
  t=t.replace(/```(\\w+)?:?([^\\n]*)\\n([\\s\\S]*?)```/g,(_,lang,path,code)=>{
    const l=lang||'text',lbl=path?`${l}:${path}`:l,id='c'+Math.random().toString(36).slice(2);
    return `<div class="cb"><div class="cb-h"><span class="cb-l">${esc(lbl)}</span><button class="cb-cp" onclick="cp('${id}')">Copy</button></div><pre id="${id}">${esc(code.replace(/\\n$/,''))}</pre></div>`
  });
  t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
  t=t.replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>');
  t=t.replace(/\\*(.+?)\\*/g,'<em>$1</em>');
  return t.split(/\\n\\n+/).map(p=>{const l=p.split('\\n').join('<br>');return l.startsWith('<div')||l.startsWith('<pre')?l:`<p>${l}</p>`}).join('')
}
function cp(id){const e=document.getElementById(id);if(e)navigator.clipboard.writeText(e.textContent).catch(()=>{})}
function appendMsg(role,content,thinking='',toolRes=''){
  document.getElementById('welcome')?.remove();
  const msgs=document.getElementById('msgs');
  const row=document.createElement('div'); row.className='row'+(role==='user'?' user':'');
  const av=document.createElement('div'); av.className='av'+(role==='user'?' u':''); av.textContent=role==='user'?'U':'Q';
  const body=document.createElement('div'); body.className='mbody';
  const meta=document.createElement('div'); meta.className='mmeta'; meta.textContent=role==='user'?'You':'Qwen Coder';
  body.appendChild(meta);
  if(thinking){const tb=document.createElement('div');tb.className='think';tb.innerHTML=`<div class="think-h">🧠 Thinking <small style="font-weight:400;opacity:.6">(click)</small></div><div class="think-b">${esc(thinking)}</div>`;tb.addEventListener('click',()=>tb.classList.toggle('exp'));body.appendChild(tb)}
  if(toolRes){const tr=document.createElement('div');tr.className='tools';tr.innerHTML=`<div class="tools-h">🔧 Tool Results</div><pre>${esc(toolRes)}</pre>`;body.appendChild(tr)}
  const mc=document.createElement('div');mc.className='mcontent';
  mc.innerHTML=role==='user'?`<p>${esc(content).replace(/\\n/g,'<br>')}</p>`:md(content);
  body.appendChild(mc); row.appendChild(av); row.appendChild(body); msgs.appendChild(row);
  msgs.scrollTop=msgs.scrollHeight; return {mc,body,meta};
}
async function send(){
  if(streaming)return;
  const input=ta.value.trim(); if(!input)return;
  ta.value=''; ta.style.height='auto';
  appendMsg('user',input);
  hist.push({role:'user',content:input});
  document.getElementById('sbtn').disabled=true; streaming=true; setStatus('streaming','Connecting...');
  const msgs=document.getElementById('msgs');
  document.getElementById('welcome')?.remove();
  const row=document.createElement('div'); row.className='row';
  const av=document.createElement('div'); av.className='av'; av.textContent='Q';
  const body=document.createElement('div'); body.className='mbody';
  const meta=document.createElement('div'); meta.className='mmeta'; meta.textContent='Qwen Coder';
  body.appendChild(meta);
  const tb=document.createElement('div'); tb.className='think'; tb.style.display='none';
  tb.innerHTML='<div class="think-h">🧠 Thinking... <small style="font-weight:400;opacity:.6">(click)</small></div><div class="think-b" id="ltb"></div>';
  tb.addEventListener('click',()=>tb.classList.toggle('exp')); body.appendChild(tb);
  const mc=document.createElement('div'); mc.className='mcontent';
  mc.innerHTML='<span style="color:var(--muted)">▋</span>'; body.appendChild(mc);
  row.appendChild(av); row.appendChild(body); msgs.appendChild(row);
  let full='', thinking='', usedModel='';
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:hist,model:document.getElementById('msel').value})});
    const reader=r.body.getReader(), dec=new TextDecoder();
    while(true){
      const{done,value}=await reader.read(); if(done)break;
      const lines=dec.decode(value).split('\\n').filter(l=>l.trim());
      for(const line of lines){
        try{
          const d=JSON.parse(line);
          if(d.error){mc.innerHTML=`<p style="color:var(--red)">⛔ ${esc(d.error)}</p>`}
          else if(d.thinking){thinking+=d.thinking;tb.style.display='';setStatus('thinking','Thinking...');document.getElementById('ltb').textContent=thinking.slice(-600)}
          else if(d.content){full+=d.content;setStatus('streaming','Streaming...');mc.innerHTML=md(full)+'<span style="color:var(--muted)">▋</span>'}
          else if(d.tool_results){const tr=document.createElement('div');tr.className='tools';tr.innerHTML=`<div class="tools-h">🔧 Tool Results</div><pre>${esc(d.tool_results)}</pre>`;body.insertBefore(tr,mc)}
          else if(d.system){const s=document.createElement('div');s.className='sysmsg';s.textContent=d.system;msgs.appendChild(s)}
          else if(d.done)usedModel=d.model||''
        }catch(e){}
      }
      msgs.scrollTop=msgs.scrollHeight;
    }
  }catch(err){mc.innerHTML=`<p style="color:var(--red)">⛔ ${esc(String(err))}</p>`}
  mc.innerHTML=md(full||'(no response)');
  if(thinking){tb.querySelector('.think-b').textContent=thinking;tb.style.display=''}
  if(usedModel)meta.innerHTML=`Qwen Coder <small style="color:var(--muted);font-weight:400">(${esc(usedModel)})</small>`;
  hist.push({role:'assistant',content:full});
  streaming=false; document.getElementById('sbtn').disabled=false;
  setStatus('idle','Ready'); updateInfo(); msgs.scrollTop=msgs.scrollHeight;
}
async function resetSession(){
  if(!confirm('Clear entire session history?'))return;
  await fetch('/reset',{method:'POST'}); hist=[];
  document.getElementById('msgs').innerHTML='<div class="sysmsg" style="margin-top:40px">Session cleared. Start fresh!</div>';
  updateInfo();
}
function exportChat(){
  const txt=hist.filter(m=>m.role!=='system').map(m=>`[${m.role.toUpperCase()}]\\n${m.content}`).join('\\n\\n---\\n\\n');
  const a=document.createElement('a');a.href='data:text/plain;charset=utf-8,'+encodeURIComponent(txt);a.download=`qwen-chat-${Date.now()}.txt`;a.click();
}
async function loadSession(){
  try{const r=await fetch('/history');const d=await r.json();hist=d.messages||[];
    const nm=hist.filter(m=>m.role!=='system');
    if(nm.length>0){document.getElementById('welcome')?.remove();for(const m of nm)appendMsg(m.role,m.content);document.getElementById('msgs').scrollTop=99999}
  }catch(e){}
  updateInfo(); setStatus('idle','Ready');
}
loadSession();
</script>
</body>
</html>"""

# ── HTTP Handler ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _json(self,d,c=200):
        b=json.dumps(d).encode(); self.send_response(c)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",len(b)); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path in ("/","/index.html"):
            b=HTML.encode("utf-8"); self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",len(b)); self.end_headers(); self.wfile.write(b)
        elif self.path=="/history":
            with _lock: msgs=load_session()
            self._json({"messages":msgs})
        else: self.send_response(404); self.end_headers()
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0)); body=self.rfile.read(n)
        if self.path=="/reset":
            with _lock:
                if SESSION_F.exists(): SESSION_F.unlink()
            self._json({"ok":True}); return
        if self.path=="/chat":
            try:
                req=json.loads(body); msgs=req.get("messages",[]); model=req.get("model","")
                if not msgs or msgs[0].get("role")!="system": msgs=[{"role":"system","content":SYS_PROMPT}]+msgs
                self.send_response(200); self.send_header("Content-Type","application/x-ndjson"); self.send_header("Transfer-Encoding","chunked"); self.end_headers()
                for chunk in stream_response(msgs, model):
                    d=chunk.encode("utf-8"); self.wfile.write(f"{len(d):x}\r\n".encode()); self.wfile.write(d); self.wfile.write(b"\r\n"); self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n"); self.wfile.flush()
            except Exception as e:
                try: err=json.dumps({"error":str(e)}).encode(); self.wfile.write(f"{len(err):x}\r\n".encode()); self.wfile.write(err); self.wfile.write(b"\r\n0\r\n\r\n"); self.wfile.flush()
                except Exception: pass
            return
        self.send_response(404); self.end_headers()

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    key=api_key()
    if not key:
        print("WARNING: DASHSCOPE_API_KEY not set!")
        print("  PowerShell: $env:DASHSCOPE_API_KEY = 'your-key'")
    else:
        print(f"API Key: {key[:8]}...{key[-4:]}")
    srv=HTTPServer(("0.0.0.0",PORT),Handler)
    print(f"\n Qwen Coder Chat  -->  http://localhost:{PORT}")
    print(f" Workspace: {WORKSPACE.resolve()}")
    print(f" Session:   {SESSION_F}")
    print(f" Press Ctrl+C to stop\n")
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nBye!"); srv.server_close()
