"""Makima v7.1 — CostAnalytics

Tracks per-provider token usage, cost estimates, and provides analytics.
Extends RateLimitManager data with aggregation and reporting.

Spec:
- record_usage(provider, tokens_in, tokens_out, cost_usd, model)
- get_usage_summary(period="today"|"week"|"month"|"all") -> dict
- get_provider_breakdown(period) -> dict per provider
- get_daily_costs(days=30) -> list of daily cost totals
- get_budget_status() -> dict with budget remaining
- WS events: cost_update, budget_warning
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.cost_analytics")


class CostAnalytics:
    def __init__(self, config: dict[str, Any] | None = None, ws_broadcast=None):
        cfg = config or {}
        cost_cfg = cfg.get("cost_analytics", {}) if isinstance(cfg, dict) else {}
        self.ws_broadcast = ws_broadcast
        self.budget_daily_usd = float(cost_cfg.get("budget_daily_usd", 0))
        self.budget_monthly_usd = float(cost_cfg.get("budget_monthly_usd", 0))

        base_path = os.path.expanduser(cost_cfg.get("db_path", "~/.makima/cost_analytics.sqlite"))
        self.db_path = Path(base_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._ensure_db()
        logger.info("CostAnalytics started")

    async def stop(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    async def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT DEFAULT '',
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                task_id TEXT DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_provider ON cost_log(provider)")
        conn.commit()
        self._conn = conn

    async def record_usage(
        self,
        provider: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        model: str = "",
        task_id: str = "",
    ) -> None:
        now = time.time()
        async with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO cost_log(timestamp, provider, model, tokens_in, tokens_out, cost_usd, task_id) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (now, provider, model, tokens_in, tokens_out, cost_usd, task_id),
                )
                self._conn.commit()
            except Exception as e:
                logger.error("record_usage failed: %s", e)
                return

        if self.ws_broadcast:
            from . import ws_protocol
            summary = await self.get_usage_summary("today")
            await self.ws_broadcast(ws_protocol.WSMessage(
                v=ws_protocol.PROTOCOL_VERSION,
                type="cost_update",
                payload={"provider": provider, "cost_usd": cost_usd, "summary": json.loads(summary) if isinstance(summary, str) else summary},
            ))

        if self.budget_daily_usd > 0:
            today_cost = await self._get_period_cost("today")
            if today_cost >= self.budget_daily_usd * 0.9:
                await self._send_budget_warning(today_cost, self.budget_daily_usd, "daily")

    async def get_usage_summary(self, period: str = "today") -> str:
        try:
            total_cost = await self._get_period_cost(period)
            total_in = await self._get_period_tokens(period, "tokens_in")
            total_out = await self._get_period_tokens(period, "tokens_out")
            request_count = await self._get_period_requests(period)

            return json.dumps({
                "period": period,
                "total_cost_usd": round(total_cost, 6),
                "total_tokens_in": total_in,
                "total_tokens_out": total_out,
                "total_requests": request_count,
                "budget_daily_usd": self.budget_daily_usd,
                "budget_monthly_usd": self.budget_monthly_usd,
                "budget_remaining_daily": max(0, self.budget_daily_usd - total_cost) if self.budget_daily_usd > 0 else None,
            }, default=str)
        except Exception as e:
            logger.error("get_usage_summary failed: %s", e)
            return json.dumps({"error": str(e)})

    async def get_provider_breakdown(self, period: str = "today") -> str:
        cutoff = self._period_cutoff(period)
        try:
            def _query():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT provider, SUM(cost_usd) as total_cost, SUM(tokens_in) as total_in, "
                        "SUM(tokens_out) as total_out, COUNT(*) as requests "
                        "FROM cost_log WHERE timestamp >= ? GROUP BY provider ORDER BY total_cost DESC",
                        (cutoff,),
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()

            result = await asyncio.get_event_loop().run_in_executor(None, _query)
            return json.dumps(result, default=str)
        except Exception as e:
            logger.error("get_provider_breakdown failed: %s", e)
            return json.dumps({"error": str(e)})

    async def get_daily_costs(self, days: int = 30) -> str:
        import datetime
        now = datetime.datetime.now()
        results = []
        for i in range(days):
            day_start = (now - datetime.timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + datetime.timedelta(days=1)
            cost = await self._get_range_cost(day_start.timestamp(), day_end.timestamp())
            results.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "cost_usd": round(cost, 6),
            })
        results.reverse()
        return json.dumps(results, default=str)

    async def get_budget_status(self) -> str:
        today_cost = await self._get_period_cost("today")
        month_cost = await self._get_period_cost("month")
        return json.dumps({
            "daily": {
                "spent": round(today_cost, 6),
                "budget": self.budget_daily_usd,
                "remaining": round(max(0, self.budget_daily_usd - today_cost), 6) if self.budget_daily_usd > 0 else None,
                "pct": round((today_cost / self.budget_daily_usd) * 100, 1) if self.budget_daily_usd > 0 else 0,
            },
            "monthly": {
                "spent": round(month_cost, 6),
                "budget": self.budget_monthly_usd,
                "remaining": round(max(0, self.budget_monthly_usd - month_cost), 6) if self.budget_monthly_usd > 0 else None,
                "pct": round((month_cost / self.budget_monthly_usd) * 100, 1) if self.budget_monthly_usd > 0 else 0,
            },
        }, default=str)

    async def _get_period_cost(self, period: str) -> float:
        cutoff = self._period_cutoff(period)
        return await self._get_range_cost(cutoff, time.time())

    async def _get_period_tokens(self, period: str, column: str) -> int:
        cutoff = self._period_cutoff(period)
        try:
            def _query():
                conn = sqlite3.connect(str(self.db_path))
                try:
                    row = conn.execute(
                        f"SELECT COALESCE(SUM({column}), 0) FROM cost_log WHERE timestamp >= ?",
                        (cutoff,),
                    ).fetchone()
                    return row[0] if row else 0
                finally:
                    conn.close()
            return await asyncio.get_event_loop().run_in_executor(None, _query)
        except Exception as e:
            logger.error("_get_period_tokens failed: %s", e)
            return 0

    async def _get_period_requests(self, period: str) -> int:
        cutoff = self._period_cutoff(period)
        try:
            def _query():
                conn = sqlite3.connect(str(self.db_path))
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM cost_log WHERE timestamp >= ?",
                        (cutoff,),
                    ).fetchone()
                    return row[0] if row else 0
                finally:
                    conn.close()
            return await asyncio.get_event_loop().run_in_executor(None, _query)
        except Exception:
            return 0

    async def _get_range_cost(self, start: float, end: float) -> float:
        try:
            def _query():
                conn = sqlite3.connect(str(self.db_path))
                try:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log WHERE timestamp >= ? AND timestamp < ?",
                        (start, end),
                    ).fetchone()
                    return row[0] if row else 0.0
                finally:
                    conn.close()
            return await asyncio.get_event_loop().run_in_executor(None, _query)
        except Exception as e:
            logger.error("_get_range_cost failed: %s", e)
            return 0.0

    def _period_cutoff(self, period: str) -> float:
        import datetime
        now = datetime.datetime.now()
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start = now - datetime.timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "year":
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = datetime.datetime.min
        return start.timestamp()

    async def _send_budget_warning(self, spent: float, budget: float, scope: str) -> None:
        if self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.WSMessage(
                v=ws_protocol.PROTOCOL_VERSION,
                type="budget_warning",
                payload={"spent": spent, "budget": budget, "scope": scope},
            ))
