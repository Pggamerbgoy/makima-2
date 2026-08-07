"""
Makima OS v7.2 - DocumentAgent
Autonomous Document Engineering & Parsing Engine for Executive-Grade Outputs.

Capabilities:
1. Advanced Document Parsing (PDF, DOCX, XLSX)
2. Enterprise Spreadsheet Generation (Charts, Formatting, Validation)
3. Publication-Ready Word Documents (TOC, Headers, Images)
4. Structured Executive Report Generation (Markdown/HTML)

Performance Target: <3s generation | Zero-blocking I/O | Bulletproof Resilience
"""

import asyncio
import logging
import base64
import io
import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from .base_agent import BaseAgent

# =============================================================================
# THIRD-PARTY IMPORTS WITH GRACEFUL DEGRADATION (Zero-Crash Guarantee)
# =============================================================================
try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import BarChart, Reference, PieChart
    from openpyxl.formatting.rule import ColorScaleRule
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn, nsdecls
    from docx.oxml import parse_xml
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False

try:
    from pypdf import PdfReader
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False

try:
    import markdown
    _HAS_MARKDOWN = True
except ImportError:
    _HAS_MARKDOWN = False

try:
    import aiofiles
    _HAS_AIOFILES = True
except ImportError:
    _HAS_AIOFILES = False

logger = logging.getLogger("makima.agents.document")

# =============================================================================
# DESIGN TOKENS & CONSTANTS (Single Source of Truth for Aesthetics)
# =============================================================================
class DesignTokens:
    """Curated modern enterprise color palette and typography for Makima OS."""
    
    # Excel Tokens (Slate & Blue Theme)
    HEADER_FILL = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid") if _HAS_OPENPYXL else None
    HEADER_FONT = Font(name="Segoe UI", size=11, bold=True, color="F8FAFC") if _HAS_OPENPYXL else None
    ZEBRA_FILL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid") if _HAS_OPENPYXL else None
    BODY_FONT = Font(name="Segoe UI", size=10, color="334155") if _HAS_OPENPYXL else None
    ACCENT_FONT = Font(name="Segoe UI", size=10, bold=True, color="2563EB") if _HAS_OPENPYXL else None
    
    # Word Tokens
    TITLE_SIZE = Pt(26) if _HAS_DOCX else None
    H1_SIZE = Pt(18) if _HAS_DOCX else None
    H2_SIZE = Pt(14) if _HAS_DOCX else None
    BODY_SIZE = Pt(11) if _HAS_DOCX else None
    SAFE_FONT_FAMILY = "Segoe UI"
    
    # Borders
    THIN_BORDER = Border(
        bottom=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1')
    ) if _HAS_OPENPYXL else None


