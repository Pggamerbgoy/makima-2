"""
Makima OS v8.3 — Elite DocumentAgent
Autonomous Document Engineering & Parsing Engine for Executive-Grade Outputs.

Capabilities:
1. Multi-Theme Palette Engine (Midnight Slate, Emerald Finance, Crimson Executive, Nordic Frost)
2. Smart Excel Generation (.xlsx with live formulas =SUM/=AVERAGE, KPI cards, auto-filter, double-bottom borders)
3. Publication-Ready Word Documents (.docx with Executive Cover Pages, auto-updating TOC, tables, callouts)
4. Enterprise PDF Compilation (.pdf via Playwright / Offline-safe HTML renderer)
5. Executive PowerPoint Presentations (.pptx slide decks with 16:9 widescreen layout)
6. Structured Executive Report Generation (Markdown / HTML)
7. Universal Document Parsing (PDF, DOCX, XLSX, CSV, TXT, MD, JSON, PPTX)
8. Cross-Format Document Conversion Engine (CSV ↔ XLSX ↔ DOCX ↔ PDF ↔ HTML ↔ MD)
9. Ephemeral/Temp File Management & Safe Document Deletion (delete_document, cleanup_temp_documents)

Zero-blocking I/O | Bulletproof Resilience | Strict Schema Validation
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .base_agent import BaseAgent, agent_tool, TOOL_DISCIPLINE_BLOCK
from ..core.known_folders import resolve_known_folder

# =============================================================================
# THIRD-PARTY IMPORTS WITH GRACEFUL DEGRADATION (Zero-Crash Guarantee)
# =============================================================================
try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.chart import BarChart, PieChart, Reference
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    from docx.shared import Cm, Inches, Pt, RGBColor
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False

try:
    from pypdf import PdfReader
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor as PptxRGBColor
    from pptx.util import Inches as PptxInches, Pt as PptxPt
    _HAS_PPTX = True
except ImportError:
    _HAS_PPTX = False

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


# Canonical themes, palettes, and HTML converters imported directly from document_tools (zero duplication)
from ..tools.document_tools import (
    ThemePalette,
    THEMES,
    get_theme_palette,
    _escape_html,
    _format_inline,
    _simple_markdown_to_html,
)

# =============================================================================
class DocumentAgent(BaseAgent):
    """
    Autonomous leaf agent for parsing, generating, formatting, converting, and managing publication-grade documents.
    Dispatched by Makima Commander Agent.
    """
    AGENT_NAME = "document"
    DESCRIPTION = "Parses PDFs/Docs/Sheets/CSV and creates professional Excel (.xlsx), Word (.docx), PDF, PPTX, and HTML reports."
    CAPABILITIES = [
        "document_generation", "excel_formatting", "word_generation",
        "pdf_generation", "powerpoint_generation", "document_parsing",
        "document_conversion", "smart_formula_generation",
        "document_deletion", "temp_cleanup",
    ]
    AGENT_TOOLS = [
        "create_excel",
        "create_word",
        "create_pdf",
        "create_ppt",
        "parse_document",
        "create_document",
        "convert_document",
        "delete_document",
        "cleanup_temp_documents",
        "generate_excel",
        "generate_word",
        "generate_pdf",
        "generate_report",
    ]
    TAGS = ["document", "xlsx", "docx", "pdf", "pptx", "slides", "excel", "word", "sheets", "converter", "cleanup"]
    SYSTEM_PROMPT = """You are Makima's DocumentAgent — an elite document engineer and technical publisher.
You parse complex files, create publication-grade Excel (.xlsx), Word (.docx), PDF, and PowerPoint (.pptx) documents, and manage executive document workflows.

CORE CAPABILITIES & TOOLS:
- Excel (.xlsx): create_excel(filename, sheets=[...], title="...") with auto-column width, styled headers, and numeric formatting.
- Word (.docx): create_word(filename, sections=[...], title="...") with structured typography, headings, tables, and callouts.
- PDF (.pdf): create_pdf(filename, content="...", title="...") with professional layouts and headers/footers.
- PowerPoint (.pptx): create_ppt(filename, slides=[...], theme="...") with clean slide decks and bullet hierarchy.
- Parsing & Conversion: parse_document(filepath), convert_document(source, target_format), cleanup_temp_documents().

