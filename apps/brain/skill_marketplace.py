"""Makima v7.1 — SkillMarketplace

Local plugin/skill registry for discovering, installing, and managing
WASM skills and Python plugins. Provides a local marketplace interface
(not a real remote store — skills are local files/URLs).

Spec:
- list_skills() -> list of available skills
- install_skill(source) -> install from local path or URL
- uninstall_skill(name) -> remove installed skill
- enable_skill(name) / disable_skill(name)
- get_skill_info(name) -> details
- WS events: skill_installed, skill_uninstalled, skill_enabled, skill_disabled
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.skill_marketplace")


@dataclass
class Skill:
    name: str
    description: str
    version: str
    author: str
    source: str
    enabled: bool = True
    installed: bool = True
    tags: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "source": self.source,
            "enabled": self.enabled,
            "installed": self.installed,
            "tags": self.tags,
            "config": self.config,
        }


class SkillMarketplace:
    def __init__(self, config: dict[str, Any] | None = None, ws_broadcast=None):
        cfg = config or {}
        mp_cfg = cfg.get("marketplace", {}) if isinstance(cfg, dict) else {}
        self.skills_dir = Path(os.path.expanduser(mp_cfg.get("skills_dir", "~/.makima/skills")))
        self.config_dir = Path(os.path.expanduser(mp_cfg.get("config_dir", "~/.makima/skill_config")))
        self.ws_broadcast = ws_broadcast
        self._skills: dict[str, Skill] = {}

    async def start(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        await self._scan_local_skills()
        logger.info("SkillMarketplace started with %d skills", len(self._skills))

    async def stop(self) -> None:
        pass

    async def _scan_local_skills(self) -> None:
        self._skills.clear()
        for item in self.skills_dir.iterdir():
            if item.is_dir():
                manifest = item / "manifest.json"
                if manifest.exists():
                    try:
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                        skill = Skill(
                            name=data.get("name", item.name),
                            description=data.get("description", ""),
                            version=data.get("version", "0.1.0"),
                            author=data.get("author", "unknown"),
                            source=str(item),
                            enabled=data.get("enabled", True),
                            tags=data.get("tags", []),
                        )
                        self._skills[skill.name] = skill
                    except Exception as e:
                        logger.warning("Failed to load skill manifest %s: %s", manifest, e)
            elif item.suffix in (".wasm", ".py"):
                skill = Skill(
                    name=item.stem,
                    description=f"Auto-discovered skill: {item.name}",
                    version="0.1.0",
                    author="unknown",
                    source=str(item),
                )
                self._skills[skill.name] = skill

    async def list_skills(self, include_disabled: bool = True) -> str:
        skills = []
        for skill in self._skills.values():
            if include_disabled or skill.enabled:
                skills.append(skill.as_dict())
        return json.dumps(skills, default=str)

    async def get_skill_info(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill:
            return json.dumps({"error": f"Skill '{name}' not found"})
        return json.dumps(skill.as_dict(), default=str)

    async def install_skill(self, source: str, name: str | None = None) -> str:
        source_path = Path(source)
        if source_path.exists():
            return await self._install_local(source_path, name)
        elif source.startswith(("http://", "https://")):
            return await self._install_from_url(source, name)
        else:
            return f"Invalid source: {source}"

    async def _install_local(self, source: Path, name: str | None = None) -> str:
        skill_name = name or source.stem
        dest = self.skills_dir / skill_name

        if dest.exists():
            return f"Skill '{skill_name}' already installed. Use update or uninstall first."

        try:
            if source.is_dir():
                shutil.copytree(source, dest)
            else:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest / source.name)

            # Create manifest if not present
            manifest = dest / "manifest.json"
            if not manifest.exists():
                manifest_data = {
                    "name": skill_name,
                    "description": f"Installed from {source}",
                    "version": "0.1.0",
                    "author": "local",
                    "source": str(source),
                    "enabled": True,
                }
                manifest.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            skill = Skill(
                name=manifest_data.get("name", skill_name),
                description=manifest_data.get("description", f"Installed from {source}"),
                version=manifest_data.get("version", "0.1.0"),
                author=manifest_data.get("author", "local"),
                source=str(dest),
            )
            self._skills[skill_name] = skill
            await self._broadcast("skill_installed", skill.as_dict())
            return f"Skill '{skill_name}' installed successfully."
        except Exception as e:
            logger.error("install_skill failed: %s", e)
            return f"Error installing skill: {e}"

    async def _install_from_url(self, url: str, name: str | None = None) -> str:
        try:
            import urllib.request
            skill_name = name or url.split("/")[-1].split(".")[0]
            dest = self.skills_dir / skill_name
            dest.mkdir(parents=True, exist_ok=True)

            filename = url.split("/")[-1]
            filepath = dest / filename
            urllib.request.urlretrieve(url, filepath)

            manifest = dest / "manifest.json"
            if not manifest.exists():
                manifest_data = {
                    "name": skill_name,
                    "description": f"Installed from {url}",
                    "version": "0.1.0",
                    "author": "remote",
                    "source": url,
                    "enabled": True,
                }
                manifest.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

            skill = Skill(
                name=skill_name,
                description=f"Installed from {url}",
                version="0.1.0",
                author="remote",
                source=str(dest),
            )
            self._skills[skill_name] = skill
            await self._broadcast("skill_installed", skill.as_dict())
            return f"Skill '{skill_name}' installed from URL."
        except Exception as e:
            logger.error("install from URL failed: %s", e)
            return f"Error installing from URL: {e}"

    async def uninstall_skill(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill:
            return f"Skill '{name}' not found."
        try:
            skill_path = Path(skill.source)
            if skill_path.is_dir():
                shutil.rmtree(skill_path)
            elif skill_path.exists():
                skill_path.unlink()
            del self._skills[name]
            await self._broadcast("skill_uninstalled", {"name": name})
            return f"Skill '{name}' uninstalled."
        except Exception as e:
            logger.error("uninstall_skill failed: %s", e)
            return f"Error uninstalling: {e}"

    async def enable_skill(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill:
            return f"Skill '{name}' not found."
        skill.enabled = True
        await self._update_manifest(skill)
        await self._broadcast("skill_enabled", skill.as_dict())
        return f"Skill '{name}' enabled."

    async def disable_skill(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill:
            return f"Skill '{name}' not found."
        skill.enabled = False
        await self._update_manifest(skill)
        await self._broadcast("skill_disabled", skill.as_dict())
        return f"Skill '{name}' disabled."

    async def _update_manifest(self, skill: Skill) -> None:
        manifest_path = Path(skill.source) / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                data["enabled"] = skill.enabled
                manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass

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
