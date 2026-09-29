"""
Memory Graph Helper — Structures Causal Nodes and Edges for Ruflo AgentDB.
"""

from __future__ import annotations

from typing import Any, Dict, List


class MemoryGraphHelper:
    """Helper for formatting causal nodes and edges for Ruflo AgentDB."""

    @staticmethod
    def build_causal_edge(source_id: str, target_id: str, relation: str = "caused", weight: float = 1.0) -> Dict[str, Any]:
        """Constructs an AgentDB causal edge payload."""
        return {
            "sourceId": source_id,
            "targetId": target_id,
            "relation": relation,
            "weight": max(0.0, min(1.0, weight)),
        }

    @staticmethod
    def build_audit_node(node_id: str, file_path: str, critical_issues: int, rating: str) -> Dict[str, Any]:
        """Constructs a knowledge graph node payload."""
        return {
            "id": f"audit:{node_id}",
            "type": "audit_record",
            "file": file_path,
            "critical_count": critical_issues,
            "complexity_rating": rating,
        }
