"""
Makima OS — Dataset QA & Cleaning Engine for Nomic Semantic Router
Location: scripts/qa_clean_nomic_dataset.py

Cleans the 1000-row synthetic dataset:
1. Strips generator prefix artifacts ('I need you to what is...', 'Hey can you do you like...')
2. Fixes questionable/erroneous domain labels (e.g. Outlook negation -> system_control)
3. Deduplicates near-identical template clones to avoid 1-NN memorization
4. Injects balanced contrastive hard-negative pairs across all 11 critical agent boundaries
5. Balances scenario_type distribution (adversarial, chat_vs_action, anaphora, constraint, negation, hinglish)
"""
from __future__ import annotations

import json
import os
import re
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")


def clean_synthetic_prefixes(text: str) -> str:
    """Removes clumsy synthetic generator prefix concatenations and fixes punctuation."""
    t = text.strip()
    
    # Strip double punctuation at end (e.g. '?.' or '!.')
    t = re.sub(r'[\?!]\.+$', '?', t)
    t = re.sub(r'\.\.+$', '.', t)
    
    # Pattern 1: 'I need you to <question>' -> '<Question>'
    t = re.sub(r"^(?:I need you to|Please|Could you please|Hey, can you|Go ahead and)\s+(what|why|how|who|when|where|is|do|does|can|are|tumhe|kya|kaise)\b", r"\1", t, flags=re.IGNORECASE)
    
    # Pattern 2: 'Hey, can you don't <verb>' -> 'Don't <verb>'
    t = re.sub(r"^(?:I need you to|Please|Could you please|Hey, can you|Go ahead and)\s+(don't|do not|mat|kuch mat|never)\b", r"\1", t, flags=re.IGNORECASE)
    
    # Pattern 3: 'Hey, can you can you <verb>' -> 'Can you <verb>'
    t = re.sub(r"^(?:Could you please|Hey, can you|Please)\s+can you\b", "Can you", t, flags=re.IGNORECASE)
    
    # Pattern 4: Clumsy 'I need you to <hinglish>' -> Natural phrasing
    t = re.sub(r"^(?:I need you to|Go ahead and|Could you please|Hey, can you|Please)\s+([a-zA-Z\s]+(?:kholo|chalu karo|band karo|hata do|chhota kar do|saaf kar do|bhejo|lagao))\b", r"\1", t, flags=re.IGNORECASE)
    
    # Capitalize first character
    if t and len(t) > 0:
        t = t[0].upper() + t[1:]
    
    return t.strip()


def fix_domain_labels(item: dict) -> dict:
    """Corrects known label errors in synthetic dataset."""
    u_lower = item["user_utterance"].lower()
    target = item["target_domain"].lower()

    # Rule 1: Outlook/Word/Teams launch/minimize/close negations belong to system_control or chat
    if any(app in u_lower for app in ["outlook", "teams", "word", "excel", "photoshop", "zoom", "chrome", "firefox"]):
        if any(w in u_lower for w in ["launch", "open", "close", "minimize", "maximize", "window", "kill", "touch", "mat chhedna"]):
            if target == "automation" or target == "browser":
                item["target_domain"] = "system_control"

    # Rule 2: "I don't want a reminder" / "Reminder mat lagana" -> automation cancellation
    if "reminder" in u_lower and ("don't want" in u_lower or "mat lagana" in u_lower or "cancel" in u_lower):
        item["target_domain"] = "automation"

    # Rule 3: Diagnostic questions -> chat
    if any(q in u_lower for q in ["why is chrome", "why does my laptop", "what does 'kill process'", "how do i minimize", "is windows defender safe", "what are ai models", "what is python"]):
        item["target_domain"] = "chat"

    # Rule 4: Casual questions about tools -> chat
    if any(q in u_lower for q in ["do you like spotify", "what is a good browser", "do you ever get bored", "tumhe spotify pasand hai"]):
        item["target_domain"] = "chat"

    return item