DESIGN & DESTINATION RULES:
1. Structured Thinking: Always plan the document hierarchy, theme palette (slate, emerald, crimson, nordic), and section breakdown inside a <thinking>...</thinking> block before invoking generation tools.
2. Styling Standards: Always apply readable color palettes, clean borders, bold header rows, and appropriate data formatting.
3. File Destinations: Save to the user-specified folder (e.g. desktop, downloads, documents) or default workspace output directory. Always report the full resolved path.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    def __init__(
        self,
        ai_handler: Any = None,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Any = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        coordination: Any = None,
        output_dir: Union[str, Path] = "./makima_workspace/output",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            ai_handler, memory, tool_registry, ws_broadcast,
            orchestrator, guardrails, coordination, **kwargs,
        )
        self.output_dir = Path(output_dir)
        self._temp_dir = self.output_dir / "temp"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        self._last_generated_document: Optional[Path] = None
        # Thread pool for CPU-bound document generation to prevent event loop blocking
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="MakimaDoc")

        self._TOOL_MAP: dict[str, Any] = {
            "create_excel": self._tool_create_excel,
            "create_word": self._tool_create_word,
            "create_pdf": self._tool_create_pdf,
            "create_ppt": self._tool_create_ppt,
            "parse_document": self._tool_parse_document,
            "create_document": self._tool_create_document,
            "convert_document": self._tool_convert_document,
            "delete_document": self._tool_delete_document,
            "cleanup_temp_documents": self._tool_cleanup_temp_documents,
            "create_spreadsheet": self._tool_create_spreadsheet,
            "generate_excel": self._tool_create_excel,
            "generate_word": self._tool_create_word,
            "generate_pdf": self._tool_create_pdf,
            "generate_report": self._tool_create_report,
        }

    # -------------------------------------------------------------------------
    # RESILIENCE, DESTINATIONS & UTILITIES
    # -------------------------------------------------------------------------
    def _resolve_target_dir(self, destination: Optional[str] = None, is_temporary: bool = True) -> Path:
        """
        Resolves target directory.
        Defaults to temp folder unless user explicitly mentions a permanent destination
        like 'desktop', 'downloads', 'documents', or a custom folder path.
        """
        if not destination or is_temporary:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            return self._temp_dir

        dest_clean = str(destination).strip().lower()
        user_home = Path.home()

        # Known-folder resolution (OneDrive-aware, registry-backed)
        resolved = resolve_known_folder(dest_clean)
        if resolved:
            return Path(resolved)

        shortcuts = {
            "desktop": user_home / "Desktop",
            "downloads": user_home / "Downloads",
            "documents": user_home / "Documents",
            "music": user_home / "Music",
            "pictures": user_home / "Pictures",
            "videos": user_home / "Videos",
        }
        if dest_clean in shortcuts:
            target = shortcuts[dest_clean]
            if target.exists():
                return target

        p = Path(os.path.expanduser(str(destination).strip("'\"")))
        if not p.is_absolute():
            p = user_home / p
        p.mkdir(parents=True, exist_ok=True)
        return p

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
            filepath.parent.mkdir(parents=True, exist_ok=True)
            yield filepath
        except Exception as e:
            logger.error("[document_agent] Document write failed for %s: %s", filepath, e, exc_info=True)
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
    async def parse_document(self, filepath: Union[str, Path] = "", file_path: Union[str, Path] = "", **kwargs: Any) -> Dict[str, Any]:
        """Auto-detects file type and extracts structured content."""
        target = filepath or file_path or kwargs.get("path") or kwargs.get("target_path") or ""
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        ext = path.suffix.lower()
        if ext == ".pdf":
            return await self._parse_pdf(path)
        elif ext == ".docx":
            return await self._parse_docx(path)
        elif ext in [".xlsx", ".xls"]:
            return await self._parse_xlsx(path)
        elif ext == ".csv":
            return await self._parse_csv(path)
        elif ext in [".txt", ".md", ".log", ".rst", ".py", ".js", ".html"]:
            return await self._parse_text(path)
        elif ext == ".json":
            return await self._parse_json(path)
        elif ext in [".pptx", ".ppt"]:
            return await self._parse_pptx(path)
        else:
            try:
                return await self._parse_text(path)
            except Exception:
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
                sheets_data[sheet_name] = {
                    "headers": list(rows[0]) if rows else [],
                    "rows": [list(r) for r in rows[1:]] if len(rows) > 1 else [],
                }
            return {"type": "xlsx", "sheets": sheets_data}

        return await self._run_in_executor(_extract)

    async def _parse_csv(self, path: Path) -> Dict[str, Any]:
        def _extract():
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                rows = list(reader)
            headers = rows[0] if rows else []
            body_rows = rows[1:] if len(rows) > 1 else []
            return {"type": "csv", "headers": headers, "rows": body_rows, "total_rows": len(rows)}

        return await self._run_in_executor(_extract)

    async def _parse_text(self, path: Path) -> Dict[str, Any]:
        def _extract():
            content = path.read_text(encoding="utf-8", errors="replace")
            return {"type": path.suffix.lstrip("."), "content": content, "lines": len(content.splitlines())}

        return await self._run_in_executor(_extract)

    async def _parse_json(self, path: Path) -> Dict[str, Any]:
        def _extract():
            content = path.read_text(encoding="utf-8", errors="replace")
            parsed = json.loads(content)
            return {"type": "json", "data": parsed}

        return await self._run_in_executor(_extract)

    async def _parse_pptx(self, path: Path) -> Dict[str, Any]:
        if not _HAS_PPTX:
            raise RuntimeError("PPTX parsing requires 'python-pptx'. Install via: pip install python-pptx")

        def _extract():
            prs = Presentation(str(path))
            slides_data = []
            for i, slide in enumerate(prs.slides):
                slide_text = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for paragraph in shape.text_frame.paragraphs:
                            text = "".join(run.text for run in paragraph.runs).strip()
                            if text:
                                slide_text.append(text)
                slides_data.append({"slide_number": i + 1, "text": slide_text})
            return {"type": "pptx", "total_slides": len(prs.slides), "slides": slides_data}

        return await self._run_in_executor(_extract)

    # -------------------------------------------------------------------------
    # CAPABILITY B: HIGH-AESTHETIC SPREADSHEETS (.xlsx) WITH SMART FORMULAS
    # -------------------------------------------------------------------------
    async def generate_excel(
        self,
        filename: str,
        sheets: Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]],
        metadata: Optional[Dict[str, Any]] = None,
        theme: str = "slate",
        add_summary_row: bool = True,
        destination: Optional[str] = None,
        is_temporary: bool = True,
    ) -> Path:
        if not _HAS_OPENPYXL:
            raise RuntimeError("Excel generation requires 'openpyxl'.")

        palette = get_theme_palette(theme or (metadata.get("theme") if metadata else None))
        filename = filename.replace(".xlsx", "")
        target_dir = self._resolve_target_dir(destination, is_temporary=is_temporary)
        filepath = target_dir / f"{filename}.xlsx"

        def _build_excel():
            wb = Workbook()
            wb.remove(wb.active)

            nonlocal sheets
            dict_sheets: dict[str, dict[str, Any]] = {}
            if isinstance(sheets, list):
                for idx, s in enumerate(sheets):
                    if isinstance(s, dict):
                        s_name = s.get("sheet_name") or s.get("name") or f"Sheet{idx+1}"
                        dict_sheets[s_name] = s
            elif isinstance(sheets, dict):
                if "headers" in sheets or "columns" in sheets or "rows" in sheets:
                    sheet_name = metadata.get("title", "Sheet1") if metadata else "Sheet1"
                    dict_sheets[sheet_name] = sheets
                else:
                    dict_sheets = sheets
            else:
                dict_sheets = {"Sheet1": {"headers": ["Content"], "rows": [[str(sheets)]]}}

            if not dict_sheets:
                dict_sheets = {"Sheet1": {"headers": ["Status"], "rows": [["No data provided"]]}}

            # OpenPyXL Styles derived from theme palette
            header_fill = PatternFill(start_color=palette.primary_hex, end_color=palette.primary_hex, fill_type="solid")
            header_font = Font(name="Segoe UI", size=11, bold=True, color=palette.header_text_hex)
            zebra_fill = PatternFill(start_color=palette.zebra_hex, end_color=palette.zebra_hex, fill_type="solid")
            body_font = Font(name="Segoe UI", size=10, color=palette.text_hex)
            summary_font = Font(name="Segoe UI", size=11, bold=True, color=palette.primary_hex)
            summary_fill = PatternFill(start_color=palette.kpi_bg_hex, end_color=palette.kpi_bg_hex, fill_type="solid")

            thin_border = Border(
                bottom=Side(style="thin", color=palette.border_hex),
                top=Side(style="thin", color=palette.border_hex),
                left=Side(style="thin", color=palette.border_hex),
                right=Side(style="thin", color=palette.border_hex),
            )
            double_bottom_border = Border(
                top=Side(style="thin", color=palette.border_hex),
                bottom=Side(style="double", color=palette.primary_hex),
                left=Side(style="thin", color=palette.border_hex),
                right=Side(style="thin", color=palette.border_hex),
            )

            for sheet_name, sheet_data in dict_sheets.items():
                ws = wb.create_sheet(title=str(sheet_name)[:31])
                headers = sheet_data.get("headers") or sheet_data.get("columns") or []
                rows = sheet_data.get("rows") or []

                normalized_rows = []
                if rows and isinstance(rows[0], dict):
                    if not headers:
                        headers = list(rows[0].keys())
                    for r in rows:
                        normalized_rows.append([r.get(k, "") for k in headers])
                else:
                    normalized_rows = rows

                if not headers and normalized_rows:
                    headers = [f"Column {i+1}" for i in range(len(normalized_rows[0]))]

                for col_idx, header in enumerate(headers, 1):
                    cell = ws.cell(row=1, column=col_idx, value=str(header))
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border = thin_border

                numeric_cols: set[int] = set()

                for row_idx, row_data in enumerate(normalized_rows, 2):
                    sanitized = self._sanitize_row(row_data, len(headers))
                    for col_idx, value in enumerate(sanitized, 1):
                        cell = ws.cell(row=row_idx, column=col_idx, value=value)
                        cell.font = body_font
                        cell.border = thin_border
                        if row_idx % 2 == 0:
                            cell.fill = zebra_fill
                        if isinstance(value, (int, float)):
                            numeric_cols.add(col_idx)
                            cell.number_format = "#,##0.00" if isinstance(value, float) else "#,##0"

                last_data_row = len(normalized_rows) + 1

                # Smart Summary Row with Live Formulas
                if add_summary_row and normalized_rows and numeric_cols and len(normalized_rows) > 1:
                    summary_row_idx = last_data_row + 1
                    for col_idx in range(1, len(headers) + 1):
                        cell = ws.cell(row=summary_row_idx, column=col_idx)
                        cell.font = summary_font
                        cell.fill = summary_fill
                        cell.border = double_bottom_border

                        if col_idx == 1:
                            cell.value = "TOTAL / SUMMARY"
                        elif col_idx in numeric_cols:
                            col_letter = get_column_letter(col_idx)
                            h_name = str(headers[col_idx - 1]).lower()
                            if any(k in h_name for k in ("avg", "average", "rate", "pct", "%", "ratio", "score")):
                                cell.value = f"=AVERAGE({col_letter}2:{col_letter}{last_data_row})"
                                cell.number_format = "#,##0.00"
                            else:
                                cell.value = f"=SUM({col_letter}2:{col_letter}{last_data_row})"
                                cell.number_format = "#,##0.00"
                        else:
                            cell.value = ""

                if headers and normalized_rows:
                    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_data_row}"
                    ws.freeze_panes = "A2"
                elif headers:
                    ws.freeze_panes = "A2"

                if normalized_rows and numeric_cols:
                    for col_idx in numeric_cols:
                        col_letter = get_column_letter(col_idx)
                        rule = ColorScaleRule(
                            start_type="min", start_color="F87171",
                            mid_type="percentile", mid_value=50, mid_color="FBBF24",
                            end_type="max", end_color="34D399",
                        )
                        ws.conditional_formatting.add(f"{col_letter}2:{col_letter}{last_data_row}", rule)

                if sheet_data.get("chart") and len(headers) >= 2 and len(normalized_rows) >= 1:
                    chart_type = sheet_data["chart"].get("type", "bar")
                    chart_title = sheet_data["chart"].get("title", sheet_name)

                    if chart_type == "pie":
                        chart = PieChart()
                        data = Reference(ws, min_col=2, min_row=1, max_row=last_data_row)
                        cats = Reference(ws, min_col=1, min_row=2, max_row=last_data_row)
                        chart.add_data(data, titles_from_data=True)
                        chart.set_categories(cats)
                    else:
                        chart = BarChart()
                        data = Reference(ws, min_col=2, max_col=len(headers), min_row=1, max_row=last_data_row)
                        cats = Reference(ws, min_col=1, min_row=2, max_row=last_data_row)
                        chart.add_data(data, titles_from_data=True)
                        chart.set_categories(cats)

                    chart.title = chart_title
                    chart.style = 10
                    ws.add_chart(chart, f"{get_column_letter(len(headers) + 2)}2")

                for col_idx in range(1, len(headers) + 1):
                    max_length = len(str(headers[col_idx - 1])) if headers else 10
                    for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, min_row=2, max_row=min(100, last_data_row)):
                        for cell in row:
                            if cell.value:
                                max_length = max(max_length, min(len(str(cell.value)), 40))
                    ws.column_dimensions[get_column_letter(col_idx)].width = max_length + 4

            if metadata:
                wb.properties.title = metadata.get("title", filename)
                wb.properties.creator = "Makima OS v8.3"

            wb.save(str(filepath))
            return filepath

        async with self._safe_write(filepath):
            res_path = await self._run_in_executor(_build_excel)
            self._last_generated_document = res_path
            return res_path

    # -------------------------------------------------------------------------
    # CAPABILITY C: PUBLICATION-READY WORD DOCUMENTS (.docx)
    # -------------------------------------------------------------------------
    async def generate_word(
        self,
        filename: str,
        content_blocks: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        theme: str = "slate",
        has_cover_page: bool = False,
        destination: Optional[str] = None,
        is_temporary: bool = True,
    ) -> Path:
        if not _HAS_DOCX:
            raise RuntimeError("Word generation requires 'python-docx'.")

        palette = get_theme_palette(theme or (metadata.get("theme") if metadata else None))
        filename = filename.replace(".docx", "")
        target_dir = self._resolve_target_dir(destination, is_temporary=is_temporary)
        filepath = target_dir / f"{filename}.docx"

        def _build_word():
            doc = Document()

            try:
                settings_element = doc.settings.element
                update_fields = parse_xml(f'<w:updateFields {nsdecls("w")} w:val="true"/>')
                settings_element.append(update_fields)
            except Exception as e:
                logger.debug("[document_agent] Could not set w:updateFields: %s", e)

            for section in doc.sections:
                section.top_margin = Cm(2.54)
                section.bottom_margin = Cm(2.54)
                section.left_margin = Cm(2.54)
                section.right_margin = Cm(2.54)

                header = section.header
                hp = header.paragraphs[0]
                hp.text = metadata.get("title", "Makima OS Executive Report") if metadata else "Makima OS Report"
                hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                if hp.runs:
                    hp.runs[0].font.size = Pt(9)
                    hp.runs[0].font.color.rgb = RGBColor(100, 116, 139)

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

            if has_cover_page or (metadata and metadata.get("has_cover_page")):
                p_spacer = doc.add_paragraph()
                p_spacer.paragraph_format.space_before = Pt(72)

                p_title = doc.add_paragraph()
                p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
                run_title = p_title.add_run(metadata.get("title", filename) if metadata else filename)
                run_title.font.name = "Segoe UI"
                run_title.font.size = Pt(30)
                run_title.font.bold = True
                run_title.font.color.rgb = RGBColor(*palette.primary_rgb)

                p_sub = doc.add_paragraph()
                run_sub = p_sub.add_run(metadata.get("subtitle", "Executive Strategic Briefing") if metadata else "Executive Report")
                run_sub.font.name = "Segoe UI"
                run_sub.font.size = Pt(14)
                run_sub.font.color.rgb = RGBColor(*palette.accent_rgb)

                p_meta = doc.add_paragraph()
                p_meta.paragraph_format.space_before = Pt(140)
                run_meta = p_meta.add_run(f"Author: Makima OS v8.3 | Date: {metadata.get('date', 'Current Date') if metadata else 'Current Date'}\nClassification: Confidential & Proprietary")
                run_meta.font.size = Pt(10)
                run_meta.font.color.rgb = RGBColor(100, 116, 139)

                doc.add_page_break()

            for block in content_blocks:
                b_type = str(block.get("type", "body")).lower()
                content = block.get("content") or block.get("text", "")

                if b_type == "title":
                    p = doc.add_heading(str(content), level=0)
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in p.runs:
                        run.font.color.rgb = RGBColor(*palette.primary_rgb)

                elif b_type == "subtitle":
                    p = doc.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = p.add_run(str(content))
                    run.font.size = Pt(13)
                    run.italic = True
                    run.font.color.rgb = RGBColor(100, 116, 139)

                elif b_type == "toc":
                    p = doc.add_paragraph()
                    run = p.add_run("Table of Contents\n")
                    run.bold = True
                    run.font.size = Pt(14)
                    run.font.color.rgb = RGBColor(*palette.primary_rgb)

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
                    r4 = p.add_run("[Table of Contents]")
                    r5 = p.add_run()
                    r5._r.append(fldChar_end)

                elif b_type in ["h1", "h2", "h3", "heading"]:
                    level = int(block.get("level", 1)) if b_type == "heading" else int(b_type[-1])
                    doc.add_heading(str(content), level=level)

                elif b_type in ["body", "paragraph", "text"]:
                    p = doc.add_paragraph(str(content))
                    p.paragraph_format.space_after = Pt(8)
                    p.paragraph_format.line_spacing = 1.15

                elif b_type == "quote":
                    p = doc.add_paragraph()
                    p.paragraph_format.left_indent = Cm(1.2)
                    run = p.add_run(f'"{content}"')
                    run.italic = True
                    run.font.color.rgb = RGBColor(71, 85, 105)

                elif b_type == "bullet":
                    doc.add_paragraph(str(content), style="List Bullet")

                elif b_type == "numbered":
                    doc.add_paragraph(str(content), style="List Number")

                elif b_type == "callout":
                    p = doc.add_paragraph()
                    run = p.add_run(f"💡 {content}")
                    run.bold = True
                    run.font.color.rgb = RGBColor(*palette.primary_rgb)
                    p.paragraph_format.left_indent = Cm(1)
                    p.paragraph_format.space_before = Pt(10)
                    p.paragraph_format.space_after = Pt(10)
                    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{palette.kpi_bg_hex}"/>')
                    p._p.get_or_add_pPr().append(shd)

                elif b_type == "code":
                    p = doc.add_paragraph()
                    run = p.add_run(str(content))
                    run.font.name = "Consolas"
                    run.font.size = Pt(9.5)
                    run.font.color.rgb = RGBColor(15, 23, 42)
                    p.paragraph_format.left_indent = Cm(0.8)
                    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F1F5F9"/>')
                    p._p.get_or_add_pPr().append(shd)

                elif b_type == "image":
                    if isinstance(content, str) and content.startswith("data:image"):
                        img_data = base64.b64decode(content.split(",")[1])
                        img_stream = io.BytesIO(img_data)
                        doc.add_picture(img_stream, width=Inches(5.5))
                    elif Path(str(content)).exists():
                        doc.add_picture(str(content), width=Inches(5.5))
                    if doc.paragraphs:
                        last_paragraph = doc.paragraphs[-1]
                        last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

                elif b_type == "table":
                    table_data = None
                    if isinstance(block, dict):
                        table_data = block.get("data") or block.get("table")
                        if not table_data and ("headers" in block or "rows" in block or "columns" in block):
                            table_data = block
                    if table_data is None:
                        table_data = content

                    headers: list[Any] = []
                    rows: list[list[Any]] = []

                    if isinstance(table_data, dict):
                        headers = table_data.get("headers") or table_data.get("columns") or []
                        rows = table_data.get("rows") or []
                    elif isinstance(table_data, list):
                        if table_data and isinstance(table_data[0], list):
                            headers = table_data[0]
                            rows = table_data[1:]
                        elif table_data and isinstance(table_data[0], dict):
                            headers = list(table_data[0].keys())
                            rows = [[row.get(k, "") for k in headers] for row in table_data]

                    if not headers and not rows:
                        headers = ["Content"]
                        rows = [[str(table_data or content)]]
                    elif not headers and rows:
                        headers = [f"Col {i+1}" for i in range(len(rows[0]))]

                    table = doc.add_table(rows=1 + len(rows), cols=max(1, len(headers)))
                    table.style = "Table Grid"
                    table.alignment = WD_TABLE_ALIGNMENT.CENTER

                    for i, h in enumerate(headers):
                        cell = table.rows[0].cells[i]
                        cell.text = str(h)
                        for paragraph in cell.paragraphs:
                            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            for run in paragraph.runs:
                                run.bold = True
                                run.font.color.rgb = RGBColor(*palette.header_text_rgb)
                        shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{palette.primary_hex}"/>')
                        cell._tc.get_or_add_tcPr().append(shading)

                    for r_idx, row_data in enumerate(rows, 1):
                        sanitized = self._sanitize_row(row_data, len(headers))
                        for c_idx, val in enumerate(sanitized):
                            cell = table.rows[r_idx].cells[c_idx]
                            cell.text = str(val) if val is not None else ""
                            if r_idx % 2 == 0:
                                shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{palette.zebra_hex}"/>')
                                cell._tc.get_or_add_tcPr().append(shading)

                elif b_type == "page_break":
                    doc.add_page_break()

            if metadata:
                doc.core_properties.title = metadata.get("title", filename)
                doc.core_properties.author = "Makima OS v8.3"

            doc.save(str(filepath))
            return filepath

        async with self._safe_write(filepath):
            res_path = await self._run_in_executor(_build_word)
            self._last_generated_document = res_path
            return res_path

    async def create_spreadsheet(
        self,
        file_path: Union[str, Path] = "",
        title: str = "Spreadsheet",
        columns: Optional[List[str]] = None,
        data: Optional[List[List[Any]]] = None,
        theme: str = "slate",
        include_summary: bool = True,
        **kwargs: Any,
    ) -> str:
        """Alias and adapter for generating Excel spreadsheets."""
        p = Path(file_path) if file_path else Path("spreadsheet.xlsx")
        dest = str(p.parent) if p.parent != Path(".") else None
        fname = p.stem
        sheets = {
            title: {
                "headers": columns or kwargs.get("headers", ["Column 1"]),
                "rows": data or kwargs.get("rows", []),
            }
        }
        res = await self.generate_excel(
            filename=fname,
            sheets=sheets,
            metadata={"title": title},
            theme=theme,
            add_summary_row=include_summary,
            destination=dest,
            is_temporary=False,
        )
        return f"Successfully created spreadsheet: {res}"

    # -------------------------------------------------------------------------
    # CAPABILITY D: EXECUTIVE POWERPOINT GENERATION (.pptx)
    # -------------------------------------------------------------------------
    async def generate_ppt(
        self,
        filename: str,
        slides_data: List[Dict[str, Any]],
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        theme: str = "slate",
        destination: Optional[str] = None,
        is_temporary: bool = True,
    ) -> Path:
        """Generates real, executive 16:9 PowerPoint slides via python-pptx."""
        palette = get_theme_palette(theme or (metadata.get("theme") if metadata else None))
        filename = filename.replace(".pptx", "")
        target_dir = self._resolve_target_dir(destination, is_temporary=is_temporary)
        filepath = target_dir / f"{filename}.pptx"

        if not _HAS_PPTX:
            logger.warning("[document_agent] python-pptx not installed — compiling HTML slide deck fallback")
            html_slides = target_dir / f"{filename}_slides.html"
            slides_html = []
            for idx, s in enumerate(slides_data, 1):
                s_title = s.get("title") or s.get("heading") or f"Slide {idx}"
                s_bullets = s.get("bullets") or s.get("points") or []
                s_body = s.get("content") or s.get("body") or ""
                bullets_li = "".join(f"<li>{_format_inline(str(b))}</li>" for b in s_bullets)
                slides_html.append(f"""
                <section style="background: #{palette.primary_hex}; color: #F8FAFC; padding: 3rem; border-radius: 12px; margin-bottom: 2rem; min-height: 350px;">
                    <h2 style="color: #{palette.accent_hex}; font-size: 24pt; border-bottom: 2px solid #{palette.accent_hex}; padding-bottom: 0.5rem;">{_escape_html(s_title)}</h2>
                    {f'<p style="font-size: 14pt; color: #E2E8F0;">{_format_inline(s_body)}</p>' if s_body else ''}
                    {f'<ul style="font-size: 13pt; line-height: 1.8; margin-top: 1rem;">{bullets_li}</ul>' if bullets_li else ''}
                </section>
                """)

            deck_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title or filename}</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0F172A; margin: 0; padding: 2rem; max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #F8FAFC; text-align: center; margin-bottom: 2rem; }}
    </style>
