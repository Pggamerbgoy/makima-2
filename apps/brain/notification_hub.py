"""
Makima v7.1 — Notification Hub

gRPC bridge to the C++ Windows notification service.
Outbound: send Windows toast notifications (title, body, actions).
Inbound: read/monitor notifications from the OS notification center.
Falls back to plyer (cross-platform) if native service unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("makima.notification_hub")


class NotificationHub:
    """
    Sends and reads Windows notifications.
    Primary: gRPC to native notify service.
    Fallback: plyer.notification (software toast).
    """

    def __init__(self, config: dict, notify_service=None, ws_broadcast=None):
        self.config = config.get("notifications", {})
        self.notify_service = notify_service
        self.ws_broadcast = ws_broadcast
        self.app_name = self.config.get("app_name", "Makima")
        self.enabled = self.config.get("enabled", True)

    async def send_toast(
        self,
        title: str,
        body: str,
        actions: Optional[list[dict]] = None,
        duration: str = "short",  # "short" = 5s, "long" = 25s
    ) -> bool:
        """
        Send a Windows toast notification.
        actions: [{"id": "action_id", "label": "Button Label"}, ...]
        """
        if not self.enabled:
            return False

        # Try native gRPC service first
        if self.notify_service:
            try:
                result = await asyncio.wait_for(
                    self.notify_service.send_toast(
                        title=title,
                        body=body,
                        actions=actions or [],
                        duration=duration,
                    ),
                    timeout=5.0,
                )
                logger.info(f"Toast sent via native service: '{title}'")
                return result.get("success", False)
            except Exception as e:
                logger.warning(f"Native notify failed, falling back: {e}")

        # Fallback: plyer
        return await self._send_plyer_toast(title, body)

    async def _send_plyer_toast(self, title: str, body: str) -> bool:
        """Fallback notification via plyer."""
        try:
            loop = asyncio.get_event_loop()

            def _notify():
                from plyer import notification
                notification.notify(
                    title=title,
                    message=body,
                    app_name=self.app_name,
                    timeout=5,
                )

            await loop.run_in_executor(None, _notify)
            return True
        except ImportError:
            logger.warning("plyer not installed. Install with: pip install plyer")
            # Final fallback: log to console
            print(f"\n🔔 [{self.app_name}] {title}\n{body}\n")
            return True
        except Exception as e:
            logger.error(f"Plyer notification failed: {e}")
            return False

    async def send_ai_response_notification(self, task_id: str, summary: str) -> None:
        """Notify user that Makima finished a background task."""
        await self.send_toast(
            title="Makima — Task Complete",
            body=summary[:128],
            actions=[{"id": f"view_{task_id}", "label": "View Result"}],
        )

    async def send_reminder(self, title: str, body: str) -> None:
        """Send a calendar/user-set reminder notification."""
        await self.send_toast(
            title=f"⏰ {title}",
            body=body,
            duration="long",
        )

    async def send_budget_warning(self, used_usd: float, budget_usd: float) -> None:
        """Notify when LLM cost budget is approaching limit."""
        pct = int((used_usd / budget_usd) * 100) if budget_usd > 0 else 100
        await self.send_toast(
            title="Makima — Cost Warning",
            body=f"API cost at {pct}% of budget (${used_usd:.2f} / ${budget_usd:.2f})",
        )
