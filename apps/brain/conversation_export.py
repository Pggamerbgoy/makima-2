"""Makima v7.1 — ConversationExport

Export and import conversations in multiple formats:
- JSON (full fidelity, round-trip)
- Markdown (human-readable)
- CSV (spreadsheet-compatible)

Spec:
- export_conversation(conversation_id, format) -> file path or content
- import_conversation(source, format) -> conversation_id
- export_all(format, output_dir) -> list of exported files
- WS events: export_complete, import_complete
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.conversation_export")

VALID_FORMATS = ("json", "markdown", "csv")


class ConversationExport:
    def __init__(self, eternal_memory=None, config: dict[str, Any] | None = None, ws_broadcast=None):
        self.memory = eternal_memory
        self.ws_broadcast = ws_broadcast
        cfg = config or {}
        exp_cfg = cfg.get("export", {}) if isinstance(cfg, dict) else {}
        self.export_dir = Path(os.path.expanduser(exp_cfg.get("export_dir", "~/.makima/exports")))

    async def start(self) -> None:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ConversationExport started")

    async def stop(self) -> None:
        pass

    async def export_conversation(
        self,
        conversation_id: str,
        fmt: str = "json",
    ) -> str:
        if fmt not in VALID_FORMATS:
            return f"Invalid format. Use one of: {VALID_FORMATS}"
        if not self.memory:
            return "Memory module not available."

        messages = await self.memory.get_conversation(conversation_id)
        if not messages:
            return f"No conversation found with id: {conversation_id}"

        if fmt == "json":
            content = self._to_json(messages, conversation_id)
        elif fmt == "markdown":
            content = self._to_markdown(messages, conversation_id)
        elif fmt == "csv":
            content = self._to_csv(messages, conversation_id)
        else:
            return f"Unsupported format: {fmt}"

        # Save to file
        filename = f"conv_{conversation_id}_{int(time.time())}.{fmt if fmt != 'markdown' else 'md'}"
        filepath = self.export_dir / filename
        filepath.write_text(content, encoding="utf-8")

        await self._broadcast("export_complete", {
            "conversation_id": conversation_id,
            "format": fmt,
            "file": str(filepath),
            "message_count": len(messages),
        })

        return json.dumps({
            "status": "exported",
            "file": str(filepath),
            "format": fmt,
            "message_count": len(messages),
        })

    async def export_all(self, fmt: str = "json", output_dir: str | None = None) -> str:
        if not self.memory:
            return "Memory module not available."
        if fmt not in VALID_FORMATS:
            return f"Invalid format. Use one of: {VALID_FORMATS}"

        conversations = await self.memory.list_conversations()
        if not conversations:
            return "No conversations to export."

        out_dir = Path(output_dir) if output_dir else self.export_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        exported = []
        for conv in conversations:
            cid = conv.get("conversation_id")
            if cid:
                result = await self.export_conversation(cid, fmt)
                try:
                    data = json.loads(result)
                    exported.append(data)
                except Exception:
                    exported.append({"conversation_id": cid, "error": result})

        return json.dumps({"exported_count": len(exported), "files": exported}, default=str)

    async def import_conversation(
        self,
        source: str,
        fmt: str | None = None,
    ) -> str:
        source_path = Path(source)
        if not source_path.exists():
            return f"File not found: {source}"

        content = source_path.read_text(encoding="utf-8")
        if not fmt:
            fmt = self._detect_format(source_path)

        if fmt not in VALID_FORMATS:
            return f"Cannot detect format. Specify one of: {VALID_FORMATS}"

        if fmt == "json":
            messages = self._from_json(content)
        elif fmt == "markdown":
            messages = self._from_markdown(content)
        elif fmt == "csv":
            messages = self._from_csv(content)
        else:
            return f"Unsupported format: {fmt}"

        if not messages:
            return "No messages found in import file."

        # Generate new conversation_id and save
        new_conv_id = f"imported-{uuid.uuid4().hex[:10]}"
        for msg in messages:
            if self.memory:
                await self.memory.save_turn(
                    message=msg.get("content", ""),
                    role=msg.get("role", "user"),
                    conversation_id=new_conv_id,
                )

        await self._broadcast("import_complete", {
            "conversation_id": new_conv_id,
            "message_count": len(messages),
            "source_file": source,
        })

        return json.dumps({
            "status": "imported",
            "conversation_id": new_conv_id,
            "message_count": len(messages),
        })

    def _to_json(self, messages: list[dict], conversation_id: str) -> str:
        return json.dumps({
            "conversation_id": conversation_id,
            "exported_at": time.time(),
            "messages": messages,
        }, indent=2, default=str)

    def _to_markdown(self, messages: list[dict], conversation_id: str) -> str:
        lines = [f"# Conversation: {conversation_id}\n"]
        lines.append(f"*Exported at: {time.ctime()}*\n\n---\n")
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", msg.get("message", ""))
            ts = msg.get("created_at")
            timestamp = f" *({time.ctime(ts)})*" if ts else ""
            lines.append(f"### {role}{timestamp}\n\n{content}\n")
        return "\n".join(lines)

    def _to_csv(self, messages: list[dict], conversation_id: str) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["role", "content", "created_at", "conversation_id"])
        for msg in messages:
            writer.writerow([
                msg.get("role", ""),
                msg.get("content", msg.get("message", "")),
                msg.get("created_at", ""),
                conversation_id,
            ])
        return output.getvalue()

    def _from_json(self, content: str) -> list[dict]:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data.get("messages", [])
            elif isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.error("JSON parse failed: %s", e)
            return []

    def _from_markdown(self, content: str) -> list[dict]:
        messages = []
        current_role = None
        current_content = []

        for line in content.split("\n"):
            if line.startswith("### "):
                if current_role and current_content:
                    messages.append({
                        "role": current_role,
                        "content": "\n".join(current_content).strip(),
                    })
                role_part = line[4:].split("*")[0].strip().lower()
                current_role = role_part if role_part in ("user", "assistant", "system") else "user"
                current_content = []
            elif line.startswith("---") or line.startswith("# ") or line.startswith("*Exported"):
                continue
            else:
                current_content.append(line)

        if current_role and current_content:
            messages.append({
                "role": current_role,
                "content": "\n".join(current_content).strip(),
            })
        return messages

    def _from_csv(self, content: str) -> list[dict]:
        reader = csv.DictReader(io.StringIO(content))
        messages = []
        for row in reader:
            if row.get("content"):
                messages.append({
                    "role": row.get("role", "user"),
                    "content": row["content"],
                })
        return messages

    def _detect_format(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".json":
            return "json"
        elif suffix in (".md", ".markdown"):
            return "markdown"
        elif suffix == ".csv":
            return "csv"
        # Try JSON parse
        try:
            json.loads(path.read_text(encoding="utf-8")[:100])
            return "json"
        except Exception:
            pass
        return "json"

    async def _broadcast(self, event_type: str, data: dict) -> None:
        if self.ws_broadcast:
            try:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.WSMessage(
                    v=ws_protocol.PROTOCOL_VERSION,
                    type=event_type,
                    payload=data,
                ))
            except Exception:
                pass