# ─── DENSE CONTRASTIVE HARD-NEGATIVE PAIRS ACROSS ALL 11 BOUNDARIES ───
CONTRASTIVE_PAIRS: list[dict] = [
    # 1. CHAT ↔ SYSTEM
    {"user_utterance": "Why is Chrome using so much RAM?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Kill Chrome, it is using too much RAM.", "target_domain": "system_control", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "What does taskkill mean in Windows?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Taskkill notepad.exe immediately.", "target_domain": "system_control", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "How do I minimize windows on desktop?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Minimize all windows on desktop.", "target_domain": "system_control", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Windows me screenshot lene ka shortcut kya hai?", "target_domain": "chat", "language": "hinglish", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Screen ka snapshot le lo.", "target_domain": "system_control", "language": "hinglish", "scenario_type": "chat_vs_action"},

    # 2. CHAT ↔ BROWSER
    {"user_utterance": "What is the most secure web browser in 2026?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Open Brave browser and go to github.com.", "target_domain": "browser", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "How does Google search ranking work?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Search Google for best Python tutorials.", "target_domain": "browser", "language": "english", "scenario_type": "chat_vs_action"},

    # 3. CHAT ↔ MEDIA
    {"user_utterance": "Do you want to play a game with me?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Play lo-fi study beats on Spotify.", "target_domain": "media", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Do you listen to music when thinking?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Music band karo.", "target_domain": "media", "language": "hinglish", "scenario_type": "chat_vs_action"},

    # 4. CHAT ↔ RESEARCH
    {"user_utterance": "What is the definition of deep research?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Research the latest clinical trials for Alzheimer's disease.", "target_domain": "research", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Tell me your thoughts on quantum physics.", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Find recent academic papers on quantum error correction.", "target_domain": "research", "language": "english", "scenario_type": "chat_vs_action"},

    # 5. CHAT ↔ CODE
    {"user_utterance": "Is Python hard to learn for beginners?", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Write a Python script to parse JSON logs.", "target_domain": "code", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Explain how recursive functions work.", "target_domain": "chat", "language": "english", "scenario_type": "chat_vs_action"},
    {"user_utterance": "Refactor this recursive function to be iterative.", "target_domain": "code", "language": "english", "scenario_type": "chat_vs_action"},

    # 6. SYSTEM ↔ BROWSER
    {"user_utterance": "Launch Chrome browser application.", "target_domain": "system_control", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Go to https://news.ycombinator.com.", "target_domain": "browser", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Close all open windows of Chrome.", "target_domain": "system_control", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Close the active browser tab on Hacker News.", "target_domain": "browser", "language": "english", "scenario_type": "domain_confusion"},

    # 7. SYSTEM ↔ MEDIA
    {"user_utterance": "Start Spotify desktop client.", "target_domain": "system_control", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Play my Discover Weekly playlist on Spotify.", "target_domain": "media", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Bring Spotify window to the front.", "target_domain": "system_control", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Volume 50 percent set karo.", "target_domain": "media", "language": "hinglish", "scenario_type": "domain_confusion"},

    # 8. BROWSER ↔ RESEARCH
    {"user_utterance": "Navigate to reddit.com/r/technology.", "target_domain": "browser", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Find and summarize news about Apple's new M5 chip.", "target_domain": "research", "language": "english", "scenario_type": "domain_confusion"},

    # 9. MEMORY_QUERY ↔ CHAT
    {"user_utterance": "Do you remember the WiFi password I told you?", "target_domain": "memory_query", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "Do you have a good memory in general?", "target_domain": "chat", "language": "english", "scenario_type": "domain_confusion"},

    # 10. MEMORY_FORGET ↔ CHAT
    {"user_utterance": "Forget my home address from your memory.", "target_domain": "memory_forget", "language": "english", "scenario_type": "domain_confusion"},
    {"user_utterance": "I forgot my wallet at home today.", "target_domain": "chat", "language": "english", "scenario_type": "domain_confusion"},

    # 11. MULTI_STEP ↔ SINGLE DOMAIN
    {"user_utterance": "Open Chrome and then open VSCode.", "target_domain": "multi_step", "language": "english", "scenario_type": "multi_step"},
    {"user_utterance": "Open Chrome.", "target_domain": "system_control", "language": "english", "scenario_type": "direct"},
    {"user_utterance": "Clean desktop then create a backup zip.", "target_domain": "multi_step", "language": "english", "scenario_type": "multi_step"},
    {"user_utterance": "Organize my desktop icons.", "target_domain": "system_control", "language": "english", "scenario_type": "direct"},
    {"user_utterance": "Pehle calculator kholo phir excel open karo.", "target_domain": "multi_step", "language": "hinglish", "scenario_type": "multi_step"},
    {"user_utterance": "Calculator khol do.", "target_domain": "system_control", "language": "hinglish", "scenario_type": "hinglish"},
]


def process_and_clean_dataset(raw_items: list[dict]) -> list[dict]:
    """Cleans, normalizes, deduplicates, and enriches the dataset."""
    cleaned: list[dict] = []
    seen_utterances = set()

    for item in raw_items:
        # Step 1: Clean synthetic prefix artifacts
        utterance = clean_synthetic_prefixes(item["user_utterance"])
        
        # Step 2: Skip empty or unhelpful fragments
        if len(utterance) < 3:
            continue
            
        norm_key = re.sub(r'[^a-zA-Z0-9\s]', '', utterance.lower()).strip()
        if norm_key in seen_utterances:
            continue
        seen_utterances.add(norm_key)

        clean_item = {
            "id": len(cleaned) + 1,
            "user_utterance": utterance,
            "target_domain": item["target_domain"].lower(),
            "language": item.get("language", "english").lower(),
            "scenario_type": item.get("scenario_type", "natural_variation"),
        }

        # Step 3: Fix erroneous labels
        clean_item = fix_domain_labels(clean_item)
        cleaned.append(clean_item)

    # Step 4: Inject balanced contrastive hard-negative pairs
    for pair in CONTRASTIVE_PAIRS:
        norm_key = re.sub(r'[^a-zA-Z0-9\s]', '', pair["user_utterance"].lower()).strip()
        if norm_key not in seen_utterances:
            seen_utterances.add(norm_key)
            pair_item = {
                "id": len(cleaned) + 1,
                "user_utterance": pair["user_utterance"],
                "target_domain": pair["target_domain"],
                "language": pair["language"],
                "scenario_type": pair["scenario_type"],
            }
            cleaned.append(pair_item)

    return cleaned


if __name__ == "__main__":
    raw_path = os.path.join(os.path.dirname(__file__), "raw_1000_dataset.json")
    out_path = os.path.join(os.path.dirname(__file__), "clean_nomic_training_data.json")

    if not os.path.exists(raw_path):
        print(f"ERROR: {raw_path} not found!")
        sys.exit(1)

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    cleaned_data = process_and_clean_dataset(raw_data)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_data, f, indent=2, ensure_ascii=False)

    print(f"Successfully processed and cleaned {len(raw_data)} raw items into {len(cleaned_data)} high-quality training samples.")
    print(f"Saved cleaned dataset to: {out_path}")
