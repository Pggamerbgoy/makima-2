"""
Makima OS — Retrain Semantic Router with Clean Dataset
Location: scripts/retrain_clean_nomic.py

Loads scripts/clean_nomic_training_data.json, computes normalized embeddings via Ollama nomic-embed-text,
and updates apps/brain/data/semantic_router.pkl.
"""
from __future__ import annotations

import asyncio
import json
import os
import pickle
import sys
import time
from pathlib import Path

import httpx
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
INPUT_PATH = Path(__file__).resolve().parent / "clean_nomic_training_data.json"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "apps" / "brain" / "data" / "semantic_router.pkl"


async def get_embedding(client: httpx.AsyncClient, text: str, retries: int = 3) -> list[float]:
    for attempt in range(retries):
        try:
            resp = await client.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            emb = data.get("embedding")
            if emb:
                return emb
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"Embedding failed for '{text}': {e}")
            await asyncio.sleep(0.2)
    return []


async def retrain():
    print("=" * 80)
    print(" MAKIMA BRAIN — RETRAINING SEMANTIC ROUTER WITH CLEAN DATASET")
    print("=" * 80)

    if not INPUT_PATH.exists():
        print(f"ERROR: {INPUT_PATH} does not exist!")
        return

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        training_samples = json.load(f)

    total = len(training_samples)
    print(f"Loaded {total} clean samples from: {INPUT_PATH}")
    print(f"Connecting to Ollama at {OLLAMA_URL} (Model: {EMBED_MODEL})...\n")

    embeddings: list[list[float]] = []
    labels: list[str] = []
    start_time = time.perf_counter()

    async with httpx.AsyncClient() as client:
        # Check health
        try:
            health = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            if health.status_code != 200:
                print(f"ERROR: Ollama returned status {health.status_code}")
                return
        except Exception as e:
            print(f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}: {e}")
            return

        for i, sample in enumerate(training_samples):
            text = sample["user_utterance"]
            label = sample["target_domain"]
            emb = await get_embedding(client, text)
            
            # Normalize vector
            arr = np.array(emb, dtype=np.float32)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            
            embeddings.append(arr.tolist())
            labels.append(label)

            if (i + 1) % 50 == 0 or (i + 1) == total:
                elapsed = time.perf_counter() - start_time
                rate = (i + 1) / elapsed
                print(f"Processed {i + 1:03d}/{total} embeddings ({rate:.1f} emb/s)...")

    emb_matrix = np.array(embeddings, dtype=np.float32)
    print(f"\nFinal embedding matrix shape: {emb_matrix.shape}")

    payload = {
        "embeddings": emb_matrix,
        "labels": labels,
        "model": EMBED_MODEL,
        "sample_count": total,
        "timestamp": time.time(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "wb") as f:
        pickle.dump(payload, f)

    total_time = time.perf_counter() - start_time
    print(f"Saved {total} embeddings to: {OUTPUT_PATH}")
    print(f"Retraining completed successfully in {total_time:.2f}s!\n")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(retrain())
