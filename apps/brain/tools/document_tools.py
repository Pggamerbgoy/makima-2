"""
Makima OS — Standalone Document Capability Tools
Location: apps/brain/tools/document_tools.py

Headless document engineering, parsing, formatting, and conversion engine:
- create_excel (openpyxl + theme palettes + live formulas + KPI summary)
- create_word (python-docx + executive cover page + TOC + styled tables)
- create_pdf (HTML/Markdown to PDF via Playwright with fallback)
- create_powerpoint (python-pptx 16:9 widescreen layout + slide decks)
- convert_document (universal cross-format conversion)
- parse_document (PDF, DOCX, XLSX, CSV, TXT, MD, JSON, PPTX)
- generate_report (executive Markdown/HTML reports)
- delete_document & cleanup_temp_documents
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
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..core.known_folders import resolve_known_folder

logger = logging.getLogger("makima.tools.document")

# ---------------------------------------------------------------------------
# Optional Third-Party Imports (Zero-Crash Guarantee)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Theme Palettes & Design Tokens
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ThemePalette:
    name: str
    primary_hex: str
    primary_rgb: tuple[int, int, int]
    accent_hex: str
    accent_rgb: tuple[int, int, int]
    zebra_hex: str
    text_hex: str
    text_rgb: tuple[int, int, int]
    header_text_hex: str
    header_text_rgb: tuple[int, int, int]
    border_hex: str
    kpi_bg_hex: str


THEMES: dict[str, ThemePalette] = {
    "slate": ThemePalette(
        name="Midnight Slate",
        primary_hex="1E293B",
        primary_rgb=(30, 41, 59),
        accent_hex="2563EB",
        accent_rgb=(37, 99, 235),
        zebra_hex="F1F5F9",
        text_hex="334155",
        text_rgb=(51, 65, 85),
        header_text_hex="F8FAFC",
        header_text_rgb=(248, 250, 252),
        border_hex="CBD5E1",
        kpi_bg_hex="EFF6FF",
    ),
    "emerald": ThemePalette(
        name="Emerald Finance",
        primary_hex="064E3B",
        primary_rgb=(6, 78, 59),
        accent_hex="059669",
        accent_rgb=(5, 150, 105),
        zebra_hex="F0FDF4",
        text_hex="1E293B",
        text_rgb=(30, 41, 59),
        header_text_hex="F8FAFC",
        header_text_rgb=(248, 250, 252),
        border_hex="A7F3D0",
        kpi_bg_hex="ECFDF5",
    ),
    "crimson": ThemePalette(
        name="Crimson Executive",
        primary_hex="881337",
        primary_rgb=(136, 19, 55),
        accent_hex="E11D48",
        accent_rgb=(225, 29, 72),
        zebra_hex="FFF1F2",
        text_hex="27272A",
        text_rgb=(39, 39, 42),
        header_text_hex="FFFFFF",
        header_text_rgb=(255, 255, 255),
        border_hex="FECDD3",
        kpi_bg_hex="FFE4E6",
    ),
    "nordic": ThemePalette(
        name="Nordic Frost",
        primary_hex="18181B",
        primary_rgb=(24, 24, 27),
        accent_hex="0284C7",
        accent_rgb=(2, 132, 199),
        zebra_hex="F4F4F5",
        text_hex="3F3F46",
        text_rgb=(63, 63, 70),
        header_text_hex="FAFAFA",
        header_text_rgb=(250, 250, 250),
        border_hex="E4E4E7",
        kpi_bg_hex="F0F9FF",
    ),
}


def get_theme_palette(theme_name: Optional[str] = None) -> ThemePalette:
    """Resolve theme name to ThemePalette instance."""
    if not theme_name:
        return THEMES["slate"]
    key = str(theme_name).lower().strip()
    if key in ("emerald", "finance", "green", "money", "invest"):
        return THEMES["emerald"]
    if key in ("crimson", "executive", "red", "legal", "burgundy"):
        return THEMES["crimson"]
    if key in ("nordic", "frost", "minimal", "dark", "zinc", "cyan"):
        return THEMES["nordic"]
    return THEMES.get(key, THEMES["slate"])


# ---------------------------------------------------------------------------
# Global Shared Executor & Temp Folder Management
# ---------------------------------------------------------------------------
_doc_executor: Optional[ThreadPoolExecutor] = None
_DEFAULT_TEMP_DIR = Path(tempfile.gettempdir()) / "makima_documents"


def _get_executor() -> ThreadPoolExecutor:
    global _doc_executor
    if _doc_executor is None:
        _doc_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="makima_doc_worker")
    return _doc_executor


async def _run_in_executor(func: Callable[..., Any], *args: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_executor(), func, *args)


def resolve_target_dir(destination: Optional[str] = None, is_temporary: bool = False) -> Path:
    """
    Resolves target output directory.
    Uses known folder mapping (desktop, downloads, documents) or user home / custom path.
    """
    if is_temporary or destination == "temp":
        _DEFAULT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        return _DEFAULT_TEMP_DIR

    if not destination:
        # Default permanent path is Makima Generated Docs or Desktop
        known = resolve_known_folder("desktop")
        if known:
            p = Path(known)
            p.mkdir(parents=True, exist_ok=True)
            return p
        p = Path.home() / "Desktop"
        p.mkdir(parents=True, exist_ok=True)
        return p

    dest_clean = str(destination).strip().lower()
    user_home = Path.home()

    # Known-folder resolution (OneDrive-aware)
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
        target.mkdir(parents=True, exist_ok=True)
        return target

    p = Path(os.path.expanduser(str(destination).strip("'\"")))
    if not p.is_absolute():
        p = user_home / p
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# HTML / Markdown Conversion Helpers
# ---------------------------------------------------------------------------
def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_inline(text: str) -> str:
    escaped = _escape_html(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def _simple_markdown_to_html(md_text: str) -> str:
    lines = md_text.splitlines()
    html_lines: list[str] = []
    in_list = False
    in_table = False
    table_header_done = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_table:
                html_lines.append("</table>")
                in_table = False
                table_header_done = False
            continue

        if stripped.startswith("# "):
            html_lines.append(f"<h1>{_escape_html(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_escape_html(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{_escape_html(stripped[4:])}</h3>")
        elif stripped.startswith("#### "):
            html_lines.append(f"<h4>{_escape_html(stripped[5:])}</h4>")
        elif stripped.startswith("---") or stripped.startswith("***"):
            html_lines.append("<hr/>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_format_inline(stripped[2:])}</li>")
        elif stripped.startswith("|") and stripped.endswith("|"):
            if "---" in stripped:
                table_header_done = True
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            tag = "th" if not table_header_done else "td"
            row_html = "".join(f"<{tag}>{_format_inline(c)}</{tag}>" for c in cells)
            html_lines.append(f"<tr>{row_html}</tr>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_table:
                html_lines.append("</table>")
                in_table = False
                table_header_done = False
            html_lines.append(f"<p>{_format_inline(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</table>")

    return "\n".join(html_lines)


# =============================================================================
# 1. EXCEL GENERATION (.xlsx)
# =============================================================================
async def create_excel(
    filename: str = "report.xlsx",
    sheets_data: Optional[Dict[str, Any]] = None,
    title: str = "",
    columns: Optional[List[Any]] = None,
    rows: Optional[List[Any]] = None,
    theme: str = "slate",
    add_summary_row: bool = True,
    destination: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    is_temporary: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Generate publication-grade Excel spreadsheets (.xlsx) with theme styling,
    auto-filtering, formulas (=SUM/=AVERAGE), and auto column width.
    """
    if not _HAS_OPENPYXL:
        raise RuntimeError("Excel generation requires 'openpyxl'.")

    meta = dict(metadata or {})
    if title:
        meta["title"] = title

    palette = get_theme_palette(theme or meta.get("theme"))
    base_name = Path(filename).name
    if not base_name.endswith(".xlsx"):
        base_name += ".xlsx"

    target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
    filepath = target_dir / base_name

    # Normalize sheet structure
    data = sheets_data
    if not data and (columns or rows):
        sheet_key = title or "Sheet1"
        data = {
            sheet_key: {
                "headers": columns or [],
                "rows": rows or [],
            }
        }
    elif not data:
        data = {
            "Sheet1": {
                "headers": ["Item", "Value"],
                "rows": [["Status", "Generated Successfully"]],
            }
        }

    def _build_excel() -> Path:
        wb = Workbook()
        wb.remove(wb.active)  # Remove default blank sheet

        dict_sheets: dict[str, dict[str, Any]] = {}
        if isinstance(data, list):
            for idx, s in enumerate(data):
                if isinstance(s, dict):
                    s_name = s.get("sheet_name") or s.get("name") or f"Sheet{idx+1}"
                    dict_sheets[s_name] = s
        elif isinstance(data, dict):
            if "headers" in data or "columns" in data or "rows" in data:
                dict_sheets[title or "Sheet1"] = data
            else:
                dict_sheets = data

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
        )

        for sheet_name, sheet_content in dict_sheets.items():
            ws = wb.create_sheet(title=str(sheet_name)[:31])
            ws.views.sheetView[0].showGridLines = True

            headers = sheet_content.get("headers") or sheet_content.get("columns") or []
            data_rows = sheet_content.get("rows") or sheet_content.get("data") or []

            current_row = 1
            header_row_idx = current_row
            num_cols = len(headers)

            if headers:
                for col_idx, header in enumerate(headers, start=1):
                    cell = ws.cell(row=header_row_idx, column=col_idx, value=str(header))
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                    cell.border = thin_border
                ws.row_dimensions[header_row_idx].height = 24
                current_row += 1

            start_data_row = current_row
            for r_idx, row_values in enumerate(data_rows):
                row_num = current_row + r_idx
                ws.row_dimensions[row_num].height = 20
                is_even = (r_idx % 2 == 1)

                if isinstance(row_values, dict):
                    row_list = [row_values.get(h, "") for h in headers]
                elif isinstance(row_values, (list, tuple)):
                    row_list = list(row_values)
                else:
                    row_list = [row_values]

                for col_idx, val in enumerate(row_list, start=1):
                    cell = ws.cell(row=row_num, column=col_idx, value=val)
                    cell.font = body_font
                    cell.border = thin_border
                    if is_even:
                        cell.fill = zebra_fill

                    if isinstance(val, (int, float)):
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                        if isinstance(val, float):
                            cell.number_format = "#,##0.00"
                        else:
                            cell.number_format = "#,##0"
                    else:
                        cell.alignment = Alignment(horizontal="left", vertical="center")

            end_data_row = current_row + len(data_rows) - 1

            # Auto-inject Smart Formula Summary Row if enabled
            if add_summary_row and len(data_rows) > 0 and num_cols > 0:
                summary_row_num = end_data_row + 1
                ws.row_dimensions[summary_row_num].height = 22
                ws.cell(row=summary_row_num, column=1, value="Total / Summary").font = summary_font
                ws.cell(row=summary_row_num, column=1).fill = summary_fill
                ws.cell(row=summary_row_num, column=1).border = double_bottom_border

                for c_idx in range(1, num_cols + 1):
                    col_letter = get_column_letter(c_idx)
                    cell = ws.cell(row=summary_row_num, column=c_idx)
                    cell.fill = summary_fill
                    cell.border = double_bottom_border

                    # Check if column is numeric
                    numeric_count = sum(
                        1 for r in data_rows
                        if isinstance(r, (list, tuple)) and len(r) >= c_idx and isinstance(r[c_idx - 1], (int, float))
                    )
                    if numeric_count > len(data_rows) * 0.5:
                        cell.value = f"=SUM({col_letter}{start_data_row}:{col_letter}{end_data_row})"
                        cell.font = summary_font
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                        cell.number_format = "#,##0.00"

            # Enable Auto-Filter
            if headers:
                last_col_letter = get_column_letter(num_cols)
                ws.auto_filter.ref = f"A{header_row_idx}:{last_col_letter}{max(end_data_row, header_row_idx)}"

            # Auto column width adjustment
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    val_str = str(cell.value or "")
                    if len(val_str) > max_len:
                        max_len = len(val_str)
                ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        filepath.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(filepath))
        return filepath

    res_path = await _run_in_executor(_build_excel)
    return {
        "status": "ok",
        "format": "excel",
        "path": str(res_path),
        "filename": res_path.name,
    }


# =============================================================================
# 2. WORD DOCUMENT GENERATION (.docx)
# =============================================================================
async def create_word(
    filename: str = "document.docx",
    content_blocks: Optional[List[Dict[str, Any]]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    title: str = "",
    theme: str = "slate",
    has_cover_page: bool = False,
    destination: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    is_temporary: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Generate publication-ready Microsoft Word documents (.docx) with auto-updating
    TOC, headings, styled tables, cover pages, and callouts.
    """
    if not _HAS_DOCX:
        raise RuntimeError("Word generation requires 'python-docx'.")

    meta = dict(metadata or {})
    if title:
        meta["title"] = title

    palette = get_theme_palette(theme or meta.get("theme"))
    base_name = Path(filename).name
    if not base_name.endswith(".docx"):
        base_name += ".docx"

    target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
    filepath = target_dir / base_name

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
        blocks = [
            {"type": "heading", "text": title or "Document Title", "level": 1},
            {"type": "paragraph", "text": "Document generated by Makima Document Engine."},
        ]

    def _build_word() -> Path:
        doc = Document()

        try:
            settings_element = doc.settings.element
            update_fields = parse_xml(f'<w:updateFields {nsdecls("w")} w:val="true"/>')
            settings_element.append(update_fields)
        except Exception:
            pass

        for section in doc.sections:
            section.top_margin = Cm(2.54)
            section.bottom_margin = Cm(2.54)
            section.left_margin = Cm(2.54)
            section.right_margin = Cm(2.54)

            header = section.header
            if header.paragraphs:
                hp = header.paragraphs[0]
                hp.text = meta.get("title", "Makima OS Executive Report")
                hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                if hp.runs:
                    hp.runs[0].font.size = Pt(9)
                    hp.runs[0].font.color.rgb = RGBColor(100, 116, 139)

            footer = section.footer
            if footer.paragraphs:
                fp = footer.paragraphs[0]
                fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = fp.add_run()
                run._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>'))
                run2 = fp.add_run()
                run2._r.append(parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>'))
                run3 = fp.add_run()
                run3._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))

        if has_cover_page or meta.get("has_cover_page"):
            p_spacer = doc.add_paragraph()
            p_spacer.paragraph_format.space_before = Pt(72)

            p_title = doc.add_paragraph()
            p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run_title = p_title.add_run(meta.get("title", base_name))
            run_title.font.name = "Segoe UI"
            run_title.font.size = Pt(30)
            run_title.font.bold = True
            run_title.font.color.rgb = RGBColor(*palette.primary_rgb)

            p_sub = doc.add_paragraph()
            run_sub = p_sub.add_run(meta.get("subtitle", "Executive Strategic Briefing"))
            run_sub.font.name = "Segoe UI"
            run_sub.font.size = Pt(14)
            run_sub.font.color.rgb = RGBColor(*palette.accent_rgb)

            p_meta = doc.add_paragraph()
            p_meta.paragraph_format.space_before = Pt(140)
            run_meta = p_meta.add_run(f"Author: Makima OS | Date: {meta.get('date', time.strftime('%Y-%m-%d'))}\nClassification: Confidential & Proprietary")
            run_meta.font.size = Pt(10)
            run_meta.font.color.rgb = RGBColor(100, 116, 139)

            doc.add_page_break()

        for block in blocks:
            b_type = str(block.get("type", "body")).lower()
            content = block.get("content") or block.get("text", "")

            if b_type == "title":
                p = doc.add_heading(str(content), level=0)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in p.runs:
                    run.font.color.rgb = RGBColor(*palette.primary_rgb)

            elif b_type == "heading":
                level = min(max(int(block.get("level", 1)), 1), 3)
                p = doc.add_heading(str(content), level=level)
                for run in p.runs:
                    run.font.color.rgb = RGBColor(*palette.primary_rgb)

            elif b_type == "paragraph" or b_type == "body":
                p = doc.add_paragraph(str(content))
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_after = Pt(8)

            elif b_type == "bullet" or b_type == "bullet_point":
                doc.add_paragraph(str(content), style="List Bullet")

            elif b_type == "callout" or b_type == "quote":
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.5)
                p.paragraph_format.right_indent = Inches(0.5)
                run = p.add_run(f"💡 {content}")
                run.italic = True
                run.font.color.rgb = RGBColor(*palette.accent_rgb)

            elif b_type == "table":
                t_data = block.get("data") or {}
                headers = t_data.get("headers") or block.get("headers") or []
                rows_data = t_data.get("rows") or block.get("rows") or []

                if headers or rows_data:
                    col_count = len(headers) if headers else (len(rows_data[0]) if rows_data else 1)
                    table = doc.add_table(rows=0, cols=col_count)
                    table.alignment = WD_TABLE_ALIGNMENT.CENTER

                    if headers:
                        hdr_cells = table.add_row().cells
                        for c_idx, h in enumerate(headers):
                            hdr_cells[c_idx].text = str(h)
                            for run in hdr_cells[c_idx].paragraphs[0].runs:
                                run.font.bold = True
                                run.font.color.rgb = RGBColor(255, 255, 255)
                            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{palette.primary_hex}"/>')
                            hdr_cells[c_idx]._tc.get_or_add_tcPr().append(shd)

                    for r_idx, r in enumerate(rows_data):
                        row_cells = table.add_row().cells
                        for c_idx, cell_val in enumerate(r):
                            if c_idx < len(row_cells):
                                row_cells[c_idx].text = str(cell_val)
                                if r_idx % 2 == 1:
                                    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{palette.zebra_hex}"/>')
                                    row_cells[c_idx]._tc.get_or_add_tcPr().append(shd)

        filepath.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(filepath))
        return filepath

    res_path = await _run_in_executor(_build_word)
    return {
        "status": "ok",
        "format": "word",
        "path": str(res_path),
        "filename": res_path.name,
    }


# =============================================================================
# 3. PDF COMPILATION (.pdf)
# =============================================================================
async def create_pdf(
    filename: str = "document.pdf",
    md_filepath: Optional[str] = None,
    html_content: Optional[str] = None,
    markdown_content: Optional[str] = None,
    title: str = "",
    theme: str = "slate",
    destination: Optional[str] = None,
    is_temporary: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Generate enterprise-grade PDF documents compiled from Markdown or HTML with
    theme palettes via Playwright (or fallback HTML).
    """
    palette = get_theme_palette(theme)
    base_name = Path(filename).name
    if not base_name.endswith(".pdf"):
        base_name += ".pdf"

    target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
    out_path = target_dir / base_name
    title_stem = out_path.stem

    # Extract source content
    md_text = ""
    if md_filepath and Path(md_filepath).exists():
        md_text = Path(md_filepath).read_text(encoding="utf-8", errors="replace")
    elif markdown_content:
        md_text = markdown_content
    elif html_content:
        md_text = html_content
    else:
        md_text = f"# {title or 'Document'}\nGenerated PDF content."

    json_md = json.dumps(md_text)
    html_template = rf"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title_stem}</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
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
        hr {{ border: none; border-top: 1px solid #{palette.border_hex}; margin: 24px 0; }}
    </style>
</head>
<body>
    <div id="content"></div>
    <script>
        const rawMarkdown = {json_md};
        if (typeof marked !== 'undefined' && marked.parse) {{
            document.getElementById('content').innerHTML = marked.parse(rawMarkdown);
        }} else {{
            document.getElementById('content').innerHTML = "<pre>" + rawMarkdown + "</pre>";
        }}
    </script>
</body>
</html>"""

    temp_html = _DEFAULT_TEMP_DIR / f"temp_{title_stem}.html"
    temp_html.parent.mkdir(parents=True, exist_ok=True)
    temp_html.write_text(html_template, encoding="utf-8")

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(f"file:///{temp_html.as_posix()}", wait_until="domcontentloaded")
                await asyncio.sleep(0.5)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                await page.pdf(
                    path=str(out_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("PDF generation via Playwright failed (%s) — falling back to HTML report", e)
        fallback_html = target_dir / f"{title_stem}.html"
        fallback_html.write_text(html_template, encoding="utf-8")
        return {
            "status": "ok",
            "format": "html_fallback",
            "path": str(fallback_html),
            "filename": fallback_html.name,
        }
    finally:
        if temp_html.exists():
            try:
                temp_html.unlink()
            except Exception:
                pass

    return {
        "status": "ok",
        "format": "pdf",
        "path": str(out_path),
        "filename": out_path.name,
    }


# =============================================================================
# 4. POWERPOINT PRESENTATION GENERATION (.pptx)
# =============================================================================
async def create_powerpoint(
    filename: str = "presentation.pptx",
    slides_data: Optional[List[Dict[str, Any]]] = None,
    title: str = "",
    theme: str = "slate",
    destination: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    is_temporary: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Generate executive PowerPoint presentations (.pptx) with modern 16:9 widescreen layout.
    """
    palette = get_theme_palette(theme)
    base_name = Path(filename).name
    if not base_name.endswith(".pptx"):
        base_name += ".pptx"

    target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
    filepath = target_dir / base_name

    slides = slides_data or [
        {
            "title": title or "Executive Overview",
            "content": "Presentation generated by Makima Document Engine.",
            "bullets": ["Key milestone 1", "Strategic initiative 2", "Projected outcome 3"],
        }
    ]

    if not _HAS_PPTX:
        logger.warning("python-pptx not installed — compiling HTML slide deck fallback")
        html_slides = target_dir / f"{filepath.stem}_slides.html"
        slides_html = []
        for idx, s in enumerate(slides, 1):
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
    <title>{title or base_name}</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0F172A; margin: 0; padding: 2rem; max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #F8FAFC; text-align: center; margin-bottom: 2rem; }}
    </style>
</head>
<body>
    <h1>{title or base_name}</h1>
    {''.join(slides_html)}
</body>
</html>"""
        html_slides.write_text(deck_template, encoding="utf-8")
        return {
            "status": "ok",
            "format": "html_slides_fallback",
            "path": str(html_slides),
            "filename": html_slides.name,
        }

    def _build_ppt() -> Path:
        prs = Presentation()
        prs.slide_width = PptxInches(13.333)
        prs.slide_height = PptxInches(7.5)

        title_layout = prs.slide_layouts[0]
        slide1 = prs.slides.add_slide(title_layout)
        slide_title = slide1.shapes.title
        slide_subtitle = slide1.placeholders[1] if len(slide1.placeholders) > 1 else None

        if slide_title:
            slide_title.text = title or base_name
            for p in slide_title.text_frame.paragraphs:
                p.font.name = "Segoe UI"
                p.font.size = PptxPt(40)
                p.font.bold = True
                p.font.color.rgb = PptxRGBColor(*palette.primary_rgb)

        if slide_subtitle:
            slide_subtitle.text = "Makima OS Presentation"
            for p in slide_subtitle.text_frame.paragraphs:
                p.font.name = "Segoe UI"
                p.font.size = PptxPt(18)
                p.font.color.rgb = PptxRGBColor(*palette.accent_rgb)

        content_layout = prs.slide_layouts[1]
        for s_info in slides:
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

        filepath.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(filepath))
        return filepath

    res_path = await _run_in_executor(_build_ppt)
    return {
        "status": "ok",
        "format": "pptx",
        "path": str(res_path),
        "filename": res_path.name,
    }


# Alias for backward compatibility
create_ppt = create_powerpoint


# =============================================================================
# 5. UNIVERSAL DOCUMENT PARSING (PDF, DOCX, XLSX, CSV, TXT, MD, JSON, PPTX)
# =============================================================================
async def parse_document(
    filepath: Union[str, Path] = "",
    file_path: Union[str, Path] = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Parse and extract structured text, tables, and sheets from PDF, DOCX, XLSX, CSV, TXT, MD, JSON, or PPTX.
    """
    target = filepath or file_path or kwargs.get("path") or ""
    path = Path(target)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")

    ext = path.suffix.lower()

    if ext == ".pdf":
        if not _HAS_PYPDF:
            raise RuntimeError("PDF parsing requires 'pypdf'.")
        def _extract_pdf():
            reader = PdfReader(str(path))
            text_pages = [{"page": i + 1, "text": p.extract_text() or ""} for i, p in enumerate(reader.pages)]
            return {"type": "pdf", "pages": len(reader.pages), "content": text_pages}
        parsed = await _run_in_executor(_extract_pdf)

    elif ext == ".docx":
        if not _HAS_DOCX:
            raise RuntimeError("DOCX parsing requires 'python-docx'.")
        def _extract_docx():
            doc = Document(str(path))
            paragraphs = [{"text": p.text, "style": p.style.name} for p in doc.paragraphs if p.text.strip()]
            tables = [[[cell.text for cell in row.cells] for row in table.rows] for table in doc.tables]
            return {"type": "docx", "paragraphs": paragraphs, "tables": tables}
        parsed = await _run_in_executor(_extract_docx)

    elif ext in (".xlsx", ".xls"):
        if not _HAS_OPENPYXL:
            raise RuntimeError("XLSX parsing requires 'openpyxl'.")
        def _extract_xlsx():
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
        parsed = await _run_in_executor(_extract_xlsx)

    elif ext == ".csv":
        def _extract_csv():
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                rows = list(reader)
            headers = rows[0] if rows else []
            body_rows = rows[1:] if len(rows) > 1 else []
            return {"type": "csv", "headers": headers, "rows": body_rows, "total_rows": len(rows)}
        parsed = await _run_in_executor(_extract_csv)

    elif ext == ".json":
        def _extract_json():
            content = path.read_text(encoding="utf-8", errors="replace")
            return {"type": "json", "data": json.loads(content)}
        parsed = await _run_in_executor(_extract_json)

    elif ext in (".pptx", ".ppt"):
        if not _HAS_PPTX:
            raise RuntimeError("PPTX parsing requires 'python-pptx'.")
        def _extract_pptx():
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
        parsed = await _run_in_executor(_extract_pptx)

    else:
        def _extract_txt():
            content = path.read_text(encoding="utf-8", errors="replace")
            return {"type": path.suffix.lstrip(".") or "text", "content": content, "lines": len(content.splitlines())}
        parsed = await _run_in_executor(_extract_txt)

    return {
        "status": "ok",
        "filepath": str(path),
        "result": parsed,
    }


# =============================================================================
# 6. UNIVERSAL DOCUMENT CONVERSION ENGINE
# =============================================================================
async def convert_document(
    source_path: Union[str, Path],
    target_format: str,
    output_filename: Optional[str] = None,
    theme: str = "slate",
    destination: Optional[str] = None,
    is_temporary: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Universal document converter: transforms files between CSV, XLSX, DOCX, PDF, HTML, and Markdown.
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"Source document not found: {src}")

    target_fmt = str(target_format).lower().replace(".", "").strip()
    out_name = output_filename or f"{src.stem}_converted.{target_fmt}"

    parse_res = await parse_document(src)
    parsed = parse_res.get("result", {})
    p_type = parsed.get("type", "")

    if target_fmt in ("xlsx", "excel"):
        if p_type == "csv":
            sheets = {"Sheet1": {"headers": parsed.get("headers", []), "rows": parsed.get("rows", [])}}
            return await create_excel(filename=out_name, sheets_data=sheets, theme=theme, destination=destination, is_temporary=is_temporary)
        elif p_type == "json":
            data = parsed.get("data")
            rows = data if isinstance(data, list) else [data]
            sheets = {"Sheet1": {"headers": [], "rows": rows}}
            return await create_excel(filename=out_name, sheets_data=sheets, theme=theme, destination=destination, is_temporary=is_temporary)
        else:
            raw_text = parsed.get("content", str(parsed))
            sheets = {"Sheet1": {"headers": ["Document Content"], "rows": [[line] for line in str(raw_text).splitlines()]}}
            return await create_excel(filename=out_name, sheets_data=sheets, theme=theme, destination=destination, is_temporary=is_temporary)

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
        return await create_word(filename=out_name, content_blocks=blocks, theme=theme, destination=destination, is_temporary=is_temporary)

    elif target_fmt == "pdf":
        raw_text = parsed.get("content", "")
        if not raw_text and p_type in ("csv", "xlsx"):
            headers = parsed.get("headers", [])
            rows = parsed.get("rows", [])
            lines = [f"# {src.stem}\n"]
            if headers:
                lines.append("| " + " | ".join(str(h) for h in headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for r in rows:
                lines.append("| " + " | ".join(str(c) for c in r) + " |")
            raw_text = "\n".join(lines)
        return await create_pdf(filename=out_name, markdown_content=str(raw_text), theme=theme, destination=destination, is_temporary=is_temporary)

    elif target_fmt == "csv":
        target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
        out_path = target_dir / out_name
        if p_type == "xlsx":
            sheets = parsed.get("sheets", {})
            first_sheet = next(iter(sheets.values()), {})
            headers = first_sheet.get("headers", [])
            rows = first_sheet.get("rows", [])
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if headers:
                    writer.writerow(headers)
                writer.writerows(rows)
            return {"status": "ok", "format": "csv", "path": str(out_path), "filename": out_path.name}
        else:
            raw_text = parsed.get("content", str(parsed))
            out_path.write_text(str(raw_text), encoding="utf-8")
            return {"status": "ok", "format": "csv", "path": str(out_path), "filename": out_path.name}

    else:
        # Default text / markdown / html conversion
        target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
        out_path = target_dir / out_name
        raw_text = parsed.get("content", str(parsed))
        out_path.write_text(str(raw_text), encoding="utf-8")
        return {"status": "ok", "format": target_fmt, "path": str(out_path), "filename": out_path.name}


# =============================================================================
# 7. EXECUTIVE REPORT GENERATOR (HTML / Markdown)
# =============================================================================
async def generate_report(
    filename: str = "report.html",
    sections: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    theme: str = "slate",
    destination: Optional[str] = None,
    is_temporary: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Generates a structured Markdown and HTML executive report."""
    palette = get_theme_palette(theme)
    base_stem = Path(filename).stem
    target_dir = resolve_target_dir(destination, is_temporary=is_temporary)
    filepath_html = target_dir / f"{base_stem}.html"

    doc_title = (metadata or {}).get("title", base_stem)
    sec_list = sections or [{"title": "Overview", "content": "Executive briefing report."}]

    md_lines = [f"# {doc_title}\n\n", f"*Generated by Makima OS | {time.strftime('%Y-%m-%d')}*\n\n---\n\n"]
    for s in sec_list:
        h = s.get("heading") or s.get("title", "Section")
        b = s.get("body") or s.get("content", "")
        md_lines.append(f"## {h}\n\n{b}\n\n")
        if "bullets" in s:
            for bullet in s["bullets"]:
                md_lines.append(f"- {bullet}\n")
            md_lines.append("\n")

    full_md = "".join(md_lines)
    if _HAS_MARKDOWN:
        html_body = markdown.markdown(full_md, extensions=["tables", "fenced_code"])
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
    </style>
</head>
<body>
    {html_body}
</body>
</html>"""

    filepath_html.parent.mkdir(parents=True, exist_ok=True)
    filepath_html.write_text(html_template, encoding="utf-8")
    return {
        "status": "ok",
        "format": "html",
        "path": str(filepath_html),
        "filename": filepath_html.name,
    }


# =============================================================================
# 8. EPHEMERAL DOCUMENT CLEANUP & MANAGEMENT
# =============================================================================
async def delete_document(filepath: Union[str, Path]) -> Dict[str, Any]:
    """Safely delete a generated or temporary document file."""
    path = Path(filepath)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {path}"}
    try:
        path.unlink()
        return {"status": "ok", "message": f"Deleted document: {path.name}", "path": str(path)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete {path}: {e}"}


async def cleanup_temp_documents(max_age_hours: Optional[float] = None) -> Dict[str, Any]:
    """Clean up all temporary documents generated in the temp directory."""
    if not _DEFAULT_TEMP_DIR.exists():
        return {"status": "ok", "message": "No temp documents found", "deleted_count": 0}

    now = time.time()
    count = 0
    for p in _DEFAULT_TEMP_DIR.glob("*"):
        if p.is_file():
            try:
                if max_age_hours is None or (now - p.stat().st_mtime > max_age_hours * 3600):
                    p.unlink()
                    count += 1
            except Exception:
                pass
    return {"status": "ok", "message": f"Cleaned up {count} temp document(s)", "deleted_count": count}


# ---------------------------------------------------------------------------
# Declarative Definitions and Registry Mapping
# ---------------------------------------------------------------------------

DOCUMENT_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "create_excel",
        "description": "Generate publication-grade Excel spreadsheets (.xlsx) with formatting, live formulas, and theme palettes.",
        "func": create_excel,
        "schema": {
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
        "hints": ["document", "data_analyst", "finance", "code"],
        "task_tags": ["document", "excel", "xlsx", "spreadsheet"],
        "priority": 2,
    },
    {
        "name": "create_word",
        "description": "Generate executive Microsoft Word documents (.docx) with auto-updating TOC, headings, tables, cover pages, and callouts.",
        "func": create_word,
        "schema": {
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
        "hints": ["document", "research", "code"],
        "task_tags": ["document", "word", "docx"],
        "priority": 2,
    },
    {
        "name": "create_pdf",
        "description": "Generate enterprise-grade PDF documents compiled from Markdown or HTML with theme palettes.",
        "func": create_pdf,
        "schema": {
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
        "hints": ["document", "research"],
        "task_tags": ["document", "pdf"],
        "priority": 2,
    },
    {
        "name": "create_powerpoint",
        "description": "Generate executive PowerPoint presentations (.pptx) with modern 16:9 widescreen layout.",
        "func": create_powerpoint,
        "schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target .pptx filename", "default": "presentation.pptx"},
                "slides_data": {"type": "array", "description": "List of slide definitions with title, body, and bullets"},
                "title": {"type": "string", "description": "Presentation title"},
                "theme": {"type": "string", "description": "Theme palette: slate | emerald | crimson | nordic", "default": "slate"},
                "destination": {"type": "string", "description": "Save destination folder: desktop | downloads | documents or custom path"},
            },
        },
        "hints": ["document", "research"],
        "task_tags": ["document", "pptx", "presentation", "slides"],
        "priority": 2,
    },
    {
        "name": "convert_document",
        "description": "Universal document converter: transforms files between CSV, XLSX, DOCX, PDF, HTML, and Markdown.",
        "func": convert_document,
        "schema": {
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
        "hints": ["document", "data_analyst", "research"],
        "task_tags": ["document", "convert", "converter"],
        "priority": 2,
    },
    {
        "name": "parse_document",
        "description": "Parse and extract structured text, tables, and pages from PDF, DOCX, XLSX, CSV, TXT, MD, or JSON.",
        "func": parse_document,
        "schema": {
            "type": "object",
            "required": ["filepath"],
            "properties": {
                "filepath": {"type": "string", "description": "Path to document file to parse"},
                "file_path": {"type": "string", "description": "Alias for filepath"},
            },
        },
        "hints": ["document", "data_analyst", "research", "code"],
        "task_tags": ["document", "parse", "read"],
        "priority": 2,
    },
]


def register_document_tools(registry: Any) -> None:
    """
    Registers all headless document capability tools into Makima's ToolRegistry.
    Compatible with registry.register_tool(), registry.register(), registry.add_tool(), or dict-like registries.
    """
    if registry is None:
        logger.warning("register_document_tools: registry is None, skipping")
        return

    registered_count = 0
    for tool_def in DOCUMENT_TOOL_DEFINITIONS:
        name = tool_def["name"]
        func = tool_def["func"]
        description = tool_def["description"]
        schema = tool_def["schema"]
        category = "document"
        hints = tool_def.get("hints", ["document"])
        task_tags = tool_def.get("task_tags", ["document"])
        priority = tool_def.get("priority", 2)

        try:
            if hasattr(registry, "register_tool"):
                registry.register_tool(
                    name=name,
                    description=description,
                    func=func,
                    schema=schema,
                    category=category,
                    agent_hints=hints,
                    task_tags=task_tags,
                    priority=priority,
                    parallel_safe=True,
                    timeout_s=60.0,
                )
            elif hasattr(registry, "register"):
                registry.register(
                    name=name,
                    func=func,
                    description=description,
                    schema=schema,
                    category=category,
                    agent_hints=hints,
                    task_tags=task_tags,
                    priority=priority,
                )
            elif hasattr(registry, "add_tool"):
                registry.add_tool(
                    name=name,
                    func=func,
                    description=description,
                    schema=schema,
                    category=category,
                )
            else:
                registry[name] = func
            registered_count += 1
        except Exception as e:
            logger.warning("Failed to register document tool '%s': %s", name, e)

    logger.info("Successfully registered %d document capability tools into ToolRegistry.", registered_count)
