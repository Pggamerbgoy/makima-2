# 🤖 Qwen 3.7 Max Self-Contained Prompt for Makima's `document_agent.py`

*Copy the ENTIRE text below inside the dashed box and paste it into Web-Based Qwen 3.7 Max:*

---

```text
You are an expert Python systems architect. We are building a multi-agent desktop AI assistant called "Makima v7.1" (Python backend + Tauri Rust shell).
You do not have access to our local codebase, so here is the complete architectural context you need to write a fully compatible leaf agent.

### 1. The BaseAgent Interface in Makima
Every agent in Makima inherits from `BaseAgent` (`from .base_agent import BaseAgent`).
Here is how `BaseAgent` works and what methods/attributes it provides:

```python
class BaseAgent(ABC):
    AGENT_NAME: str = "base"
    DESCRIPTION: str = "Base agent"
    SYSTEM_PROMPT: str = "You are a helpful AI assistant."

    def __init__(self, ai_handler, memory=None, tool_registry=None, ws_broadcast=None, ...):
        self.ai_handler = ai_handler # Provides LLM calls
        self.memory = memory         # Provides SQLite memory context
        self.tool_registry = tool_registry
        ...

    def _reset_state(self):
        """Must be called at the beginning of execute()."""

    def _build_messages(self, message: str, context: dict[str, Any], extra_system: str = "") -> list[dict[str, str]]:
        """Builds standardized chat history messages from user message and context."""

    async def _llm_call(self, messages: list[dict[str, str]], task: str = "general", require_json: bool = False) -> str:
        """Asynchronously calls Makima's configured LLM (Opus/Qwen/etc.) and returns text."""
```

### 2. Your Task
Please write the complete, production-ready code for `apps/brain/agents/document_agent.py`.
This agent (`DocumentAgent(BaseAgent)`) is responsible for generating professional, beautifully formatted documents in the user's workspace:
1. **Spreadsheets (`.xlsx`)** using `openpyxl`: Should create tables with styled header rows, auto-adjust column widths, bold titles, and basic formulas if numbers are present.
2. **Word Documents (`.docx`)** using `python-docx`: Should create structured documents with Title, Subtitle, proper Headings (H1/H2), bulleted lists, and tables.
3. **Markdown / Text Reports (`.md` / `.txt`)**: Standard clean markdown export.

### 3. Requirements for `DocumentAgent`:
- Must define `AGENT_NAME = "document"` and `DESCRIPTION = "Creates professional Excel spreadsheets (.xlsx), Word docs (.docx), and structured reports"`.
- Must define an XML-tagged `SYSTEM_PROMPT` in the Anthropic Fable 5 style (`<system_instructions>`, `<document_rules>`, `<formatting_standards>`).
- Must implement:
  `async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:`
- Inside `execute()`, first call `self._reset_state()`.
- Use `self._build_messages(message, context)` and `await self._llm_call(...)` to determine:
  (a) Document format requested (`xlsx`, `docx`, `md`).
  (b) The structured content/rows/headings to include in the document.
- Save the generated document to a dedicated output folder: `output_dir = Path(context.get("workspace_dir", ".")) / "output"`. Create the directory if it does not exist (`output_dir.mkdir(parents=True, exist_ok=True)`).
- Ensure all file operations are wrapped in `try...except` blocks and return a human-readable success message with the absolute file path (e.g., `"Successfully created Excel spreadsheet at: C:/.../output/report.xlsx"`).

Please provide only the complete Python code for `document_agent.py` with necessary imports and docstrings.
```
