import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("makima.brain.notification_tools")

# --- Pydantic Models ---
class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str
    priority: int = Field(ge=1, le=5, description="1=Low, 2=Normal, 3=High, 4=Urgent, 5=Critical")
    channel: str = Field(description="Routing channel (e.g., slack, email, sms, webhook)")
    target: str = Field(description="Target identifier (e.g., user_id, channel_id, endpoint)")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    read: bool = False

class DNDRule(BaseModel):
    name: str
    start_time: str = Field(description="Start time in HH:MM format (UTC)")
    end_time: str = Field(description="End time in HH:MM format (UTC)")
    allowed_priorities: List[int] = Field(default_factory=lambda: [4, 5], description="Priorities that bypass DND")

class WorkflowStatus(BaseModel):
    workflow_id: str
    status: str = Field(description="Current state (e.g., running, paused, completed, failed)")
    details: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# --- In-Memory Cache / Store ---
class NotificationStore:
    def __init__(self):
        self.alerts: List[Alert] = []
        self.dnd_rules: List[DNDRule] = []
        self.workflows: Dict[str, WorkflowStatus] = {}
        self.lock = asyncio.Lock()

    def _is_dnd_active(self, priority: int) -> bool:
        now = datetime.utcnow().strftime("%H:%M")
        for rule in self.dnd_rules:
            if rule.start_time <= now <= rule.end_time:
                if priority not in rule.allowed_priorities:
                    return True
        return False

    async def add_alert(self, alert: Alert) -> bool:
        async with self.lock:
            if self._is_dnd_active(alert.priority):
                logger.info(f"Alert suppressed by DND: {alert.message}")
                return False
            self.alerts.append(alert)
            return True

    async def add_dnd_rule(self, rule: DNDRule) -> None:
        async with self.lock:
            self.dnd_rules = [r for r in self.dnd_rules if r.name != rule.name]
            self.dnd_rules.append(rule)

    async def get_filtered_alerts(self, min_priority: int, unread_only: bool) -> List[Alert]:
        async with self.lock:
            res = [a for a in self.alerts if a.priority >= min_priority]
            if unread_only:
                res = [a for a in res if not a.read]
            return res

    async def update_workflow(self, wf: WorkflowStatus) -> None:
        async with self.lock:
            self.workflows[wf.workflow_id] = wf

_store = NotificationStore()

# --- Tool Implementations ---
async def _dispatch_alert(alert: Alert) -> None:
    """Mock dispatcher for routing alerts to external channels."""
    logger.debug(f"Dispatching alert {alert.id} to {alert.channel}:{alert.target}")

async def send_alert(message: str, priority: int, channel: str, target: str) -> Dict[str, Any]:
    try:
        alert = Alert(message=message, priority=priority, channel=channel, target=target)
        success = await _store.add_alert(alert)
        if success:
            await _dispatch_alert(alert)
            logger.info(f"Alert routed to {channel}/{target}: {message}")
            return {"status": "sent", "alert_id": alert.id, "priority": alert.priority}
        return {"status": "suppressed", "reason": "DND active", "priority": priority}
    except ValidationError as ve:
        logger.error(f"Validation error in send_alert: {ve}")
        return {"status": "error", "detail": "Invalid parameters", "errors": ve.errors()}
    except Exception as e:
        logger.error(f"send_alert failed: {e}")
        return {"status": "error", "detail": str(e)}

async def create_dnd_rule(name: str, start_time: str, end_time: str, allowed_priorities: List[int]) -> Dict[str, Any]:
    try:
        rule = DNDRule(name=name, start_time=start_time, end_time=end_time, allowed_priorities=allowed_priorities)
        await _store.add_dnd_rule(rule)
        logger.info(f"DND rule '{name}' created/updated.")
        return {"status": "success", "rule": rule.model_dump()}
    except ValidationError as ve:
        return {"status": "error", "detail": "Invalid DND rule format", "errors": ve.errors()}
    except Exception as e:
        logger.error(f"create_dnd_rule failed: {e}")
        return {"status": "error", "detail": str(e)}

async def filter_notifications(min_priority: int = 1, unread_only: bool = False) -> Dict[str, Any]:
    try:
        alerts = await _store.get_filtered_alerts(min_priority, unread_only)
        return {"status": "success", "count": len(alerts), "alerts": [a.model_dump(mode="json") for a in alerts]}
    except Exception as e:
        logger.error(f"filter_notifications failed: {e}")
        return {"status": "error", "detail": str(e)}

async def broadcast_workflow_status(workflow_id: str, status: str, details: str) -> Dict[str, Any]:
    try:
        wf = WorkflowStatus(workflow_id=workflow_id, status=status, details=details)
        await _store.update_workflow(wf)
        logger.info(f"Workflow {workflow_id} status broadcasted: {status}")
        return {"status": "success", "workflow_id": workflow_id, "updated_at": wf.updated_at.isoformat()}
    except Exception as e:
        logger.error(f"broadcast_workflow_status failed: {e}")
        return {"status": "error", "detail": str(e)}

# --- Registration ---
async def register_notification_tools(registry: Any) -> None:
    try:
        registry.register(
            name="send_alert", func=send_alert,
            description="Sends a prioritized alert to a specific channel/target, automatically respecting active DND rules."
        )
        registry.register(
            name="create_dnd_rule", func=create_dnd_rule,
            description="Creates or updates a Do Not Disturb rule. Define timeframes (HH:MM UTC) and bypass priorities."
        )
        registry.register(
            name="filter_notifications", func=filter_notifications,
            description="Queries the notification store. Filter by minimum priority level (1-5) and unread status."
        )
        registry.register(
            name="broadcast_workflow_status", func=broadcast_workflow_status,
            description="Updates the central state and broadcasts the current status and details of a specific workflow."
        )
        logger.info("Smart Workflow Notifier & Alert Toolset registered successfully.")
    except Exception as e:
        logger.error(f"Critical failure during tool registration: {e}")