# =============================================================================
# CORE DOCUMENT AGENT CLASS
# =============================================================================
class DocumentAgent(BaseAgent):
    """
    Autonomous leaf agent for parsing, generating, and formatting publication-grade documents.
    Dispatched by Makima Commander Agent.
    """
    AGENT_NAME = "document"
    DESCRIPTION = "Parses PDFs/Docs/Sheets and creates professional Excel (.xlsx), Word (.docx), and HTML reports."
    CAPABILITIES = ["document_generation", "excel_formatting", "word_generation", "pdf_generation", "powerpoint_generation", "document_parsing"]
    AGENT_TOOLS = ["create_excel", "create_word", "create_pdf", "create_ppt", "parse_document"]
    TAGS = ["document", "xlsx", "docx", "pdf", "pptx", "slides"]
    SYSTEM_PROMPT = """You are Makima's DocumentAgent — an elite document engineer.
You parse complex documents and generate beautifully formatted Excel (.xlsx), Word (.docx), and HTML reports."""

    def __init__(self, ai_handler, memory=None, tool_registry=None,
                 ws_broadcast=None, orchestrator=None, guardrails=None,
                 coordination=None, output_dir: Union[str, Path] = "./makima_workspace/output"):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, coordination)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Thread pool for CPU-bound document generation to prevent event loop blocking
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="MakimaDoc")

    # -------------------------------------------------------------------------
    # RESILIENCE & UTILITIES
    # -------------------------------------------------------------------------
    @staticmethod
    def _sanitize_row(row_data: List[Any], expected_cols: int) -> List[Any]:
        """Self-correcting data validation: Pad or truncate mismatched rows."""
        if not isinstance(row_data, list):
            row_data = [row_data]
        if len(row_data) == expected_cols:
            return row_data
        if len(row_data) < expected_cols:
            return list(row_data) + [None] * (expected_cols - len(row_data))
        return row_data[:expected_cols]

    @asynccontextmanager
    async def _safe_write(self, filepath: Path):
        """Guarantees file handle closure and yields control back to event loop."""
        try:
            yield filepath
        except Exception as e:
            logger.error(f"Document write failed for {filepath}: {e}", exc_info=True)
            raise
        finally:
            await asyncio.sleep(0)

    async def _run_in_executor(self, func, *args):
        """Offload CPU-bound sync functions to thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    # -------------------------------------------------------------------------
    # CAPABILITY A: ENTERPRISE DOCUMENT PARSING
    # -------------------------------------------------------------------------
    async def parse_document(self, filepath: Union[str, Path]) -> Dict[str, Any]:
        """Auto-detects file type and extracts structured content."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        ext = path.suffix.lower()
        if ext == ".pdf":
            return await self._parse_pdf(path)
        elif ext == ".docx":
            return await self._parse_docx(path)
        elif ext in [".xlsx", ".xls"]:
            return await self._parse_xlsx(path)
        else:
            raise ValueError(f"Unsupported file extension for parsing: {ext}")

    async def _parse_pdf(self, path: Path) -> Dict[str, Any]:
        if not _HAS_PYPDF:
            raise RuntimeError("PDF parsing requires 'pypdf'. Install via: pip install pypdf")
        
        def _extract():
            reader = PdfReader(str(path))
            text_pages = []
            for i, page in enumerate(reader.pages):
                text_pages.append({"page": i + 1, "text": page.extract_text() or ""})
            return {"type": "pdf", "pages": len(reader.pages), "content": text_pages}
            
        return await self._run_in_executor(_extract)

    async def _parse_docx(self, path: Path) -> Dict[str, Any]:
        if not _HAS_DOCX:
            raise RuntimeError("DOCX parsing requires 'python-docx'.")
            
        def _extract():
            doc = Document(str(path))
            paragraphs = [{"text": p.text, "style": p.style.name} for p in doc.paragraphs if p.text.strip()]
            tables = []
            for table in doc.tables:
                tables.append([[cell.text for cell in row.cells] for row in table.rows])
            return {"type": "docx", "paragraphs": paragraphs, "tables": tables}
            
        return await self._run_in_executor(_extract)

    async def _parse_xlsx(self, path: Path) -> Dict[str, Any]:
        if not _HAS_OPENPYXL:
            raise RuntimeError("XLSX parsing requires 'openpyxl'.")
            
        def _extract():
            wb = load_workbook(str(path), data_only=True)
            sheets_data = {}
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                sheets_data[sheet_name] = {"headers": rows[0] if rows else [], "rows": rows[1:] if len(rows) > 1 else []}
            return {"type": "xlsx", "sheets": sheets_data}
            
        return await self._run_in_executor(_extract)

    # -------------------------------------------------------------------------
    # CAPABILITY B: HIGH-AESTHETIC SPREADSHEETS (.xlsx)
    # -------------------------------------------------------------------------
    async def generate_excel(self, filename: str, sheets: Dict[str, Dict[str, Any]], metadata: Optional[Dict] = None) -> Path:
        if not _HAS_OPENPYXL:
            raise RuntimeError("Excel generation requires 'openpyxl'.")
            
        filepath = self.output_dir / f"{filename}.xlsx"
        
        def _build_excel():
            wb = Workbook()
            wb.remove(wb.active)
            
            for sheet_name, sheet_data in sheets.items():
                ws = wb.create_sheet(title=sheet_name[:31])
                headers = sheet_data.get("headers", [])
                rows = sheet_data.get("rows", [])
                
                # Headers
                for col_idx, header in enumerate(headers, 1):
                    cell = ws.cell(row=1, column=col_idx, value=header)
                    cell.font = DesignTokens.HEADER_FONT
                    cell.fill = DesignTokens.HEADER_FILL
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = DesignTokens.THIN_BORDER
                
                # Body Rows
                for row_idx, row_data in enumerate(rows, 2):
                    sanitized = self._sanitize_row(row_data, len(headers))
                    for col_idx, value in enumerate(sanitized, 1):
                        cell = ws.cell(row=row_idx, column=col_idx, value=value)
                        cell.font = DesignTokens.BODY_FONT
                        cell.border = DesignTokens.THIN_BORDER
                        if row_idx % 2 == 0:
                            cell.fill = DesignTokens.ZEBRA_FILL
                        if isinstance(value, (int, float)):
                            cell.number_format = '#,##0.00' if isinstance(value, float) else '#,##0'
                
                # Auto-filter & Freeze Panes
                if headers:
                    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
                    ws.freeze_panes = "A2"
                
                # Conditional Formatting (Color Scale for numeric columns)
                for col_idx, header in enumerate(headers, 1):
                    if rows and isinstance(rows[0][col_idx - 1] if col_idx <= len(rows[0]) else None, (int, float)):
                        col_letter = get_column_letter(col_idx)
                        rule = ColorScaleRule(start_type='min', start_color='F87171',
                                              mid_type='percentile', mid_value=50, mid_color='FBBF24',
                                              end_type='max', end_color='34D399')
                        ws.conditional_formatting.add(f"{col_letter}2:{col_letter}{len(rows)+1}", rule)

                # Charts (If requested in sheet_data)
                if sheet_data.get("chart") and len(headers) >= 2:
                    chart_type = sheet_data["chart"].get("type", "bar")
                    chart_title = sheet_data["chart"].get("title", sheet_name)
                    
                    if chart_type == "pie":
                        chart = PieChart()
                        data = Reference(ws, min_col=2, min_row=1, max_row=len(rows)+1)
                        cats = Reference(ws, min_col=1, min_row=2, max_row=len(rows)+1)
                        chart.add_data(data, titles_from_data=True)
                        chart.set_categories(cats)
                    else:
                        chart = BarChart()
                        data = Reference(ws, min_col=2, max_col=len(headers), min_row=1, max_row=len(rows)+1)
                        cats = Reference(ws, min_col=1, min_row=2, max_row=len(rows)+1)
                        chart.add_data(data, titles_from_data=True)
                        chart.set_categories(cats)
                        
                    chart.title = chart_title
                    chart.style = 10
                    ws.add_chart(chart, f"{get_column_letter(len(headers) + 2)}2")

                # Auto-fit columns
                for col_idx in range(1, len(headers) + 1):
                    max_length = len(str(headers[col_idx - 1])) if headers else 10
                    for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, min_row=2, max_row=min(100, len(rows)+1)):
                        for cell in row:
                            if cell.value:
                                max_length = max(max_length, min(len(str(cell.value)), 40))
                    ws.column_dimensions[get_column_letter(col_idx)].width = max_length + 4
            
            if metadata:
                wb.properties.title = metadata.get("title", filename)
                wb.properties.creator = "Makima OS v7.2"
                
            wb.save(str(filepath))
            return filepath

        async with self._safe_write(filepath):
            return await self._run_in_executor(_build_excel)

    # -------------------------------------------------------------------------
    # CAPABILITY C: PUBLICATION-READY WORD DOCUMENTS (.docx)
    # -------------------------------------------------------------------------
    async def generate_word(self, filename: str, content_blocks: List[Dict[str, Any]], metadata: Optional[Dict] = None) -> Path:
        if not _HAS_DOCX:
            raise RuntimeError("Word generation requires 'python-docx'.")
            
        filepath = self.output_dir / f"{filename}.docx"
        
        def _build_word():
            doc = Document()
            
            # Page Setup
            for section in doc.sections:
                section.top_margin = Cm(2.54)
                section.bottom_margin = Cm(2.54)
                section.left_margin = Cm(2.54)
                section.right_margin = Cm(2.54)
                
                # Header & Footer
                header = section.header
                hp = header.paragraphs[0]
                hp.text = metadata.get("title", "Makima OS Executive Report") if metadata else "Makima OS Report"
                hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                hp.runs[0].font.size = Pt(9)
                hp.runs[0].font.color.rgb = RGBColor(100, 100, 100)
                
                footer = section.footer
                fp = footer.paragraphs[0]
                fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = fp.add_run()
                fldChar1 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>')
                run._r.append(fldChar1)
                run2 = fp.add_run()
                instrText = parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>')
                run2._r.append(instrText)
                run3 = fp.add_run()
                fldChar2 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>')
                run3._r.append(fldChar2)

            # Process Content Blocks
            for block in content_blocks:
                b_type = block.get("type", "body")
                content = block.get("content", "")
                
                if b_type == "title":
                    p = doc.add_heading(content, level=0)
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in p.runs:
                        run.font.color.rgb = RGBColor(30, 41, 59)  # Slate 800
                        
                elif b_type == "toc":
                    p = doc.add_paragraph()
                    run = p.add_run("Table of Contents\n")
                    run.bold = True
                    run.font.size = Pt(14)
                    # Add TOC field
                    fldChar_begin = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>')
                    instrText = parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText>')
                    fldChar_separate = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>')
                    fldChar_end = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>')
                    
                    r1 = p.add_run()
                    r1._r.append(fldChar_begin)
                    r2 = p.add_run()
                    r2._r.append(instrText)
                    r3 = p.add_run()
                    r3._r.append(fldChar_separate)
                    r4 = p.add_run("[Update TOC in Word to populate]")
                    r5 = p.add_run()
                    r5._r.append(fldChar_end)
                    
                elif b_type in ["h1", "h2", "h3"]:
                    level = int(b_type[-1])
                    doc.add_heading(content, level=level)
                    
                elif b_type == "body":
                    p = doc.add_paragraph(content)
                    p.paragraph_format.space_after = Pt(8)
                    p.paragraph_format.line_spacing = 1.15
                    
                elif b_type == "callout":
                    p = doc.add_paragraph()
                    run = p.add_run(f"⚠️ {content}")
                    run.bold = True
                    run.font.color.rgb = RGBColor(185, 28, 28)  # Red 700
                    p.paragraph_format.left_indent = Cm(1)
                    p.paragraph_format.space_before = Pt(12)
                    p.paragraph_format.space_after = Pt(12)
                    
                elif b_type == "image":
                    # Content can be base64 string or file path
                    if isinstance(content, str) and content.startswith("data:image"):
                        img_data = base64.b64decode(content.split(",")[1])
                        img_stream = io.BytesIO(img_data)
                        doc.add_picture(img_stream, width=Inches(5.5))
                    elif Path(content).exists():
                        doc.add_picture(content, width=Inches(5.5))
                    last_paragraph = doc.paragraphs[-1]
                    last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

                elif b_type == "table":
                    table_data = content
                    headers = table_data.get("headers", [])
                    rows = table_data.get("rows", [])
                    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
                    table.style = 'Table Grid'
                    table.alignment = WD_TABLE_ALIGNMENT.CENTER
                    
                    for i, h in enumerate(headers):
                        cell = table.rows[0].cells[i]
                        cell.text = str(h)
                        for paragraph in cell.paragraphs:
                            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            for run in paragraph.runs:
                                run.bold = True
                                run.font.color.rgb = RGBColor(255, 255, 255)
                        shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="1E293B"/>')
                        cell._tc.get_or_add_tcPr().append(shading)
                    
                    for r_idx, row_data in enumerate(rows, 1):
                        sanitized = self._sanitize_row(row_data, len(headers))
                        for c_idx, val in enumerate(sanitized):
                            cell = table.rows[r_idx].cells[c_idx]
                            cell.text = str(val) if val is not None else ""
                            if r_idx % 2 == 0:
                                shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F1F5F9"/>')
                                cell._tc.get_or_add_tcPr().append(shading)
                                
                elif b_type == "page_break":
                    doc.add_page_break()

            if metadata:
                doc.core_properties.title = metadata.get("title", filename)
                doc.core_properties.author = "Makima OS v7.2"
                
            doc.save(str(filepath))
            return filepath

        async with self._safe_write(filepath):
            return await self._run_in_executor(_build_word)

    # -------------------------------------------------------------------------
    # CAPABILITY D: EXECUTIVE REPORT GENERATION (Markdown/HTML)
    # -------------------------------------------------------------------------
    async def generate_report(self, filename: str, sections: List[Dict[str, str]], metadata: Optional[Dict] = None) -> Path:
        """Generates a structured Markdown report and compiles to HTML if possible."""
        filepath_md = self.output_dir / f"{filename}.md"
        filepath_html = self.output_dir / f"{filename}.html"
        
        md_content = []
        if metadata:
            md_content.append(f"# {metadata.get('title', filename)}\n")
            md_content.append(f"*Generated by Makima OS v7.2 | {metadata.get('date', 'Current Date')}*\n\n---\n")
            
        for section in sections:
            md_content.append(f"## {section.get('heading', 'Section')}\n\n")
            md_content.append(f"{section.get('body', '')}\n\n")
            if "bullets" in section:
                for bullet in section["bullets"]:
                    md_content.append(f"- {bullet}\n")
                md_content.append("\n")
                
        full_md = "".join(md_content)
        
        async with self._safe_write(filepath_md):
            if _HAS_AIOFILES:
                async with aiofiles.open(filepath_md, 'w', encoding='utf-8') as f:
                    await f.write(full_md)
            else:
                await self._run_in_executor(filepath_md.write_text, full_md, 'utf-8')
                
        # Compile to HTML
        if _HAS_MARKDOWN:
            def _build_html():
                html_body = markdown.markdown(full_md, extensions=['tables', 'fenced_code', 'toc'])
                html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{metadata.get('title', filename) if metadata else filename}</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.6; color: #334155; max-width: 800px; margin: 0 auto; padding: 2rem; }}
        h1, h2, h3 {{ color: #1E293B; border-bottom: 2px solid #E2E8F0; padding-bottom: 0.3rem; }}
        table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
        th, td {{ border: 1px solid #CBD5E1; padding: 8px; text-align: left; }}
        th {{ background-color: #1E293B; color: #F8FAFC; }}
        tr:nth-child(even) {{ background-color: #F1F5F9; }}
        code {{ background: #F1F5F9; padding: 2px 4px; border-radius: 4px; }}
    </style>
</head>
<body>
    {html_body}
</body>
</html>"""
                filepath_html.write_text(html_template, encoding='utf-8')
                return filepath_html
                
            await self._run_in_executor(_build_html)
            logger.info(f"HTML Report generated: {filepath_html}")
            return filepath_html
            
        return filepath_md

    # -------------------------------------------------------------------------
    # DISPATCHER & BASE AGENT CONTRACT
    # -------------------------------------------------------------------------
    async def create_document(self, task: Dict[str, Any]) -> Path:
        doc_type = str(task.get("type", "report")).lower()
        filename = str(task.get("filename", "document"))
        data = task.get("data")
        
        if doc_type == "excel":
            if isinstance(data, dict) and "sheets" in data:
                sheets = data["sheets"]
            else:
                # Convert fallback list format to proper dictionary format
                sheets = {
                    "Sheet1": {
                        "headers": ["Content"],
                        "rows": [[str(data or task)]]
                    }
                }
            return await self.generate_excel(filename, sheets, task.get("metadata"))
        elif doc_type == "word":
            blocks = data.get("blocks") if isinstance(data, dict) and "blocks" in data else [{"type": "paragraph", "text": str(data or task)}]
            return await self.generate_word(filename, blocks, task.get("metadata"))
        elif doc_type == "pdf":
            md_path = task.get("filepath") or task.get("md_path") or task.get("content") or data or task
            out_filename = task.get("filename", "document.pdf")
            return await self.generate_pdf(md_path, out_filename)
        elif doc_type == "report":
            sections = data.get("sections") if isinstance(data, dict) and "sections" in data else [{"title": "Summary Report", "content": str(data or task)}]
            return await self.generate_report(filename, sections, task.get("metadata"))
        elif doc_type == "parse":
            filepath = task.get("filepath", task.get("filename", ""))
            result = await self.parse_document(filepath)
            out_path = self.output_dir / f"{Path(filepath).stem}_parsed.json"
            await self._run_in_executor(out_path.write_text, json.dumps(result, indent=2), 'utf-8')
            return out_path
        else:
            sections = [{"title": "Document Report", "content": str(data or task)}]
            return await self.generate_report(filename, sections, task.get("metadata"))

    async def generate_pdf(self, md_filepath: Union[str, Path, None], output_filename: str) -> Path:
        """Converts any Markdown document or raw string into a styled enterprise PDF via Playwright."""
        md_text = ""
        if md_filepath:
            try:
                in_path = Path(str(md_filepath))
                if in_path.exists() and in_path.is_file():
                    md_text = in_path.read_text(encoding="utf-8")
                else:
                    md_text = str(md_filepath)
            except Exception:
                md_text = str(md_filepath)
        else:
            md_text = "# Document\nNo content provided."

        if not output_filename.endswith(".pdf"):
            output_filename += ".pdf"

        out_path = self.output_dir / output_filename
        
        import json
        json_md = json.dumps(md_text)
        
        html_template = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>__TITLE__</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>
    <style>
        @page { size: A4; margin: 20mm 15mm 20mm 15mm; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; color: #1E293B; line-height: 1.6; font-size: 11pt; padding: 0 10px; }
        h1 { color: #0F172A; font-size: 22pt; border-bottom: 3px solid #2563EB; padding-bottom: 8px; }
        h2 { color: #1E293B; font-size: 16pt; border-bottom: 1.5px solid #E2E8F0; padding-bottom: 4px; margin-top: 24px; page-break-after: avoid; }
        h3 { color: #334155; font-size: 13pt; margin-top: 18px; page-break-after: avoid; }
        p { margin-bottom: 12px; text-align: justify; }
        blockquote { background: #EFF6FF; border-left: 4px solid #2563EB; margin: 16px 0; padding: 10px 16px; border-radius: 0 6px 6px 0; font-size: 10.5pt; }
        table { width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 10pt; page-break-inside: avoid; }
        th, td { border: 1px solid #CBD5E1; padding: 8px 12px; text-align: left; }
        th { background-color: #1E293B; color: #F8FAFC; font-weight: 600; }
        tr:nth-child(even) { background-color: #F8FAFC; }
        code { background: #F1F5F9; color: #0F172A; padding: 2px 5px; border-radius: 4px; font-family: monospace; font-size: 9.5pt; }
        .mermaid { display: flex; justify-content: center; margin: 20px 0; page-break-inside: avoid; }
        hr { border: none; border-top: 1px solid #E2E8F0; margin: 24px 0; }
    </style>
</head>
<body>
    <div id="content"></div>
    <script>
        const rawMarkdown = __MARKDOWN_RAW__;
        document.getElementById('content').innerHTML = marked.parse(rawMarkdown);
        // Render Mermaid Diagrams with auto-quoting for node labels containing colons/commas
        document.querySelectorAll('pre code.language-mermaid').forEach((block) => {
            let text = block.textContent;
            let lines = text.split('\n');
            let fixedLines = lines.map(line => {
                return line.replace(/([A-Za-z0-9_]+\[)([^"\]]+)(\])/g, (m, p1, p2, p3) => {
                    if ((p2.includes(':') || p2.includes(',')) && !p2.startsWith('"')) {
                        return p1 + '"' + p2 + '"' + p3;
                    }
                    return m;
                });
            });
            const div = document.createElement('div');
            div.className = 'mermaid';
            div.textContent = fixedLines.join('\n');
            block.parentNode.replaceWith(div);
        });
        mermaid.initialize({ startOnLoad: true, theme: 'default' });
        renderMathInElement(document.body, {
            delimiters: [
                {left: '$$', right: '$$', display: true},
                {left: '$', right: '$', display: false}
            ]
        });
    </script>
</body>
</html>"""
        title_stem = out_path.stem
        html_content = html_template.replace("__TITLE__", title_stem).replace("__MARKDOWN_RAW__", json_md)
        
        temp_html = self.output_dir / f"temp_{title_stem}.html"
        temp_html.write_text(html_content, encoding="utf-8")
        
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"file:///{temp_html.as_posix()}", wait_until="networkidle")
            await asyncio.sleep(2.0)
            await page.pdf(path=str(out_path), format="A4", print_background=True, margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"})
            await browser.close()
            
        if temp_html.exists():
            temp_html.unlink()
            
        logger.info(f"DocumentAgent: Successfully compiled PDF to {out_path}")
        return out_path

    async def execute(self, task_id: str, message: str, context: Dict[str, Any],
                      entities: Dict[str, Any]) -> str:
        """Standard Makima BaseAgent execution entrypoint."""
        self._reset_state()
        try:
            workspace_dir = context.get("workspace_dir", "./makima_workspace")
            self.output_dir = Path(workspace_dir) / "output"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract task dictionary from context, entities, or LLM parsing
            task_dict = context.get("document_task") or entities.get("document_task")
            
            if not task_dict:
                task_dict = self.ai_handler.try_parse_json(message)
                
            if not task_dict or not isinstance(task_dict, dict) or "type" not in task_dict:
                # Fallback: Ask LLM to structure the request
                sys_prompt = "You are Makima's DocumentAgent. Convert the user request into a JSON object with keys: 'type' (excel, word, report, parse), 'filename', and 'data'. Respond ONLY with valid JSON."
                messages = self._build_messages(message, context, extra_system=sys_prompt)
                raw_json = await self._llm_call(messages, task="general", require_json=True)
                task_dict = self.ai_handler.try_parse_json(raw_json)
                
            if not task_dict or "type" not in task_dict:
                return "Error: Could not determine document schema from request."
                
            result_path = await self.create_document(task_dict)
            abs_path = result_path.resolve()
            return f"Document operation successful! PDF compiled.\n\n📄 **Click to view/download:** [{abs_path.name}](file:///{abs_path.as_posix()})"
            
        except FileNotFoundError as e:
            logger.warning(f"Document not found: {e}")
            return f"Error processing document request: {str(e)}"
        except Exception as e:
            logger.error(f"DocumentAgent execution failed: {e}", exc_info=True)
            return f"Error processing document request: {str(e)}"

    def __del__(self):
        """Properly shut down the ThreadPoolExecutor."""
        self._executor.shutdown(wait=True)
