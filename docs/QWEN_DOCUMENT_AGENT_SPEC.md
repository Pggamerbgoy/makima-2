# 📄 Makima v7.1 — Document Agent: System Specification, Capabilities & Performance Criteria

## 1. Executive Summary & Role in Makima OS
The **Document Agent** (`DocumentAgent`) is an autonomous, high-performance document engineering engine inside the Makima v7.1 AI OS. Unlike generic AI bots that output plain markdown text, the Document Agent is responsible for transforming raw user ideas, notes, or structured data into **publication-grade, visually stunning Excel spreadsheets (.xlsx), Word documents (.docx), and executive PDFs**.

It operates as a specialized leaf agent dispatched by Makima's Commander Agent whenever a user asks to generate, format, analyze, or export structured documents.

---

## 2. Core Abilities & Document Types Supported

### A. High-Aesthetic Spreadsheets (`.xlsx`)
- **Visual Excellence:** Automatic application of curated, modern color palettes (e.g., Deep Slate/Violet header bars, soft zebra-striping on alternating rows).
- **Auto-Formatting & Layout:** Automatically calculates and sets optimal column widths so text is never truncated (`###` or clipped words are strictly banned).
- **Dynamic Excel Formulas:** Generates native Excel formulas (`=SUM()`, `=AVERAGE()`, `=IF()`, `=VLOOKUP()`) rather than static hardcoded numbers.
- **Number & Currency Formatting:** Correctly formats accounting numbers, percentages, dates, and currencies automatically based on context.
- **Multi-Tab Orchestration:** Can build complex multi-sheet workbooks (e.g., "Summary Dashboard" tab + "Raw Data" tab) with cross-sheet formula linking.

### B. Publication-Ready Word Documents (`.docx`)
- **Typography & Hierarchy:** Uses professional typography scaling (Title, Subtitle, Heading 1, Heading 2, Heading 3, Body Text) with consistent line spacing and clean paragraph spacing (no double empty lines).
- **Executive Elements:** Can inject structured visual elements such as **Callout / Alert Boxes**, executive summaries, styled bullet points, and multi-column tables.
- **Styled Tables:** Generates tables with bold styled headers, custom cell padding, and alternating row background shading.
- **Document Metadata & Footers:** Configures clean 1-inch margins and proper document structure.

### C. Executive Reports & PDFs
- **Page-Break Awareness:** Prevents orphan headings at the bottom of pages and ensures clean table page breaks.
- **Visual Polish:** Delivers reports that look like they were designed by a professional graphic designer or management consultant.

---

## 3. Performance Benchmarks ("Kitna Performative Hoga")

### A. Execution Speed & Latency
- **Sub-3 Second Generation:** A 100-row styled spreadsheet or a 5-page executive Word document must be generated, formatted, and written to disk in **under 3.0 seconds** of local CPU execution time.
- **Zero-Blocking I/O:** All filesystem write operations must be non-blocking and execute in standard workspace output directories without hanging the main Makima event loop.

### B. Memory Efficiency & Resource Footprint
- **Low-Memory Streaming:** Must use efficient buffer streams (`openpyxl` / `python-docx` optimized writing) without loading massive redundant objects into memory.
- **Zero Resource Leaks:** Strictly guarantees that all workbook and document file handles are properly closed even if an exception occurs mid-write.

### C. Bulletproof Resilience & Error Recovery
- **Zero-Crash Guarantee:** If a requested custom font or specific style is unavailable on the user's OS (Windows/Linux/macOS), the agent must gracefully degrade to a safe system font without throwing an exception.
- **Self-Correcting Data Validation:** If an LLM-generated table row has mismatched column counts, the agent must automatically pad or sanitize the row before writing to disk.

---

## 4. Fable 5 Structured XML Reasoning Engine
The Document Agent's system prompt must utilize Anthropic Fable 5 / Opus 5 XML-structured instruction tags:
- `<agent_identity>`: Defines the professional document designer persona.
- `<formatting_standards>`: Enforces strict visual rules (never leave raw markdown syntax like `**bold**` inside Excel cells; convert it to actual bold font formatting).
- `<quality_guardrails>`: Guarantees that every generated file is immediately usable, beautifully formatted, and saved to the user's local `output/` directory with an explicit confirmation path.