</head>
<body>
    <h1>{title or filename}</h1>
    {''.join(slides_html)}
</body>
</html>"""
            html_slides.write_text(deck_template, encoding="utf-8")
            self._last_generated_document = html_slides
            return html_slides

        def _build_ppt():
            prs = Presentation()
            prs.slide_width = PptxInches(13.333)
            prs.slide_height = PptxInches(7.5)

            title_layout = prs.slide_layouts[0]
            slide1 = prs.slides.add_slide(title_layout)
            slide_title = slide1.shapes.title
            slide_subtitle = slide1.placeholders[1] if len(slide1.placeholders) > 1 else None

            if slide_title:
                slide_title.text = title or (metadata.get("title", filename) if metadata else filename)
                for p in slide_title.text_frame.paragraphs:
                    p.font.name = "Segoe UI"
                    p.font.size = PptxPt(40)
                    p.font.bold = True
                    p.font.color.rgb = PptxRGBColor(*palette.primary_rgb)

            if slide_subtitle:
                sub_text = metadata.get("subtitle", "Makima OS Executive Presentation") if metadata else "Makima OS Presentation"
                slide_subtitle.text = sub_text
                for p in slide_subtitle.text_frame.paragraphs:
                    p.font.name = "Segoe UI"
                    p.font.size = PptxPt(18)
                    p.font.color.rgb = PptxRGBColor(*palette.accent_rgb)

            content_layout = prs.slide_layouts[1]
            for s_info in slides_data:
                s = prs.slides.add_slide(content_layout)
                s_title = s_info.get("title") or s_info.get("heading") or "Executive Summary"
                s_bullets = s_info.get("bullets") or s_info.get("points") or []
                s_body = s_info.get("content") or s_info.get("body") or ""

                if s.shapes.title:
                    s.shapes.title.text = s_title
                    for p in s.shapes.title.text_frame.paragraphs:
                        p.font.name = "Segoe UI"
                        p.font.size = PptxPt(28)
                        p.font.bold = True
                        p.font.color.rgb = PptxRGBColor(*palette.primary_rgb)

                body_shape = s.placeholders[1] if len(s.placeholders) > 1 else None
                if body_shape:
                    tf = body_shape.text_frame
                    tf.word_wrap = True
                    if s_body:
                        p = tf.paragraphs[0]
                        p.text = str(s_body)
                        p.font.name = "Segoe UI"
                        p.font.size = PptxPt(16)
                        p.font.color.rgb = PptxRGBColor(*palette.text_rgb)

                    for b in s_bullets:
                        p = tf.add_paragraph()
                        p.text = str(b)
                        p.font.name = "Segoe UI"
                        p.font.size = PptxPt(14)
                        p.level = 0
                        p.font.color.rgb = PptxRGBColor(*palette.text_rgb)

            prs.save(str(filepath))
            return filepath

        async with self._safe_write(filepath):
            res_path = await self._run_in_executor(_build_ppt)
            self._last_generated_document = res_path
            return res_path

    # -------------------------------------------------------------------------
    # CAPABILITY E: EXECUTIVE REPORT GENERATION (Markdown / HTML)
    # -------------------------------------------------------------------------
    async def generate_report(
        self,
        filename: str,
        sections: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        theme: str = "slate",
        destination: Optional[str] = None,
        is_temporary: bool = True,
    ) -> Path:
        """Generates a structured Markdown report and compiles to HTML."""
        palette = get_theme_palette(theme or (metadata.get("theme") if metadata else None))
        filename = filename.replace(".html", "").replace(".md", "")
        target_dir = self._resolve_target_dir(destination, is_temporary=is_temporary)
        filepath_md = target_dir / f"{filename}.md"
        filepath_html = target_dir / f"{filename}.html"

        md_content = []
        doc_title = metadata.get("title", filename) if metadata else filename
        doc_date = metadata.get("date", "Current Date") if metadata else "Current Date"

        md_content.append(f"# {doc_title}\n\n")
        md_content.append(f"*Generated by Makima OS v8.3 | {doc_date}*\n\n---\n\n")

        for section in sections:
            heading = section.get("heading") or section.get("title", "Section")
            body = section.get("body") or section.get("content", "")
            md_content.append(f"## {heading}\n\n")
            if body:
                md_content.append(f"{body}\n\n")
            if "bullets" in section:
                for bullet in section["bullets"]:
                    md_content.append(f"- {bullet}\n")
                md_content.append("\n")

        full_md = "".join(md_content)

        async with self._safe_write(filepath_md):
            def _write_md():
                filepath_md.write_text(full_md, encoding="utf-8")
            await self._run_in_executor(_write_md)

        def _build_html():
            if _HAS_MARKDOWN:
                html_body = markdown.markdown(full_md, extensions=["tables", "fenced_code", "toc"])
            else:
                html_body = _simple_markdown_to_html(full_md)

            html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{doc_title}</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.6; color: #{palette.text_hex}; max-width: 850px; margin: 0 auto; padding: 2rem; }}
        h1, h2, h3 {{ color: #{palette.primary_hex}; border-bottom: 2px solid #E2E8F0; padding-bottom: 0.3rem; }}
        h1 {{ border-bottom: 3px solid #{palette.accent_hex}; }}
        table {{ border-collapse: collapse; width: 100%; margin: 1.5rem 0; }}
        th, td {{ border: 1px solid #{palette.border_hex}; padding: 10px 14px; text-align: left; }}
        th {{ background-color: #{palette.primary_hex}; color: #{palette.header_text_hex}; }}
        tr:nth-child(even) {{ background-color: #{palette.zebra_hex}; }}
        code {{ background: #{palette.zebra_hex}; color: #{palette.primary_hex}; padding: 2px 5px; border-radius: 4px; font-family: Consolas, monospace; }}
        hr {{ border: none; border-top: 1px solid #E2E8F0; margin: 24px 0; }}
    </style>
</head>
<body>
    {html_body}
</body>
</html>"""
            filepath_html.write_text(html_template, encoding="utf-8")
            return filepath_html

        await self._run_in_executor(_build_html)
        logger.info("[document_agent] HTML Report generated: %s", filepath_html)
        self._last_generated_document = filepath_html
        return filepath_html

    # -------------------------------------------------------------------------
    # CAPABILITY F: ENTERPRISE PDF COMPILATION (.pdf)
    # -------------------------------------------------------------------------
    async def generate_pdf(
        self,
        md_content_or_path: Union[str, Path, None],
        output_filename: str = "document.pdf",
        theme: str = "slate",
        destination: Optional[str] = None,
        is_temporary: bool = True,
    ) -> Path:
        """Converts Markdown or HTML document into a styled enterprise PDF via Playwright."""
        palette = get_theme_palette(theme)
        md_text = ""
        if md_content_or_path:
            try:
                in_path = Path(str(md_content_or_path))
                if in_path.exists() and in_path.is_file():
                    md_text = in_path.read_text(encoding="utf-8", errors="replace")
                else:
                    md_text = str(md_content_or_path)
            except Exception:
                md_text = str(md_content_or_path)
        else:
            md_text = "# Document\nNo content provided."

        if not output_filename.endswith(".pdf"):
            output_filename += ".pdf"

        target_dir = self._resolve_target_dir(destination, is_temporary=is_temporary)
        out_path = target_dir / output_filename
        title_stem = out_path.stem
        json_md = json.dumps(md_text)

        html_template = rf"""<!DOCTYPE html>
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
        @page {{ size: A4; margin: 20mm 15mm 20mm 15mm; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; color: #{palette.text_hex}; line-height: 1.6; font-size: 11pt; padding: 0 10px; }}
        h1 {{ color: #{palette.primary_hex}; font-size: 22pt; border-bottom: 3px solid #{palette.accent_hex}; padding-bottom: 8px; }}
        h2 {{ color: #{palette.primary_hex}; font-size: 16pt; border-bottom: 1.5px solid #{palette.border_hex}; padding-bottom: 4px; margin-top: 24px; page-break-after: avoid; }}
        h3 {{ color: #{palette.text_hex}; font-size: 13pt; margin-top: 18px; page-break-after: avoid; }}
        p {{ margin-bottom: 12px; text-align: justify; }}
        blockquote {{ background: #{palette.kpi_bg_hex}; border-left: 4px solid #{palette.accent_hex}; margin: 16px 0; padding: 10px 16px; border-radius: 0 6px 6px 0; font-size: 10.5pt; }}
        table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 10pt; page-break-inside: avoid; }}
        th, td {{ border: 1px solid #{palette.border_hex}; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #{palette.primary_hex}; color: #{palette.header_text_hex}; font-weight: 600; }}
        tr:nth-child(even) {{ background-color: #{palette.zebra_hex}; }}
        code {{ background: #{palette.zebra_hex}; color: #{palette.primary_hex}; padding: 2px 5px; border-radius: 4px; font-family: Consolas, monospace; font-size: 9.5pt; }}
        .mermaid {{ display: flex; justify-content: center; margin: 20px 0; page-break-inside: avoid; }}
        hr {{ border: none; border-top: 1px solid #{palette.border_hex}; margin: 24px 0; }}
    </style>
</head>
<body>
    <div id="content"></div>
    <script>
        const rawMarkdown = __MARKDOWN_RAW__;
        if (typeof marked !== 'undefined' && marked.parse) {{
            document.getElementById('content').innerHTML = marked.parse(rawMarkdown);
        }} else {{
            document.getElementById('content').innerHTML = "<pre>" + rawMarkdown + "</pre>";
        }}
        try {{
            document.querySelectorAll('pre code.language-mermaid').forEach((block) => {{
                let text = block.textContent;
                let lines = text.split('\n');
                let fixedLines = lines.map(line => {{
                    return line.replace(/([A-Za-z0-9_]+\[)([^"\]]+)(\])/g, (m, p1, p2, p3) => {{
                        if ((p2.includes(':') || p2.includes(',')) && !p2.startsWith('"')) {{
                            return p1 + '"' + p2 + '"' + p3;
                        }}
                        return m;
                    }});
                }});
                const div = document.createElement('div');
                div.className = 'mermaid';
                div.textContent = fixedLines.join('\n');
                block.parentNode.replaceWith(div);
            }});
            if (typeof mermaid !== 'undefined') {{
                mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
            }}
        }} catch (_) {{}}
    </script>
</body>
</html>"""
        html_content = html_template.replace("__TITLE__", title_stem).replace("__MARKDOWN_RAW__", json_md)

        temp_html = self._temp_dir / f"temp_{title_stem}.html"
        temp_html.write_text(html_content, encoding="utf-8")

        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(f"file:///{temp_html.as_posix()}", wait_until="domcontentloaded")
                    await asyncio.sleep(1.0)
                    await page.pdf(
                        path=str(out_path),
                        format="A4",
                        print_background=True,
                        margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                    )
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("[document_agent] Playwright PDF generation failed (%s) — falling back to HTML report", e)
            fallback_html = target_dir / f"{title_stem}.html"
            fallback_html.write_text(html_content, encoding="utf-8")
            self._last_generated_document = fallback_html
            return fallback_html
        finally:
            if temp_html.exists():
                try:
                    temp_html.unlink()
                except Exception:
                    pass

        logger.info("[document_agent] Successfully compiled PDF to %s", out_path)
        self._last_generated_document = out_path
        return out_path

    # -------------------------------------------------------------------------
    # CAPABILITY G: CROSS-FORMAT CONVERTER ENGINE
    # -------------------------------------------------------------------------
    async def convert_document(
        self,
        source_path: Union[str, Path],
        target_format: str,
        output_filename: Optional[str] = None,
        theme: str = "slate",
        destination: Optional[str] = None,
        is_temporary: bool = True,
    ) -> Path:
        """Converts any document between CSV, XLSX, DOCX, PDF, HTML, and Markdown."""
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(f"Source document not found: {src}")

        target_fmt = str(target_format).lower().replace(".", "").strip()
        out_name = output_filename or f"{src.stem}_converted.{target_fmt}"
        target_dir = self._resolve_target_dir(destination, is_temporary=is_temporary)

        parsed = await self.parse_document(src)
        p_type = parsed.get("type", "")

        if target_fmt in ("xlsx", "excel"):
            if p_type == "csv":
                sheets = {"Sheet1": {"headers": parsed.get("headers", []), "rows": parsed.get("rows", [])}}
                return await self.generate_excel(out_name, sheets, theme=theme, destination=destination, is_temporary=is_temporary)
            elif p_type == "json":
                data = parsed.get("data")
                rows = data if isinstance(data, list) else [data]
                sheets = {"Sheet1": {"headers": [], "rows": rows}}
                return await self.generate_excel(out_name, sheets, theme=theme, destination=destination, is_temporary=is_temporary)
            else:
                raw_text = parsed.get("content", str(parsed))
                sheets = {"Sheet1": {"headers": ["Document Content"], "rows": [[line] for line in raw_text.splitlines()]}}
                return await self.generate_excel(out_name, sheets, theme=theme, destination=destination, is_temporary=is_temporary)

        elif target_fmt in ("docx", "word"):
            blocks = []
            if p_type == "csv":
                blocks.append({"type": "heading", "content": f"{src.stem} Data Table", "level": 1})
                blocks.append({"type": "table", "data": {"headers": parsed.get("headers", []), "rows": parsed.get("rows", [])}})
            elif p_type == "xlsx":
                sheets_dict = parsed.get("sheets", {})
                for s_name, s_data in sheets_dict.items():
                    blocks.append({"type": "heading", "content": s_name, "level": 2})
                    blocks.append({"type": "table", "data": s_data})
            else:
                raw_text = parsed.get("content", str(parsed))
                blocks.append({"type": "paragraph", "content": raw_text})
            return await self.generate_word(out_name, blocks, theme=theme, destination=destination, is_temporary=is_temporary)

        elif target_fmt == "pdf":
            raw_text = parsed.get("content", "")
            if not raw_text and p_type in ("csv", "xlsx"):
                headers = parsed.get("headers", [])
                rows = parsed.get("rows", [])
                lines = [f"# {src.stem}\n"]
                if headers:
                    lines.append("| " + " | ".join(str(h) for h in headers) + " |")
                    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                    for r in rows:
                        lines.append("| " + " | ".join(str(c) for c in r) + " |")
                raw_text = "\n".join(lines)
            return await self.generate_pdf(raw_text, out_name, theme=theme, destination=destination, is_temporary=is_temporary)

        elif target_fmt == "csv":
            out_path = target_dir / (out_name if out_name.endswith(".csv") else f"{out_name}.csv")
            if p_type == "xlsx":
                sheets_dict = parsed.get("sheets", {})
                first_sheet = list(sheets_dict.values())[0] if sheets_dict else {}
                headers = first_sheet.get("headers", [])
                rows = first_sheet.get("rows", [])
                with open(out_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    if headers:
                        writer.writerow(headers)
                    writer.writerows(rows)
                self._last_generated_document = out_path
                return out_path
            else:
                raw_text = parsed.get("content", "")
                out_path.write_text(raw_text, encoding="utf-8")
                self._last_generated_document = out_path
                return out_path

        elif target_fmt in ("html", "report"):
            raw_text = parsed.get("content", "")
            sections = [{"title": f"{src.stem} Content", "body": raw_text}]
            return await self.generate_report(out_name, sections, theme=theme, destination=destination, is_temporary=is_temporary)

        else:
            raise ValueError(f"Unsupported target format for conversion: {target_fmt}")

    # -------------------------------------------------------------------------
    # CAPABILITY H: SAFE DELETION & EPHEMERAL CLEANUP
    # -------------------------------------------------------------------------
    async def delete_document(self, filepath: Optional[Union[str, Path]] = None) -> Path:
        """Safely delete a generated or temporary document."""
        target: Optional[Path] = None
        if filepath:
            target = Path(filepath)
            if not target.is_absolute():
                # Check in output_dir, temp_dir, or current dir
                for candidate in (self._temp_dir / filepath, self.output_dir / filepath, Path(filepath)):
                    if candidate.exists():
                        target = candidate
                        break
        else:
            target = self._last_generated_document

        if not target or not target.exists():
            raise FileNotFoundError(f"Document not found to delete: {filepath or 'last document'}")

        def _unlink():
            target.unlink()

        await self._run_in_executor(_unlink)
        if self._last_generated_document == target:
            self._last_generated_document = None
        logger.info("[document_agent] Deleted document: %s", target)
        return target

    async def cleanup_temp_documents(self, max_age_hours: Optional[float] = None) -> int:
        """Clean up all temporary documents generated by DocumentAgent."""
        def _clean():
            count = 0
            if not self._temp_dir.exists():
                return 0
            now = time.time()
            for f in self._temp_dir.iterdir():
                if f.is_file():
                    if max_age_hours is None or (now - f.stat().st_mtime) > max_age_hours * 3600:
                        try:
                            f.unlink()
                            count += 1
                        except Exception as e:
                            logger.debug("[document_agent] Failed to unlink temp file %s: %s", f, e)
            return count

        count = await self._run_in_executor(_clean)
        self._last_generated_document = None
        logger.info("[document_agent] Cleaned up %d temp document(s)", count)
        return count

    # -------------------------------------------------------------------------
    # CANONICAL TOOLREGISTRY INTERFACES & @agent_tool DECORATORS
    # -------------------------------------------------------------------------

    @agent_tool(
        name="create_excel",
        description="Generate publication-grade Excel spreadsheets (.xlsx) with formatting, live formulas, and optional charts.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target .xlsx filename", "default": "report.xlsx"},
                "sheets_data": {"type": "object", "description": "Dictionary of sheets with headers and rows"},
                "title": {"type": "string", "description": "Spreadsheet title"},
                "columns": {"type": "array", "description": "Columns/headers for single-sheet creation"},
                "rows": {"type": "array", "description": "Rows of data"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "add_summary_row": {"type": "boolean", "description": "Auto-inject =SUM/=AVERAGE formula summary row", "default": True},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
                "metadata": {"type": "object", "description": "Document metadata (title, author)"},
            },
        },
    )
    async def _tool_create_excel(
        self,
        filename: str = "report.xlsx",
        sheets_data: Optional[Dict[str, Any]] = None,
        title: str = "",
        columns: Optional[List[Any]] = None,
        rows: Optional[List[Any]] = None,
        theme: str = "slate",
        add_summary_row: bool = True,
        destination: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to generate an Excel spreadsheet."""
        meta = dict(metadata or {})
        if title:
            meta["title"] = title

        data = sheets_data
        if not data and (columns or rows):
            data = {
                title or "Sheet1": {
                    "headers": columns or [],
                    "rows": rows or [],
                }
            }
        elif not data:
            return {
                "status": "error",
                "code": "MISSING_SPECIFICATION",
                "message": "Cannot create spreadsheet: no data, columns, or rows provided. Please specify the desired structure or content.",
            }

        is_temp = destination is None and not meta.get("persistent")
        res_path = await self.generate_excel(
            filename=filename, sheets=data, metadata=meta,
            theme=theme, add_summary_row=add_summary_row,
            destination=destination, is_temporary=is_temp,
        )
        return {
            "status": "ok",
            "format": "excel",
            "path": str(res_path),
            "filename": res_path.name,
        }

    @agent_tool(
        name="create_word",
        description="Generate executive Microsoft Word documents (.docx) with auto-updating TOC, headings, tables, cover pages, and callouts.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target .docx filename", "default": "document.docx"},
                "content_blocks": {"type": "array", "description": "List of structured content blocks (heading, body, table, etc.)"},
                "sections": {"type": "array", "description": "List of sections (heading, body, bullets)"},
                "title": {"type": "string", "description": "Document title"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "has_cover_page": {"type": "boolean", "description": "Include executive cover page", "default": False},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
                "metadata": {"type": "object", "description": "Document metadata"},
            },
        },
    )
    async def _tool_create_word(
        self,
        filename: str = "document.docx",
        content_blocks: Optional[List[Dict[str, Any]]] = None,
        sections: Optional[List[Dict[str, Any]]] = None,
        title: str = "",
        theme: str = "slate",
        has_cover_page: bool = False,
        destination: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to generate a Microsoft Word (.docx) document."""
        meta = dict(metadata or {})
        if title:
            meta["title"] = title

        blocks = content_blocks
        if not blocks and sections:
            blocks = []
            if title:
                blocks.append({"type": "title", "content": title})
            for s in sections:
                if s.get("heading") or s.get("title"):
                    blocks.append({"type": "heading", "content": s.get("heading") or s.get("title"), "level": 1})
                if s.get("body") or s.get("content"):
                    blocks.append({"type": "paragraph", "content": s.get("body") or s.get("content")})
                if s.get("bullets"):
                    for b in s["bullets"]:
                        blocks.append({"type": "bullet", "content": b})
                if s.get("table"):
                    blocks.append({"type": "table", "data": s["table"]})
        elif not blocks:
            return {
                "status": "error",
                "code": "MISSING_SPECIFICATION",
                "message": "Cannot create Word document: no content blocks or sections provided. Please specify document content.",
            }

        is_temp = destination is None and not meta.get("persistent")
        res_path = await self.generate_word(
            filename=filename, content_blocks=blocks, metadata=meta,
            theme=theme, has_cover_page=has_cover_page,
            destination=destination, is_temporary=is_temp,
        )
        return {
            "status": "ok",
            "format": "word",
            "path": str(res_path),
            "filename": res_path.name,
        }

    @agent_tool(
        name="create_document",
        description="Universal document creator for Word (.docx), PDF, or Markdown documents.",
        category="document",
    )
    async def _tool_create_document(
        self,
        filename: str = "document.docx",
        file_path: Optional[str] = None,
        title: str = "",
        subtitle: str = "",
        sections: Optional[List[Dict[str, Any]]] = None,
        content_blocks: Optional[List[Dict[str, Any]]] = None,
        doc_type: str = "docx",
        theme: str = "slate",
        has_cover_page: bool = False,
        include_cover: bool = False,
        destination: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Universal tool interface to create documents."""
        target_name = file_path or filename
        if str(target_name).endswith(".pdf") or doc_type == "pdf":
            return await self._tool_create_pdf(filename=str(target_name), title=title, theme=theme, destination=destination, **kwargs)
        return await self._tool_create_word(
            filename=str(target_name),
            content_blocks=content_blocks,
            sections=sections,
            title=title,
            theme=theme,
            has_cover_page=has_cover_page or include_cover,
            destination=destination,
            **kwargs,
        )

    @agent_tool(
        name="create_spreadsheet",
        description="Universal spreadsheet creator for Excel (.xlsx) or CSV.",
        category="document",
    )
    async def _tool_create_spreadsheet(
        self,
        filename: str = "spreadsheet.xlsx",
        file_path: Optional[str] = None,
        title: str = "",
        columns: Optional[List[Any]] = None,
        data: Optional[List[Any]] = None,
        rows: Optional[List[Any]] = None,
        sheets_data: Optional[Dict[str, Any]] = None,
        theme: str = "slate",
        include_summary: bool = True,
        add_summary_row: bool = True,
        destination: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Universal tool interface to create spreadsheets."""
        target_name = file_path or filename
        return await self._tool_create_excel(
            filename=str(target_name),
            sheets_data=sheets_data,
            title=title,
            columns=columns,
            rows=data or rows,
            theme=theme,
            add_summary_row=include_summary and add_summary_row,
            destination=destination,
            **kwargs,
        )

    @agent_tool(
        name="create_pdf",
        description="Generate enterprise-grade PDF documents compiled from Markdown or HTML with theme palettes.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target .pdf filename", "default": "document.pdf"},
                "md_filepath": {"type": "string", "description": "Path to input markdown file"},
                "html_content": {"type": "string", "description": "Raw HTML content"},
                "markdown_content": {"type": "string", "description": "Raw Markdown content"},
                "title": {"type": "string", "description": "PDF Document title"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
            },
        },
    )
    async def _tool_create_pdf(
        self,
        filename: str = "document.pdf",
        md_filepath: Optional[str] = None,
        html_content: Optional[str] = None,
        markdown_content: Optional[str] = None,
        title: str = "",
        theme: str = "slate",
        destination: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to generate an enterprise PDF from Markdown or HTML."""
        target_content = md_filepath or markdown_content or html_content
        if not target_content:
            return {
                "status": "error",
                "code": "MISSING_SPECIFICATION",
                "message": "Cannot create PDF: no markdown, HTML, or md_filepath provided. Please specify the document content.",
            }
        is_temp = destination is None
        res_path = await self.generate_pdf(target_content, filename, theme=theme, destination=destination, is_temporary=is_temp)
        return {
            "status": "ok",
            "format": "pdf",
            "path": str(res_path),
            "filename": res_path.name,
        }

    @agent_tool(
        name="create_ppt",
        description="Generate executive PowerPoint presentations (.pptx) with modern 16:9 widescreen layout.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target .pptx filename", "default": "presentation.pptx"},
                "slides_data": {"type": "array", "description": "List of slide definitions with title, body, and bullets"},
                "title": {"type": "string", "description": "Presentation title"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
            },
        },
    )
    async def _tool_create_ppt(
        self,
        filename: str = "presentation.pptx",
        slides_data: Optional[List[Dict[str, Any]]] = None,
        title: str = "",
        theme: str = "slate",
        destination: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to generate PowerPoint presentations."""
        if not slides_data:
            return {
                "status": "error",
                "code": "MISSING_SPECIFICATION",
                "message": "Cannot create PowerPoint presentation: no slide definitions or bullet content provided. Please specify slide contents.",
            }
        slides = slides_data
        is_temp = destination is None
        res_path = await self.generate_ppt(filename=filename, slides_data=slides, title=title, theme=theme, destination=destination, is_temporary=is_temp)
        return {
            "status": "ok",
            "format": "pptx",
            "path": str(res_path),
            "filename": res_path.name,
        }

    @agent_tool(
        name="parse_document",
        description="Parse and extract structured text, tables, and pages from PDF, DOCX, XLSX, CSV, TXT, MD, or JSON.",
        category="document",
        schema={
            "type": "object",
            "required": ["filepath"],
            "properties": {
                "filepath": {"type": "string", "description": "Path to document file to parse"},
            },
        },
    )
    async def _tool_parse_document(
        self,
        filepath: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to parse documents (PDF, DOCX, XLSX, CSV, TXT)."""
        if not filepath:
            return {"status": "error", "message": "filepath is required"}
        result = await self.parse_document(filepath)
        return {"status": "ok", "result": result, "filepath": filepath}

    @agent_tool(
        name="convert_document",
        description="Universal document converter: transforms files between CSV, XLSX, DOCX, PDF, HTML, and Markdown.",
        category="document",
        schema={
            "type": "object",
            "required": ["source_path", "target_format"],
            "properties": {
                "source_path": {"type": "string", "description": "Path to source document"},
                "target_format": {"type": "string", "description": "Target format: xlsx | docx | pdf | csv | html | md"},
                "output_filename": {"type": "string", "description": "Optional custom output filename"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
            },
        },
    )
    async def _tool_convert_document(
        self,
        source_path: str,
        target_format: str,
        output_filename: Optional[str] = None,
        theme: str = "slate",
        destination: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to convert documents across formats."""
        is_temp = destination is None
        res_path = await self.convert_document(
            source_path=source_path,
            target_format=target_format,
            output_filename=output_filename,
            theme=theme,
            destination=destination,
            is_temporary=is_temp,
        )
        return {
            "status": "ok",
            "format": target_format,
            "path": str(res_path),
            "filename": res_path.name,
        }

    @agent_tool(
        name="delete_document",
        description="Safely delete a generated or temporary document file.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to document file to delete (defaults to last created document)"},
            },
        },
    )
    async def _tool_delete_document(
        self,
        filepath: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to safely delete a document."""
        deleted_path = await self.delete_document(filepath)
        return {
            "status": "ok",
            "message": f"Deleted document: {deleted_path.name}",
            "path": str(deleted_path),
        }

    @agent_tool(
        name="cleanup_temp_documents",
        description="Clean up all temporary documents generated by DocumentAgent.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "max_age_hours": {"type": "number", "description": "Optional max age in hours to clean up"},
            },
        },
    )
    async def _tool_cleanup_temp_documents(
        self,
        max_age_hours: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to clean up temp documents."""
        count = await self.cleanup_temp_documents(max_age_hours=max_age_hours)
        return {
            "status": "ok",
            "message": f"Cleaned up {count} temporary document(s)",
            "deleted_count": count,
        }

    @agent_tool(
        name="create_document",
        description="Unified dispatcher to generate any document format (excel, word, pdf, pptx, report).",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target filename"},
                "doc_type": {"type": "string", "description": "Document format: excel | word | pdf | pptx | report | parse"},
                "data": {"type": "object", "description": "Document data payload"},
                "title": {"type": "string", "description": "Document title"},
                "columns": {"type": "array", "description": "Columns/headers for tabular data"},
                "rows": {"type": "array", "description": "Rows of data"},
                "sections": {"type": "array", "description": "Sections for text/report"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "add_summary_row": {"type": "boolean", "description": "Auto-inject formula summary row in Excel", "default": True},
                "has_cover_page": {"type": "boolean", "description": "Include cover page in Word", "default": False},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
                "metadata": {"type": "object", "description": "Document metadata"},
            },
        },
    )
    async def _tool_create_document(
        self,
        filename: str = "document",
        doc_type: str = "excel",
        data: Optional[Dict[str, Any]] = None,
        title: str = "",
        columns: Optional[List[Any]] = None,
        rows: Optional[List[Any]] = None,
        sections: Optional[List[Any]] = None,
        theme: str = "slate",
        add_summary_row: bool = True,
        has_cover_page: bool = False,
        destination: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Unified document creator dispatcher."""
        meta = dict(metadata or {})
        if title:
            meta["title"] = title
        if theme:
            meta["theme"] = theme
        if has_cover_page:
            meta["has_cover_page"] = True

        task = {
            "type": doc_type,
            "filename": filename,
            "data": data,
            "title": title,
            "columns": columns,
            "rows": rows,
            "sections": sections,
            "theme": theme,
            "add_summary_row": add_summary_row,
            "has_cover_page": has_cover_page,
            "destination": destination,
            "metadata": meta,
        }
        res_path = await self.create_document(task)
        return {
            "status": "ok",
            "type": doc_type,
            "path": str(res_path),
            "filename": res_path.name,
        }

    @agent_tool(
        name="generate_report",
        description="Generate structured HTML and Markdown executive reports.",
        category="document",
        schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target report filename", "default": "report.html"},
                "sections": {"type": "array", "description": "List of report sections with title and body"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
                "metadata": {"type": "object", "description": "Report metadata"},
            },
        },
    )
    async def _tool_create_report(
        self,
        filename: str = "report.html",
        sections: Optional[List[Dict[str, Any]]] = None,
        theme: str = "slate",
        destination: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tool interface to generate HTML / Markdown reports."""
        secs = sections or [{"title": "Report Summary", "content": "Report body content."}]
        is_temp = destination is None
        res_path = await self.generate_report(filename, secs, metadata, theme=theme, destination=destination, is_temporary=is_temp)
        return {
            "status": "ok",
            "format": "report",
            "path": str(res_path),
            "filename": res_path.name,
        }

    async def create_document(
        self,
        task: Optional[Union[Dict[str, Any], str]] = None,
        file_path: Union[str, Path] = "",
        filename: str = "document.docx",
        title: str = "",
        subtitle: str = "",
        sections: Optional[List[Dict[str, Any]]] = None,
        content_blocks: Optional[List[Dict[str, Any]]] = None,
        doc_type: str = "docx",
        theme: str = "slate",
        include_cover: bool = False,
        has_cover_page: bool = False,
        destination: Optional[str] = None,
        **kwargs: Any,
    ) -> Path:
        if isinstance(task, dict):
            task_dict = dict(task)
        else:
            task_dict = {
                "type": doc_type,
                "filename": str(file_path or filename or task or "document.docx"),
                "title": title,
                "subtitle": subtitle,
                "sections": sections,
                "blocks": content_blocks,
                "theme": theme,
                "has_cover_page": include_cover or has_cover_page,
                "destination": destination,
                **kwargs,
            }

        raw_type = str(task_dict.get("type", "report")).lower().strip()
        fname = str(task_dict.get("filename", "document"))
        data = task_dict.get("data")
        meta = dict(task_dict.get("metadata") or {})
        doc_theme = task_dict.get("theme") or meta.get("theme", "slate")
        add_summary_row = task_dict.get("add_summary_row", True)
        has_cover = task_dict.get("has_cover_page", False) or task_dict.get("include_cover", False)
        dest = task_dict.get("destination") or meta.get("destination")
        is_temp = dest is None and not meta.get("persistent")

        if task_dict.get("title"):
            meta["title"] = task_dict.get("title")
        if task_dict.get("subtitle"):
            meta["subtitle"] = task_dict.get("subtitle")

        if any(x in raw_type for x in ("excel", "sheet", "xlsx", "csv", "table")):
            sheets = None
            if isinstance(data, dict) and "sheets" in data:
                sheets = data["sheets"]
            elif isinstance(data, dict) and ("headers" in data or "columns" in data or "rows" in data):
                sheets = {"Sheet1": data}
            elif task_dict.get("columns") or task_dict.get("rows"):
                sheets = {
                    meta.get("title", "Sheet1"): {
                        "headers": task_dict.get("columns") or [],
                        "rows": task_dict.get("rows") or [],
                    }
                }
            elif isinstance(data, list):
                sheets = {"Sheet1": {"headers": [], "rows": data}}
            else:
                sheets = {
                    "Sheet1": {
                        "headers": ["Content"],
                        "rows": [[str(data or task_dict)]],
                    }
                }
            return await self.generate_excel(
                fname, sheets, metadata=meta, theme=doc_theme, add_summary_row=add_summary_row,
                destination=dest, is_temporary=is_temp,
            )

        elif any(x in raw_type for x in ("word", "docx", "doc")):
            blocks = None
            if isinstance(data, dict) and "blocks" in data:
                blocks = data["blocks"]
            elif task_dict.get("sections") or (isinstance(data, dict) and "sections" in data):
                secs = task_dict.get("sections") or (data.get("sections") if isinstance(data, dict) else [])
                blocks = []
                if meta.get("title"):
                    blocks.append({"type": "title", "content": meta.get("title")})
                for s in secs:
                    if s.get("heading") or s.get("title"):
                        blocks.append({"type": "heading", "content": s.get("heading") or s.get("title"), "level": 1})
                    if s.get("body") or s.get("content"):
                        blocks.append({"type": "paragraph", "content": s.get("body") or s.get("content")})
                    if s.get("callout"):
                        blocks.append({"type": "callout", "content": s["callout"]})
                    if s.get("bullets"):
                        for b in s["bullets"]:
                            blocks.append({"type": "bullet", "content": b})
                    if s.get("table"):
                        blocks.append({"type": "table", "data": s["table"]})
            elif isinstance(data, dict):
                blocks = [{"type": "paragraph", "text": str(data)}]
            elif data:
                blocks = [{"type": "paragraph", "text": str(data)}]
            else:
                blocks = [{"type": "paragraph", "text": f"Document: {meta.get('title', fname)}"}]
            return await self.generate_word(
                fname, blocks, metadata=meta, theme=doc_theme, has_cover_page=has_cover,
                destination=dest, is_temporary=is_temp,
            )

        elif raw_type == "pdf":
            md_path = task_dict.get("filepath") or task_dict.get("md_path") or task_dict.get("content") or task_dict.get("markdown") or data
            if not md_path:
                if task_dict.get("sections"):
                    md_lines = [f"# {meta.get('title', fname)}\n"]
                    for s in task_dict["sections"]:
                        h = s.get("heading") or s.get("title", "")
                        b = s.get("body") or s.get("content", "")
                        if h:
                            md_lines.append(f"## {h}\n")
                        if b:
                            md_lines.append(f"{b}\n\n")
                    md_path = "".join(md_lines)
                else:
                    md_path = f"# {meta.get('title', fname)}\n\nDocument generated by Makima."
            out_filename = fname if fname.endswith(".pdf") else f"{fname}.pdf"
            return await self.generate_pdf(md_path, out_filename, theme=doc_theme, destination=dest, is_temporary=is_temp)

        elif any(x in raw_type for x in ("ppt", "pptx", "slide", "presentation", "powerpoint")):
            slides = task_dict.get("slides") or (data.get("slides") if isinstance(data, dict) else None)
            if not slides and task_dict.get("sections"):
                slides = []
                for s in task_dict["sections"]:
                    slides.append({
                        "title": s.get("heading") or s.get("title", "Slide"),
                        "content": s.get("body") or s.get("content", ""),
                        "bullets": s.get("bullets", []),
                    })
            elif not slides:
                slides = [{"title": meta.get("title", fname), "content": str(data or "Executive Presentation")}]
            return await self.generate_ppt(
                fname, slides, title=meta.get("title", fname), metadata=meta, theme=doc_theme,
                destination=dest, is_temporary=is_temp,
            )

        elif raw_type == "parse":
            filepath = task_dict.get("filepath") or task_dict.get("filename") or (data.get("filepath") if isinstance(data, dict) else str(data))
            result = await self.parse_document(filepath)
            target_dir = self._resolve_target_dir(dest, is_temporary=is_temp)
            out_path = target_dir / f"{Path(filepath).stem}_parsed.json"
            def _write_json():
                out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            await self._run_in_executor(_write_json)
            self._last_generated_document = out_path
            return out_path

        elif raw_type == "delete":
            filepath = task.get("filepath") or task.get("filename") or (data.get("filepath") if isinstance(data, dict) else None)
            return await self.delete_document(filepath)

        elif raw_type == "cleanup":
            max_age = task.get("max_age_hours")
            count = await self.cleanup_temp_documents(max_age_hours=max_age)
            dummy_path = self._temp_dir / f"cleanup_{count}_files"
            return dummy_path

        else:
            sections = None
            if isinstance(data, dict) and "sections" in data:
                sections = data["sections"]
            elif task.get("sections"):
                sections = task["sections"]
            else:
                sections = [{"title": meta.get("title", "Summary Report"), "content": str(data or task)}]
            return await self.generate_report(filename, sections, metadata=meta, theme=theme, destination=destination, is_temporary=is_temp)

    # -------------------------------------------------------------------------
    # MAIN BASE AGENT EXECUTION
    # -------------------------------------------------------------------------
    async def execute(
        self,
        task_id: str,
        message: str,
        context: Dict[str, Any],
        entities: Dict[str, Any],
    ) -> str:
        """Standard Makima BaseAgent execution entrypoint."""
        self._reset_state()
        try:
            workspace_dir = context.get("workspace_dir", "./makima_workspace")
            self.output_dir = Path(workspace_dir) / "output"
            self._temp_dir = self.output_dir / "temp"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._temp_dir.mkdir(parents=True, exist_ok=True)

            msg_l = (message or "").lower().strip()

            # Direct Deletion & Cleanup Intent Detection
            is_delete_cmd = any(k in msg_l for k in ("delete", "remove", "hata", "hatao", "erase", "trash", "clean"))
            is_doc_ref = any(k in msg_l for k in ("file", "doc", "document", "sheet", "excel", "pdf", "word", "it", "ye", "yeh", "this", "temp", "temporary"))

            if is_delete_cmd and is_doc_ref:
                if any(k in msg_l for k in ("cleanup", "all temp", "saare temp", "temp files", "temp documents")):
                    count = await self.cleanup_temp_documents()
                    return f"🗑️ **Cleaned up {count} temporary document(s) successfully.**"
                else:
                    try:
                        # Check if a specific filename is mentioned in message
                        file_match = re.search(r"[\w\-]+\.(xlsx|docx|pdf|pptx|html|md|csv|txt|json)", message, re.IGNORECASE)
                        del_target = file_match.group(0) if file_match else None
                        deleted_path = await self.delete_document(del_target)
                        return f"🗑️ **Document deleted successfully:** `{deleted_path.name}`"
                    except Exception as del_err:
                        return f"⚠️ Could not delete document: {del_err}"

            # ── OpenAI Agents SDK Runner Execution ────────────────────────────
            try:
                final_out = await self.run_sdk_execution(
                    task_id=task_id,
                    message=message,
                    context=context,
                    max_turns=6,
                    task_type="general",
                    input_guardrails=[],
                    output_guardrails=[],
                )
                self._partial_result = final_out
                return final_out
            except Exception as sdk_exc:
                logger.warning("[document_agent] SDK Runner encountered exception, falling back: %s", sdk_exc)

            # Persistent Destination Intent Detection
            dest = None
            if "desktop" in msg_l:
                dest = "desktop"
            elif "downloads" in msg_l or "download" in msg_l:
                dest = "downloads"
            elif "documents" in msg_l or "my documents" in msg_l:
                dest = "documents"

            # ── P1 Bridge 4B: Consume canonical AgentTask parameters directly ─
            agent_task = getattr(self, "_current_agent_task", None) or context.get("agent_task")
            task_dict: Optional[dict[str, Any]] = None

            if agent_task and hasattr(agent_task, "parameters") and agent_task.parameters:
                params = dict(agent_task.parameters)
                raw_type = str(
                    params.get("type") or params.get("document_type") or params.get("format")
                    or agent_task.operation or "excel"
                ).lower()

                if any(x in raw_type for x in ("excel", "sheet", "xlsx", "csv", "table")):
                    doc_type = "excel"
                elif any(x in raw_type for x in ("word", "docx", "doc")):
                    doc_type = "word"
                elif raw_type == "pdf":
                    doc_type = "pdf"
                elif any(x in raw_type for x in ("ppt", "pptx", "slide", "presentation", "powerpoint")):
                    doc_type = "presentation"
                elif any(x in raw_type for x in ("parse", "read", "extract")):
                    doc_type = "parse"
                elif any(x in raw_type for x in ("delete", "remove")):
                    doc_type = "delete"
                elif any(x in raw_type for x in ("clean", "cleanup")):
                    doc_type = "cleanup"
                elif any(x in raw_type for x in ("convert", "transform")):
                    doc_type = "convert"
                elif any(x in raw_type for x in ("report", "html", "md", "markdown")):
                    doc_type = "report"
                else:
                    # Natural language requests (report, template, document) → Word by default.
                    # Excel is only chosen when the user explicitly mentions spreadsheet/xlsx/csv/table.
                    doc_type = "word"

                task_dest = params.get("destination") or dest
                task_dict = {
                    "type": doc_type,
                    "filename": (
                        params.get("filename") or params.get("file_name") or params.get("name")
                        or agent_task.target_entity or f"document_{task_id[:8]}"
                    ),
                    "title": params.get("title") or agent_task.goal or "Document Report",
                    "columns": params.get("columns") or [],
                    "rows": params.get("rows") or [],
                    "sections": params.get("sections") or [],
                    "theme": params.get("theme") or "slate",
                    "add_summary_row": params.get("add_summary_row", True),
                    "has_cover_page": params.get("has_cover_page", False),
                    "destination": task_dest,
                    "source_path": params.get("source_path"),
                    "target_format": params.get("target_format"),
                    "data": params.get("data") or {},
                    "metadata": params.get("metadata") or {},
                }
                logger.info(
                    "[document_agent] P1-4B: Consumed structured AgentTask parameters directly (type=%s, filename=%s)",
                    doc_type, task_dict["filename"],
                )

            # Secondary fallback: context or entities dictionary
            if not task_dict:
                task_dict = context.get("document_task") or entities.get("document_task")

            # Tertiary fallback: parse structured JSON directly from message
            if not task_dict and self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
                task_dict = self.ai_handler.try_parse_json(message)

            # Quaternary fallback: ask LLM to structure unstructured natural language
            if not task_dict or not isinstance(task_dict, dict) or "type" not in task_dict:
                # Inject real conversation context — the fake-inventory bug
                # happened because this prompt generated data with zero grounding.
                conv_digest = ""
                turns = context.get("history") if isinstance(context, dict) else None
                if isinstance(turns, list):
                    recent = [
                        f"{str(t.get('role', 'user'))}: {str(t.get('content') or t.get('message') or '')[:300]}"
                        for t in turns[-8:] if isinstance(t, dict)
                    ]
                    if recent:
                        conv_digest = "\n\n[CONVERSATION CONTEXT]\n" + "\n".join(recent)
                sys_prompt = (
                    "You are Makima's DocumentAgent. Convert the user request into a JSON object with keys: "
                    "'type' (excel, word, pdf, pptx, report, parse, convert, delete, cleanup), 'filename', 'title', 'columns', 'rows', 'sections', "
                    "'theme' (slate, emerald, crimson, nordic), 'add_summary_row' (boolean), 'destination' (desktop, downloads, documents, or null), and 'data'. "
                    "Respond ONLY with valid JSON.\n\n"
                    "TYPE SELECTION RULES (CRITICAL):\n"
                    "- Use 'word' for: Word document, .docx, report, template, letter, proposal, project report, any narrative/structured document. DEFAULT for ambiguous documents.\n"
                    "- Use 'excel' ONLY when user explicitly mentions: spreadsheet, Excel, .xlsx, table with rows & columns, CSV, data grid.\n"
                    "- Use 'pdf' for: PDF, printable document.\n"
                    "- Use 'pptx' for: PowerPoint, slides, presentation.\n"
                    "Examples: 'Word mein report banao' → type='word'; 'project template banao' → type='word'; 'Excel sheet banao' → type='excel'.\n\n"
                    "DATA INTEGRITY (ABSOLUTE RULE): Use ONLY facts that appear in the USER REQUEST "
                    "or in the [CONVERSATION CONTEXT] block. NEVER invent product names, model numbers, "
                    "serial numbers, purchase dates, prices, statistics, or any row values that were "
                    "not stated anywhere in the conversation. Generic template examples (Dell XPS, HP "
                    "Pavilion etc.) are FORBIDDEN unless the user typed them. If the user references "
                    "earlier conversation data, pull it from [CONVERSATION CONTEXT]. If insufficient "
                    "real data exists, return 'rows': [] and add under 'data': {\"insufficient_data\": "
                    "\"<one-line question asking the user for the actual values>\"}."
                    + conv_digest
                )
                messages = self._build_messages(message, context, extra_system=sys_prompt)
                raw_json = await self._llm_call(messages, task="general", require_json=True)
                task_dict = (
                    self.ai_handler.try_parse_json(raw_json)
                    if (self.ai_handler and hasattr(self.ai_handler, "try_parse_json"))
                    else None
                )

            if not task_dict or "type" not in task_dict:
                return "⚠️ Error: Could not determine document schema from request."

            if dest and not task_dict.get("destination"):
                task_dict["destination"] = dest

            # Handle direct conversion if requested
            if task_dict.get("type") == "convert" and task_dict.get("source_path"):
                result_path = await self.convert_document(
                    source_path=task_dict["source_path"],
                    target_format=task_dict.get("target_format", "xlsx"),
                    output_filename=task_dict.get("filename"),
                    theme=task_dict.get("theme", "slate"),
                    destination=task_dict.get("destination"),
                    is_temporary=(task_dict.get("destination") is None),
                )
            elif task_dict.get("type") == "delete":
                deleted_path = await self.delete_document(task_dict.get("filename"))
                return f"🗑️ **Document deleted successfully:** `{deleted_path.name}`"
            elif task_dict.get("type") == "cleanup":
                count = await self.cleanup_temp_documents(task_dict.get("max_age_hours"))
                return f"🗑️ **Cleaned up {count} temporary document(s) successfully.**"
            else:
                result_path = await self.create_document(task_dict)

            abs_path = result_path.resolve()
            ext = abs_path.suffix.lower()

            doc_type_name = {
                ".xlsx": "Excel Spreadsheet",
                ".xls": "Excel Spreadsheet",
                ".docx": "Word Document",
                ".doc": "Word Document",
                ".pdf": "PDF Document",
                ".pptx": "PowerPoint Presentation",
                ".ppt": "PowerPoint Presentation",
                ".html": "Executive HTML Report",
                ".md": "Markdown Report",
                ".json": "Parsed Document JSON",
                ".csv": "CSV Data File",
            }.get(ext, "Document")

            doc_icon = {
                ".xlsx": "📊",
                ".xls": "📊",
                ".docx": "📝",
                ".doc": "📝",
                ".pdf": "📄",
                ".pptx": "📊",
                ".ppt": "📊",
                ".html": "📑",
                ".md": "📑",
                ".json": "🔍",
                ".csv": "📈",
            }.get(ext, "📄")

            theme_used = task_dict.get("theme", "slate").title()
            is_temp_file = "temp" in str(abs_path).lower()
            storage_badge = "Ephemeral Temp" if is_temp_file else f"Saved to {abs_path.parent.name}"

            return (
                f"{doc_icon} **{doc_type_name} generated successfully!** (Theme: *{theme_used}* | *{storage_badge}*)\n\n"
                f"📄 **Click to view/download:** [{abs_path.name}](file:///{abs_path.as_posix()})"
            )

        except FileNotFoundError as e:
            logger.warning("[document_agent] Document not found: %s", e)
            return f"⚠️ Error processing document request: {e}"
        except Exception as e:
            logger.error("[document_agent] DocumentAgent execution failed: %s", e, exc_info=True)
            return f"⚠️ Error processing document request: {e}"

    def __del__(self):
        """Properly shut down the ThreadPoolExecutor without blocking."""
        if hasattr(self, "_executor") and self._executor:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass
