"""
Test suite for OpenAI Agents SDK Bridge in Makima OS.
Verifies MakimaModel, MakimaModelProvider, ToolRegistry bridge, and Phase 1 & 2 SDK Agent conversions:
  - SecurityAgent
  - DevOpsAgent
  - DataAnalystAgent
  - AutomationAgent
  - CreativeAgent
"""
import asyncio
import json
import pytest

from apps.brain.agents.security_agent import SecurityAgent
from apps.brain.agents.devops_agent import DevOpsAgent
from apps.brain.agents.data_analyst_agent import DataAnalystAgent
from apps.brain.agents.automation_agent import AutomationAgent
from apps.brain.agents.creative_agent import CreativeAgent
from apps.brain.agents.system_agent import SystemAgent
from apps.brain.agents.research_agent import ResearchAgent
from apps.brain.agents.memory_agent import MemoryAgent
from apps.brain.agents.messaging_agent import MessagingAgent
from apps.brain.agents.code_agent import CodeAgent
from apps.brain.agents.document_agent import DocumentAgent
from apps.brain.agents.elite_ecosystem import EcosystemAgent
from apps.brain.agents.browser_agent import BrowserAgent
from apps.brain.ai_handler import LLMResponse
from apps.brain.core.sdk_bridge import MakimaModel, MakimaModelProvider, to_sdk_function_tool, to_sdk_tools
from apps.brain.tool_registry import ToolRegistry
from agents import Agent, Runner


class MockGateway:
    def __init__(self, mode="security"):
        self.mode = mode
        self.call_history = []
        self.turn = 0

    async def generate(self, messages, task="general", **kwargs):
        self.call_history.append({"messages": messages, "task": task, "kwargs": kwargs})
        self.turn += 1

        has_tool_result = any(m.get("role") == "tool" for m in messages)

        if self.mode == "security":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_port_1",
                        "name": "scan_ports",
                        "arguments": {"target": "127.0.0.1", "ports": [80, 443]},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=60,
                )
            else:
                return LLMResponse(
                    text="Security scan complete: Port 80 and 443 are filtered on 127.0.0.1.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=110,
                )

        elif self.mode == "devops":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_docker_1",
                        "name": "docker_ps",
                        "arguments": {},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=50,
                )
            else:
                return LLMResponse(
                    text="Docker Containers:\n- redis:alpine (Running)\n- makima-brain:latest (Running)",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=120,
                )

        elif self.mode == "data_analyst":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_profile_1",
                        "name": "profile_dataset",
                        "arguments": {"file_path": "sample.csv"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=50,
                )
            else:
                return LLMResponse(
                    text="Dataset Analysis:\nRows: 100\nColumns: 4\nNo missing values found.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=130,
                )

        elif self.mode == "automation":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_remind_1",
                        "name": "set_reminder",
                        "arguments": {"text": "Morning meeting", "delay_seconds": 3600},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=50,
                )
            else:
                return LLMResponse(
                    text="Reminder set successfully for Morning meeting.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=100,
                )

        elif self.mode == "creative":
            return LLMResponse(
                text="The gentle rain descends upon the leaves,\nA rhythmic dance the summer sky conceives.",
                tool_calls=[],
                backend="mock_dashscope",
                model="qwen-flash",
                total_tokens=70,
            )

        elif self.mode == "system":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_launch_1",
                        "name": "launch_app",
                        "arguments": {"app_path": "notepad"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=50,
                )
            else:
                return LLMResponse(
                    text="Notepad opened successfully.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=90,
                )

        elif self.mode == "research":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_search_1",
                        "name": "web_search",
                        "arguments": {"query": "AI news 2025"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=60,
                )
            else:
                return LLMResponse(
                    text="Latest AI developments in 2025 indicate major advancements in agent architectures.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=140,
                )

        elif self.mode == "memory":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_mem_1",
                        "name": "memory_search",
                        "arguments": {"query": "user info"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=50,
                )
            else:
                return LLMResponse(
                    text="Memory recall: You are working on Makima autonomous agent integration.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=110,
                )

        elif self.mode == "messaging":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_msg_1",
                        "name": "messaging_register_draft",
                        "arguments": {
                            "task_id": "msg_001",
                            "platform": "whatsapp",
                            "contact_id": "c_alex",
                            "contact_name": "Alex",
                            "raw_text": "Happy Birthday!",
                        },
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=60,
                )
            else:
                return LLMResponse(
                    text="Draft registered: Birthday wish to Alex on WhatsApp.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=120,
                )

        elif self.mode == "code":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_lint_1",
                        "name": "lint_code",
                        "arguments": {"code": "def fib(n):\n    return n if n <= 1 else fib(n-1) + fib(n-2)"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=60,
                )
            else:
                return LLMResponse(
                    text="```python\ndef fib(n: int) -> int:\n    return n if n <= 1 else fib(n-1) + fib(n-2)\n```\nCode validated and ready.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=150,
                )

        elif self.mode == "document":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_doc_1",
                        "name": "create_word",
                        "arguments": {"filename": "report.docx", "title": "Project Report"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=60,
                )
            else:
                return LLMResponse(
                    text="Word Document generated successfully: report.docx",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=100,
                )

        elif self.mode == "ecosystem":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_dispatch_1",
                        "name": "subtask_dispatch",
                        "arguments": {"agent_name": "system", "task_description": "Get system stats"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=60,
                )
            else:
                return LLMResponse(
                    text="Ecosystem plan executed: System stats gathered and synthesized.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=120,
                )

        elif self.mode == "browser":
            if not has_tool_result:
                return LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_browse_1",
                        "name": "browser_navigate",
                        "arguments": {"url": "https://www.youtube.com/results?search_query=Arijit+Singh"},
                    }],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=70,
                )
            else:
                return LLMResponse(
                    text="Navigated to YouTube and located Arijit Singh tracks.",
                    tool_calls=[],
                    backend="mock_dashscope",
                    model="qwen-flash",
                    total_tokens=110,
                )

        return LLMResponse(
            text="Operation processed.",
            tool_calls=[],
            backend="mock_dashscope",
            model="qwen-flash",
            total_tokens=30,
        )


