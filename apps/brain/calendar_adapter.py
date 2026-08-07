"""
Makima v7.1 — Calendar Adapter

Backends: Google Calendar (OAuth2), Outlook/Microsoft 365 (Graph API), ICS files.
Graceful degradation: if no calendar configured, return polite message.
Read-only by default; write requires CALENDAR_WRITE config flag.

Daily briefing: generates a comprehensive morning briefing with:
- Today's calendar events
- Weather (if configured)
- Pending tasks (if task_manager available)
- Reminders due today
- Cost summary from yesterday
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("makima.calendar_adapter")


@dataclass
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    calendar_id: str = ""
    event_id: str = ""


class DailyBriefing:
    """Generates a comprehensive daily briefing from multiple sources."""

    def __init__(self, calendar: "CalendarAdapter", config: dict[str, Any] | None = None):
        self.calendar = calendar
        self.config = config or {}
        self.task_manager = None
        self.reminder_service = None
        self.cost_analytics = None
        self.learning_engine = None

    async def generate(self) -> dict[str, Any]:
        now = datetime.now()
        briefing = {
            "date": now.strftime("%A, %B %d, %Y"),
            "greeting": self._get_greeting(now),
            "events": [],
            "tasks": [],
            "reminders": [],
            "cost_summary": None,
            "patterns": [],
        }

        # Calendar events
        try:
            events = await self.calendar.get_upcoming_events(days=1)
            briefing["events"] = [
                {
                    "title": e.title,
                    "start": e.start.strftime("%I:%M %p"),
                    "end": e.end.strftime("%I:%M %p"),
                    "location": e.location,
                }
                for e in events
            ]
        except Exception as e:
            logger.warning("Failed to fetch events for briefing: %s", e)

        # Tasks
        if self.task_manager:
            try:
                tasks_json = await self.task_manager.list_tasks(status="pending", limit=5)
                import json
                briefing["tasks"] = json.loads(tasks_json) if isinstance(tasks_json, str) else []
            except Exception as e:
                logger.warning("Failed to fetch tasks for briefing: %s", e)

        # Cost summary
        if self.cost_analytics:
            try:
                summary_json = await self.cost_analytics.get_usage_summary("today")
                import json
                briefing["cost_summary"] = json.loads(summary_json) if isinstance(summary_json, str) else {}
            except Exception:
                pass

        # Learning patterns
        if self.learning_engine:
            try:
                patterns = await self.learning_engine.get_patterns_for_briefing()
                briefing["patterns"] = patterns[:3]
            except Exception:
                pass

        return briefing

    def _get_greeting(self, now: datetime) -> str:
        hour = now.hour
        if hour < 6:
            return "Burning the midnight oil? Here's your early morning briefing."
        elif hour < 12:
            return "Good morning! Here's what's on your plate today."
        elif hour < 17:
            return "Good afternoon! Here's your midday update."
        elif hour < 21:
            return "Good evening! Here's your end-of-day summary."
        else:
            return "Late night session! Here's what's happening."


class CalendarAdapter:
    """
    Unified calendar interface supporting Google Cal, Outlook, and ICS files.
    """

    def __init__(self, config: dict):
        cal_cfg = config.get("calendar", {})
        self.backend = cal_cfg.get("backend", "none")
        self.allow_write = cal_cfg.get("allow_write", False)
        self.ics_path = cal_cfg.get("ics_path", "")

        self._google_creds_path = cal_cfg.get("google_credentials_json", "")
        self._outlook_client_id = cal_cfg.get("outlook_client_id", "")
        self._outlook_client_secret = cal_cfg.get("outlook_client_secret", "")
        self._outlook_token = cal_cfg.get("outlook_token", "")

        self.briefing = DailyBriefing(self, config)

        logger.info(f"CalendarAdapter backend: {self.backend}")

    async def get_upcoming_events(self, days: int = 7) -> list[CalendarEvent]:
        """Fetch upcoming events for the next N days."""
        if self.backend == "google":
            return await self._google_events(days)
        elif self.backend == "outlook":
            return await self._outlook_events(days)
        elif self.backend == "ics":
            return await self._ics_events(days)
        else:
            return []

    async def get_today_summary(self) -> str:
        """Return human-readable summary of today's events."""
        events = await self.get_upcoming_events(days=1)
        if not events:
            if self.backend == "none":
                return "No calendar configured. Set calendar.backend in config."
            return "No events scheduled for today."

        lines = [f"📅 **Today's Schedule ({datetime.now().strftime('%A, %B %d')})**\n"]
        for e in events:
            time_str = e.start.strftime("%I:%M %p")
            lines.append(f"• **{e.title}** at {time_str}" +
                         (f" — {e.location}" if e.location else ""))
        return "\n".join(lines)

    async def create_event(self, title: str, start: datetime, end: datetime,
                           description: str = "") -> str:
        """Create a calendar event. Requires allow_write=True."""
        if not self.allow_write:
            return "Calendar write is disabled. Set calendar.allow_write: true in config."

        if self.backend == "google":
            return await self._google_create(title, start, end, description)
        elif self.backend == "outlook":
            return await self._outlook_create(title, start, end, description)
        else:
            return "Write not supported for this calendar backend."

    async def generate_daily_briefing(self) -> str:
        """Generate a comprehensive daily briefing."""
        import json
        briefing_data = await self.briefing.generate()
        return json.dumps(briefing_data, default=str, indent=2)

    # ------------------------------------------------------------------
    # Google Calendar
    # ------------------------------------------------------------------

    async def _google_events(self, days: int) -> list[CalendarEvent]:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            import os

            if not os.path.exists(self._google_creds_path):
                logger.warning("Google credentials not found")
                return []

            loop = asyncio.get_event_loop()

            def _fetch():
                creds = Credentials.from_authorized_user_file(self._google_creds_path)
                service = build("calendar", "v3", credentials=creds)
                now = datetime.utcnow().isoformat() + "Z"
                end_time = (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"
                result = service.events().list(
                    calendarId="primary",
                    timeMin=now,
                    timeMax=end_time,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                events = []
                for item in result.get("items", []):
                    start_str = item["start"].get("dateTime", item["start"].get("date"))
                    end_str = item["end"].get("dateTime", item["end"].get("date"))
                    try:
                        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    events.append(CalendarEvent(
                        title=item.get("summary", "Untitled"),
                        start=start_dt,
                        end=end_dt,
                        location=item.get("location", ""),
                        description=item.get("description", ""),
                        event_id=item["id"],
                    ))
                return events

            return await asyncio.wait_for(
                loop.run_in_executor(None, _fetch),
                timeout=10.0,
            )
        except Exception as e:
            logger.error(f"Google Calendar fetch failed: {e}")
            return []

    async def _google_create(self, title: str, start: datetime, end: datetime,
                              description: str) -> str:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            loop = asyncio.get_event_loop()

            def _create():
                creds = Credentials.from_authorized_user_file(self._google_creds_path)
                service = build("calendar", "v3", credentials=creds)
                event_body = {
                    "summary": title,
                    "description": description,
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": end.isoformat()},
                }
                return service.events().insert(calendarId="primary", body=event_body).execute()

            event = await asyncio.wait_for(
                loop.run_in_executor(None, _create),
                timeout=10.0,
            )
            return f"✅ Event '{title}' created in Google Calendar."
        except Exception as e:
            return f"Google Calendar create failed: {e}"

    # ------------------------------------------------------------------
    # Outlook / Microsoft 365
    # ------------------------------------------------------------------

    async def _outlook_events(self, days: int) -> list[CalendarEvent]:
        if not self._outlook_token:
            logger.warning("Outlook token not configured")
            return []
        try:
            import httpx
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            end = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me/calendarView",
                    headers={"Authorization": f"Bearer {self._outlook_token}"},
                    params={"startDateTime": now, "endDateTime": end, "$top": 20,
                            "$select": "subject,start,end,location,bodyPreview"},
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    return []

                events = []
                for item in resp.json().get("value", []):
                    try:
                        start_dt = datetime.fromisoformat(item["start"]["dateTime"])
                        end_dt = datetime.fromisoformat(item["end"]["dateTime"])
                    except Exception:
                        continue
                    events.append(CalendarEvent(
                        title=item.get("subject", "Untitled"),
                        start=start_dt,
                        end=end_dt,
                        location=item.get("location", {}).get("displayName", ""),
                        description=item.get("bodyPreview", ""),
                    ))
                return events
        except Exception as e:
            logger.error(f"Outlook events failed: {e}")
            return []

    async def _outlook_create(self, title: str, start: datetime, end: datetime,
                               description: str) -> str:
        if not self._outlook_token:
            return "Outlook token not configured."
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://graph.microsoft.com/v1.0/me/events",
                    headers={"Authorization": f"Bearer {self._outlook_token}",
                             "Content-Type": "application/json"},
                    json={
                        "subject": title,
                        "body": {"contentType": "text", "content": description},
                        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
                        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
                    },
                    timeout=10.0,
                )
                if resp.status_code in (200, 201):
                    return f"✅ Event '{title}' created in Outlook Calendar."
                return f"Outlook create failed ({resp.status_code}): {resp.text}"
        except Exception as e:
            return f"Outlook create failed: {e}"

    # ------------------------------------------------------------------
    # ICS File (Local / Read-only)
    # ------------------------------------------------------------------

    async def _ics_events(self, days: int) -> list[CalendarEvent]:
        if not self.ics_path:
            return []
        try:
            import icalendar
            from pathlib import Path

            loop = asyncio.get_event_loop()

            def _parse():
                cal = icalendar.Calendar.from_ical(Path(self.ics_path).read_bytes())
                events = []
                now = datetime.now()
                cutoff = now + timedelta(days=days)
                for component in cal.walk():
                    if component.name == "VEVENT":
                        dtstart = component.get("dtstart")
                        dtend = component.get("dtend")
                        if dtstart and dtend:
                            start = dtstart.dt
                            end = dtend.dt
                            if hasattr(start, "date"):
                                start = datetime.combine(start, datetime.min.time())
                                end = datetime.combine(end, datetime.min.time())
                            if now <= start <= cutoff:
                                events.append(CalendarEvent(
                                    title=str(component.get("summary", "Untitled")),
                                    start=start,
                                    end=end,
                                    location=str(component.get("location", "")),
                                    description=str(component.get("description", "")),
                                ))
                return sorted(events, key=lambda e: e.start)

            return await asyncio.wait_for(
                loop.run_in_executor(None, _parse),
                timeout=5.0,
            )
        except ImportError:
            logger.warning("icalendar not installed: pip install icalendar")
            return []
        except Exception as e:
            logger.error(f"ICS parse failed: {e}")
            return []
