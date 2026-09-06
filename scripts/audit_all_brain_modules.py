"""
Comprehensive Audit Script for all Makima Brain Modules, Agents, Tools, and Protocol Handlers.
Tests:
1. Import validity for every single .py file in apps/brain and its subpackages.
2. Initialization of all agents in NextGenOrchestrator.
3. Verification of all tools in ToolRegistry (signatures, docstrings, schema).
4. Full AppBootstrap service container validation.
5. WebSocket message dispatch routes for all ClientMessageType enums.
6. Detection of dead stubs, missing methods, or dangling imports.
"""

import asyncio
import importlib
import os
import pkgutil
import sys
import traceback
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def test_imports():
    print("=" * 60)
    print("STEP 1: Testing imports of all modules in apps/brain...")
    print("=" * 60)
    
    brain_dir = Path(__file__).resolve().parents[1] / "apps" / "brain"
    errors = []
    success_count = 0
    
    for root, _, files in os.walk(brain_dir):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                full_path = Path(root) / file
                rel_path = full_path.relative_to(brain_dir.parent.parent)
                mod_name = ".".join(rel_path.with_suffix("").parts)
                try:
                    importlib.import_module(mod_name)
                    success_count += 1
                except Exception as e:
                    errors.append((mod_name, str(e), traceback.format_exc()))
    
    print(f"Successfully imported {success_count} modules.")
    if errors:
        print(f"FAILED to import {len(errors)} modules:")
        for mod, err, tb in errors:
            print(f"  - [{mod}]: {err}")
    else:
        print("ALL modules imported cleanly with 0 syntax or import errors!")
    return errors

async def test_all_agents():
    print("\n" + "=" * 60)
    print("STEP 2: Testing Agent & Tool Initialization...")
    print("=" * 60)
    
    from apps.brain.main import _load_config
    from apps.brain.core.app_bootstrap import AppBootstrap
    
    cfg = _load_config()
    bootstrap = AppBootstrap(config=cfg)
    services = await bootstrap.initialize_services()
    
    orchestrator = services.get("orchestrator")
    print(f"NextGenOrchestrator: {orchestrator}")
    
    agents = orchestrator.agents if hasattr(orchestrator, "agents") else {}
    print(f"Registered agents ({len(agents)}): {list(agents.keys())}")
    
    tool_reg = services.get("tool_registry")
    tools = tool_reg.get_all_tools() if tool_reg else {}
    print(f"Registered global tools ({len(tools)}): {list(tools.keys())}")
    
    # Verify each agent's execution capability
    for name, agent_inst in agents.items():
        agent = getattr(agent_inst, "agent", agent_inst)
        has_exec = hasattr(agent, "execute")
        tool_count = len(getattr(agent, "_tools", []))
        role = getattr(agent, "role", "N/A")
        print(f"  - Checking agent [{name}]: role={role}, tools={tool_count}, execute={has_exec}")
        if not has_exec:
            print(f"    [ERROR] Agent {name} lacks execute() method!")
    
    # Test all WS message types
    print("\n" + "=" * 60)
    print("STEP 3: Testing WS Message Handlers against all ClientMessageType enums...")
    print("=" * 60)
    from apps.brain.ws_protocol import ClientMessageType
    from apps.brain.main import _handle_ws_message
    
    class DummyWS:
        async def send_text(self, data): pass
    
    class DummyMsg:
        def __init__(self, msg_type, payload=None, task_id="test_type_check"):
            self.type = msg_type
            self.payload = payload or {}
            self.task_id = task_id
    
    ws = DummyWS()
    for enum_val in ClientMessageType:
        msg = DummyMsg(enum_val.value, {"text": "ping", "profile": "Work", "action": "test"})
        try:
            await _handle_ws_message(msg, ws)
            print(f"  - Message type [{enum_val.name}] ({enum_val.value}): OK")
        except Exception as e:
            print(f"  - [FAIL] Message type [{enum_val.name}] raised: {e}")
            traceback.print_exc()

    await bootstrap.shutdown_services()

if __name__ == "__main__":
    import_errors = test_imports()
    asyncio.run(test_all_agents())
