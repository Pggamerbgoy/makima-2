import asyncio
import time
from apps.brain.agents.system_agent import SystemAgent

async def run_benchmark():
    agent = SystemAgent()
    print("--- TESTING COLLOQUIAL SYSTEM QUERIES ---")
    queries = [
        "hmm open outlok",
        "na just open outlok",
        "zara chrome kholo",
        "focus vs code"
    ]
    for q in queries:
        t0 = time.perf_counter()
        res = await agent._try_semantic_fastpath(q, "bench_task")
        dt = (time.perf_counter() - t0) * 1000.0
        print(f"Query: '{q}'\n  [TIME] Latency: {dt:.2f}ms | Result: {res}\n")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
