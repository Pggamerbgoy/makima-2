import asyncio
import logging
import uuid
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional

# Zero-crash resilience: graceful fallback for date parsing
try:
    from dateutil import parser as date_parser
except ImportError:
    date_parser = None

logger = logging.getLogger("makima.tools.calendar")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)

class CalendarStore:
    """High-performance in-memory calendar store with async locking."""
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def add_event(self, event: Dict[str, Any]) -> None:
        async with self._lock:
            self.events.append(event)

    async def get_overlaps(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        async with self._lock:
            return [e for e in self.events if e["start"] < end and e["end"] > start]

    async def get_events_on_date(self, target_date: datetime) -> List[Dict[str, Any]]:
        async with self._lock:
            return [e for e in self.events if e["start"].date() == target_date.date()]

_store = CalendarStore()

def _parse_dt(dt_str: str) -> datetime:
    """Robust datetime parser with fallback."""
    if date_parser:
        return date_parser.parse(dt_str).replace(tzinfo=None)
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).replace(tzinfo=None)

async def schedule_meeting(title: str, start_time: str, end_time: str, attendees: List[str], location: str) -> Dict[str, Any]:
    """Schedule a new meeting, checking for conflicts automatically."""
    try:
        start, end = _parse_dt(start_time), _parse_dt(end_time)
        if end <= start:
            return {"status": "error", "message": "End time must be strictly after start time."}
        
        conflicts = await _store.get_overlaps(start, end)
        if conflicts:
            return {"status": "conflict", "message": "Schedule conflict detected.", "conflicts": [c["title"] for c in conflicts]}
            
        event = {"id": str(uuid.uuid4()), "title": title, "start": start, "end": end, 
                 "attendees": attendees, "location": location, "type": "meeting"}
        await _store.add_event(event)
        logger.info(f"Scheduled meeting: {title} ({start} to {end})")
        return {"status": "success", "event_id": event["id"], "title": title}
    except Exception as e:
        logger.error(f"schedule_meeting failed: {e}")
        return {"status": "error", "message": f"Failed to schedule meeting: {str(e)}"}

async def check_schedule_conflicts(start_time: str, end_time: str) -> Dict[str, Any]:
    """Check for existing schedule conflicts within a given time range."""
    try:
        start, end = _parse_dt(start_time), _parse_dt(end_time)
        conflicts = await _store.get_overlaps(start, end)
        return {
            "status": "success", "has_conflicts": len(conflicts) > 0,
            "conflicts": [{"title": c["title"], "start": c["start"].isoformat(), "end": c["end"].isoformat()} for c in conflicts]
        }
    except Exception as e:
        logger.error(f"check_schedule_conflicts failed: {e}")
        return {"status": "error", "message": f"Failed to check conflicts: {str(e)}"}

async def find_available_slots(date_str: str, duration_minutes: int, working_hours: List[int]) -> Dict[str, Any]:
    """Find available time slots on a specific date within working hours."""
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_hour = working_hours[0] if working_hours else 9
        end_hour = working_hours[1] if len(working_hours) > 1 else 17
        
        day_start = datetime.combine(target_date, time(hour=start_hour))
        day_end = datetime.combine(target_date, time(hour=end_hour))
        duration = timedelta(minutes=duration_minutes)
        
        events = await _store.get_events_on_date(datetime.combine(target_date, time()))
        events.sort(key=lambda x: x["start"])
        
        slots, current = [], day_start
        for event in events:
            if event["start"] > current:
                while current + duration <= event["start"] and current + duration <= day_end:
                    slots.append(current.isoformat())
                    current += timedelta(minutes=15)
            current = max(current, event["end"])
            
        while current + duration <= day_end:
            slots.append(current.isoformat())
            current += timedelta(minutes=15)
            
        return {"status": "success", "date": date_str, "available_slots": slots}
    except Exception as e:
        logger.error(f"find_available_slots failed: {e}")
        return {"status": "error", "message": f"Failed to find slots: {str(e)}"}

async def add_time_block(title: str, date_str: str, start_hour: int, duration_hours: float) -> Dict[str, Any]:
    """Add a dedicated time block (e.g., deep work) to the calendar."""
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime.combine(target_date, time(hour=start_hour))
        end = start + timedelta(hours=duration_hours)
        
        conflicts = await _store.get_overlaps(start, end)
        if conflicts:
            return {"status": "conflict", "message": "Time block conflicts with existing events.", "conflicts": [c["title"] for c in conflicts]}
            
        event = {"id": str(uuid.uuid4()), "title": title, "start": start, "end": end, 
                 "attendees": [], "location": "Personal", "type": "time_block"}
        await _store.add_event(event)
        logger.info(f"Added time block: {title} on {date_str}")
        return {"status": "success", "event_id": event["id"], "title": title}
    except Exception as e:
        logger.error(f"add_time_block failed: {e}")
        return {"status": "error", "message": f"Failed to add time block: {str(e)}"}

def register_calendar_tools(registry: Any) -> None:
    """Registers all calendar tools into the Makima OS tool registry."""
    try:
        tools = [
            {
                "name": "schedule_meeting",
                "func": schedule_meeting,
                "description": "Call this tool EXCLUSIVELY when asked to schedule a new meeting. It requires attendees and location, and will automatically check for conflicts.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Meeting title or subject"},
                        "start_time": {"type": "string", "description": "Start datetime in ISO8601 format (e.g. 2026-08-21T14:00:00)"},
                        "end_time": {"type": "string", "description": "End datetime in ISO8601 format (e.g. 2026-08-21T15:00:00)"},
                        "attendees": {"type": "array", "items": {"type": "string"}, "description": "List of attendee email addresses or names"},
                        "location": {"type": "string", "description": "Meeting room or virtual link"},
                    },
                    "required": ["title", "start_time", "end_time", "attendees", "location"],
                },
                "category": "calendar",
            },
            {
                "name": "check_schedule_conflicts",
                "func": check_schedule_conflicts,
                "description": "Call this tool EXCLUSIVELY to check if the user has any existing calendar conflicts within a given time range.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "start_time": {"type": "string", "description": "Start datetime in ISO8601 format"},
                        "end_time": {"type": "string", "description": "End datetime in ISO8601 format"},
                    },
                    "required": ["start_time", "end_time"],
                },
                "category": "calendar",
            },
            {
                "name": "find_available_slots",
                "func": find_available_slots,
                "description": "Call this tool EXCLUSIVELY to find available free time slots on a specific date within the user's working hours.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "date_str": {"type": "string", "description": "Target date in YYYY-MM-DD format"},
                        "duration_minutes": {"type": "integer", "description": "Required slot duration in minutes", "default": 30},
                        "working_hours": {"type": "array", "items": {"type": "integer"}, "description": "Start and end hour range in 24h format, e.g. [9, 17]"},
                    },
                    "required": ["date_str", "duration_minutes", "working_hours"],
                },
                "category": "calendar",
            },
            {
                "name": "add_time_block",
                "func": add_time_block,
                "description": "Call this tool EXCLUSIVELY when the user asks to block out dedicated focus or deep work time on their calendar.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title or label for the time block (e.g. 'Deep Work')"},
                        "date_str": {"type": "string", "description": "Target date in YYYY-MM-DD format"},
                        "start_hour": {"type": "integer", "description": "Start hour in 24h format (0-23)"},
                        "duration_hours": {"type": "number", "description": "Duration in hours (e.g. 1.5)"},
                    },
                    "required": ["title", "date_str", "start_hour", "duration_hours"],
                },
                "category": "calendar",
            },
        ]
        for t in tools:
            if hasattr(registry, "register"):
                registry.register(
                    name=t["name"],
                    func=t["func"],
                    description=t["description"],
                    schema=t["schema"],
                    category=t["category"],
                )
            elif hasattr(registry, "add_tool"):
                registry.add_tool(
                    name=t["name"],
                    func=t["func"],
                    description=t["description"],
                    schema=t["schema"],
                    category=t["category"],
                )
            else:
                registry[t["name"]] = t["func"]
        logger.info("Successfully registered 4 advanced calendar tools.")
    except Exception as e:
        logger.error(f"Failed to register calendar tools: {e}")