@pytest.mark.asyncio
async def test_sdk_bridge_makima_model_and_provider():
    gateway = MockGateway("security")
    provider = MakimaModelProvider(gateway, default_task="security")
    model = provider.get_model("security")
    assert isinstance(model, MakimaModel)
    assert model.task == "security"


@pytest.mark.asyncio
async def test_tool_registry_sdk_tools():
    tr = ToolRegistry()
    
    async def sample_handler(target: str = "127.0.0.1") -> str:
        return f"Scanned {target}"

    tr.register_tool(
        name="custom_scanner",
        description="Sample scanner",
        func=sample_handler,
        schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
    )

    sdk_tool = tr.to_sdk_function_tool("custom_scanner")
    assert sdk_tool.name == "custom_scanner"
    assert sdk_tool.description == "Sample scanner"


@pytest.mark.asyncio
async def test_security_agent_sdk_execution():
    gateway = MockGateway("security")
    tr = ToolRegistry()
    sec_agent = SecurityAgent(ai_handler=gateway, tool_registry=tr)

    result = await sec_agent.execute(
        task_id="sec_test_001",
        message="Localhost ke open ports scan karo",
        context={},
        entities={},
    )

    assert "Security scan complete" in result or "filtered" in result or "80" in result
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_devops_agent_sdk_execution():
    gateway = MockGateway("devops")
    tr = ToolRegistry()
    devops_agent = DevOpsAgent(ai_handler=gateway, tool_registry=tr)

    result = await devops_agent.execute(
        task_id="devops_test_001",
        message="Docker containers list karo",
        context={},
        entities={},
    )

    assert "Docker Containers" in result or "redis" in result or "makima-brain" in result
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_data_analyst_agent_sdk_execution():
    gateway = MockGateway("data_analyst")
    tr = ToolRegistry()
    data_agent = DataAnalystAgent(ai_handler=gateway, tool_registry=tr)

    result = await data_agent.execute(
        task_id="data_test_001",
        message="Ek simple CSV ka analysis karo",
        context={},
        entities={},
    )

    assert "Dataset Analysis" in result or "Rows" in result
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_automation_agent_sdk_execution():
    gateway = MockGateway("automation")
    tr = ToolRegistry()
    auto_agent = AutomationAgent(ai_handler=gateway, tool_registry=tr)

    result = await auto_agent.execute(
        task_id="auto_test_001",
        message="Kal 8 baje reminder set karo",
        context={},
        entities={},
    )

    assert "Reminder set successfully" in result or "meeting" in result
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_creative_agent_sdk_execution():
    gateway = MockGateway("creative")
    tr = ToolRegistry()
    creative_agent = CreativeAgent(ai_handler=gateway, tool_registry=tr)

    result = await creative_agent.execute(
        task_id="creative_test_001",
        message="Ek short poem likho rain pe",
        context={},
        entities={},
    )

    assert "rain" in result.lower() or "leaves" in result.lower()
    assert gateway.turn >= 1


