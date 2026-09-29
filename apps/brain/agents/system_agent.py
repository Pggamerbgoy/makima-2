"""
Makima OS — SystemAgent Compatibility Shim.
Re-exports is_critical_process and provides SystemAgent shim for benchmark tests.
"""
from __future__ import annotations

from typing import Any
from apps.brain.tools.system_tools import is_critical_process
from .base_agent import BaseAgent


class SystemAgent(BaseAgent):
    """Compatibility SystemAgent."""
    AGENT_NAME: str = "system_agent"


__all__ = ["SystemAgent", "is_critical_process"]
