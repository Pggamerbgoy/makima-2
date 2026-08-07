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

async def register_calendar_tools(registry: Any) -> None:
    """Registers all calendar tools into the Makima OS tool registry."""
    try:
        registry.add_tool(
            name="schedule_meeting", coroutine=schedule_meeting,
            description="Schedule a new meeting with attendees and location, checking for conflicts.",
            parameters={"type": "object", "properties": {
                "title": {"type": "string"}, "start_time": {"type": "string", "description": "ISO8601 datetime"},
                "end_time": {"type": "string", "description": "ISO8601 datetime"}, "attendees": {"type": "array", "items": {"type": "string"}},
                "location": {"type": "string"}}, "required": ["title", "start_time", "end_time", "attendees", "location"]}
        )
        registry.add_tool(
            name="check_schedule_conflicts", coroutine=check_schedule_conflicts,
            description="Check for existing schedule conflicts within a given time range.",
            parameters={"type": "object", "properties": {
                "start_time": {"type": "string"}, "end_time": {"type": "string"}}, "required": ["start_time", "end_time"]}
        )
        registry.add_tool(
            name="find_available_slots", coroutine=find_available_slots,
            description="Find available time slots on a specific date within working hours.",
            parameters={"type": "object", "properties": {
                "date_str": {"type": "string", "description": "YYYY-MM-DD"}, "duration_minutes": {"type": "integer"},
                "working_hours": {"type": "array", "items": {"type": "integer"}, "description": "[start_hour, end_hour]"}}, 
                "required": ["date_str", "duration_minutes", "working_hours"]}
        )
        registry.add_tool(
            name="add_time_block", coroutine=add_time_block,
            description="Add a dedicated time block (e.g., deep work) to the calendar.",
            parameters={"type": "object", "properties": {
                "title": {"type": "string"}, "date_str": {"type": "string"}, "start_hour": {"type": "integer"},
                "duration_hours": {"type": "number"}}, "required": ["title", "date_str", "start_hour", "duration_hours"]}
        )
        logger.info("Successfully registered 4 advanced calendar tools.")
    except Exception as e:
        logger.error(f"Failed to register calendar tools: {e}")