@pytest.mark.asyncio
async def test_system_agent_sdk_execution():
    gateway = MockGateway("system")
    tr = ToolRegistry()
    sys_agent = SystemAgent(ai_handler=gateway, tool_registry=tr)

    result = await sys_agent.execute(
        task_id="sys_test_001",
        message="Notepad kholo desktop pe",
        context={},
        entities={},
    )

    assert "Notepad" in result or "opened" in result or "success" in result.lower()
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_research_agent_sdk_execution():
    gateway = MockGateway("research")
    tr = ToolRegistry()
    res_agent = ResearchAgent(ai_handler=gateway, tool_registry=tr)

    result = await res_agent.execute(
        task_id="res_test_001",
        message="Comprehensive report on AI news 2025",
        context={"agent_task": type("AT", (), {"parameters": {"depth": "deep"}})()},
        entities={},
    )

    assert "AI" in result or "2025" in result or "agent" in result.lower()


@pytest.mark.asyncio
async def test_memory_agent_sdk_execution():
    gateway = MockGateway("memory")
    tr = ToolRegistry()
    mem_agent = MemoryAgent(ai_handler=gateway, tool_registry=tr)

    result = await mem_agent.execute(
        task_id="mem_test_001",
        message="Mujhe kya pata hai",
        context={},
        entities={},
    )

    assert "Makima" in result or "recall" in result.lower() or "Memory" in result


@pytest.mark.asyncio
async def test_messaging_agent_sdk_execution():
    gateway = MockGateway("messaging")
    tr = ToolRegistry()
    msg_agent = MessagingAgent(ai_handler=gateway, tool_registry=tr)

    result = await msg_agent.execute(
        task_id="msg_test_001",
        message="Alex ko WhatsApp pe Birthday wish draft karo",
        context={},
        entities={},
    )

    assert "Draft" in result or "Birthday" in result or "Alex" in result or "WhatsApp" in result


@pytest.mark.asyncio
async def test_code_agent_sdk_execution():
    gateway = MockGateway("code")
    tr = ToolRegistry()
    code_agent = CodeAgent(ai_handler=gateway, tool_registry=tr)

    result = await code_agent.execute(
        task_id="code_test_001",
        message="Fibonacci function Python mein likho",
        context={},
        entities={},
    )

    assert "fib" in result or "def " in result


@pytest.mark.asyncio
async def test_document_agent_sdk_execution():
    gateway = MockGateway("document")
    tr = ToolRegistry()
    doc_agent = DocumentAgent(ai_handler=gateway, tool_registry=tr)

    result = await doc_agent.execute(
        task_id="doc_test_001",
        message="Word doc banao project report ka",
        context={},
        entities={},
    )

    assert "Word" in result or "report" in result or "docx" in result.lower()
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_ecosystem_agent_sdk_execution():
    gateway = MockGateway("ecosystem")
    tr = ToolRegistry()
    eco_agent = EcosystemAgent(ai_handler=gateway, tool_registry=tr)

    result = await eco_agent.execute(
        task_id="eco_test_001",
        message="Complex multi-step task execute karo",
        context={},
        entities={},
    )

    assert "Ecosystem" in result or "System" in result or "synthesized" in result.lower()
    assert gateway.turn >= 2


@pytest.mark.asyncio
async def test_browser_agent_sdk_execution():
    gateway = MockGateway("browser")
    tr = ToolRegistry()
    browser_agent = BrowserAgent(ai_handler=gateway, tool_registry=tr)

    result = await browser_agent.execute(
        task_id="browser_test_001",
        message="YouTube pe Arijit Singh dhundho",
        context={},
        entities={},
    )

    assert "YouTube" in result or "Arijit" in result or "Navigated" in result
