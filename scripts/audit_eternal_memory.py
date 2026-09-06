"""
EternalMemory Deep Audit Script:
1. Exact record count in memory database
2. Recent 10 entries with timestamp, role, and message content
3. Analysis of what is stored (conversation history vs facts/traits)
4. Live hybrid search tests: "Makima architecture" and "cleanup"
5. File sizes of memory databases in ~/.makima/
"""

import asyncio
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from apps.brain.eternal_memory import EternalMemory


def get_db_paths():
    makima_dir = Path.home() / ".makima"
    return {
        "memory_sqlite": makima_dir / "memory.sqlite",
        "memory_db": makima_dir / "memory.db",
        "learning_db": makima_dir / "learning.db",
        "vector_idx": makima_dir / "memory.vectors.npy",
    }


def audit_sqlite():
    paths = get_db_paths()
    print("=" * 70)
    print("🧠 ETERNAL MEMORY STORAGE AUDIT")
    print("=" * 70)

    # 1. Check existing files & sizes
    print("\n📁 DATABASE FILES & SIZES:")
    for name, path in paths.items():
        if path.exists():
            size_kb = path.stat().st_size / 1024.0
            print(f"  - {name} ({path}): {size_kb:.2f} KB ({path.stat().st_size} bytes)")
        else:
            print(f"  - {name} ({path}): [NOT FOUND]")

    # Find the active database file
    active_db = None
    if paths["memory_sqlite"].exists():
        active_db = paths["memory_sqlite"]
    elif paths["memory_db"].exists():
        active_db = paths["memory_db"]

    if not active_db:
        print("\n❌ No active memory SQLite database found.")
        return

    print(f"\n🔍 INSPECTING ACTIVE DB: {active_db}")
    with sqlite3.connect(str(active_db)) as conn:
        conn.row_factory = sqlite3.Row

        # Tables
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"  Tables found: {tables}")

        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  - Table '{table}': {count} total rows")

        # Check conversation table specifically
        if "conversation" in tables:
            total_conv = conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0]
            print(f"\n📊 TOTAL CONVERSATION RECORDS: {total_conv}")

            roles_breakdown = conn.execute("SELECT role, COUNT(*) FROM conversation GROUP BY role").fetchall()
            print("  Role breakdown:")
            for r, c in roles_breakdown:
                print(f"    * {r}: {c} rows")

            # Recent 10 entries
            print("\n🕒 RECENT 10 ENTRIES IN MEMORY:")
            recent_rows = conn.execute(
                "SELECT id, conversation_id, created_at, role, message FROM conversation ORDER BY id DESC LIMIT 10"
            ).fetchall()

            for r in recent_rows:
                ts = r["created_at"]
                try:
                    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt = str(ts)
                msg_preview = (r["message"] or "").replace("\n", " ")[:90]
                print(f"  [{r['id']}] {dt} | Role: {r['role']} | Conv: {r['conversation_id']} | Msg: {msg_preview}")


async def audit_search():
    print("\n" + "=" * 70)
    print("🔎 LIVE MEMORY HYBRID SEARCH TESTS")
    print("=" * 70)

    paths = get_db_paths()
    active_db = paths["memory_sqlite"] if paths["memory_sqlite"].exists() else paths["memory_db"]

    mem = EternalMemory(db_path=active_db)
    await mem.start()

    queries = ["Makima architecture", "cleanup"]

    for q in queries:
        print(f"\n>>> Search Query: \"{q}\"")
        hits = await mem.search(q, k=5)
        if not hits:
            print("  [0 hits found]")
        else:
            print(f"  Found {len(hits)} matching records:")
            for idx, h in enumerate(hits, 1):
                msg_text = h.get("message", "") if isinstance(h, dict) else getattr(h, "message", "")
                msg_preview = (msg_text or "").replace("\n", " ")[:120]
                c_score = h.get("composite_score", 0.0) if isinstance(h, dict) else getattr(h, "composite_score", 0.0)
                cos_s = h.get("cosine_sim", 0.0) if isinstance(h, dict) else getattr(h, "cosine_sim", 0.0)
                bm_s = h.get("bm25_score", 0.0) if isinstance(h, dict) else getattr(h, "bm25_score", 0.0)
                role_s = h.get("role", "") if isinstance(h, dict) else getattr(h, "role", "")
                print(f"    {idx}. Score: {c_score:.3f} (Cos: {cos_s:.3f}, BM25: {bm_s:.3f}) | Role: {role_s} | Text: {msg_preview}")

    await mem.stop()


if __name__ == "__main__":
    audit_sqlite()
    asyncio.run(audit_search())
