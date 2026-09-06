"""
Makima v7.2 — Elite Data Tools
Polars/Pandas integration, chart generation, dataset profiling.
"""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("makima.tools.data")

try:
    import polars as pl
    _HAS_POLARS = True
except ImportError:
    _HAS_POLARS = False

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

async def profile_dataset(file_path: str) -> str:
    if not _HAS_POLARS: return "[Error] Polars not installed."
    path = Path(file_path)
    if not path.exists(): return f"[Error] File not found: {file_path}"
    
    try:
        if path.suffix == ".csv":
            df = pl.read_csv(path)
        elif path.suffix == ".parquet":
            df = pl.read_parquet(path)
        else:
            return f"[Error] Unsupported file type: {path.suffix}"
        
        stats = f"Shape: {df.shape}\nSchema:\n{df.schema}\n\nDescribe:\n{df.describe()}"
        return stats
    except Exception as e:
        return f"[Error] Profiling failed: {e}"

async def execute_polars_query(file_path: str, query: str) -> str:
    if not _HAS_POLARS: return "[Error] Polars not installed."
    try:
        df = pl.read_csv(file_path) if file_path.endswith(".csv") else pl.read_parquet(file_path)
        # Safe restricted eval context
        safe_builtins = {"len": len, "min": min, "max": max, "sum": sum, "range": range}
        result = eval(query, {"__builtins__": safe_builtins, "pl": pl, "df": df})
        return str(result)
    except Exception as e:
        return f"[Error] Query execution failed: {e}"

async def generate_chart(file_path: str, chart_type: str, x_col: str, y_col: str, title: str) -> str:
    if not _HAS_POLARS or not _HAS_MPL: return "[Error] Polars/Matplotlib not installed."
    try:
        df = pl.read_csv(file_path) if file_path.endswith(".csv") else pl.read_parquet(file_path)
        x = df[x_col].to_list()
        y = df[y_col].to_list()
        
        plt.figure(figsize=(10, 6))
        if chart_type == "bar": plt.bar(x, y)
        elif chart_type == "scatter": plt.scatter(x, y)
        else: plt.plot(x, y)
            
        plt.title(title)
        plt.xlabel(x_col)
        plt.ylabel(y_col)
        
        out_path = Path(file_path).with_suffix(".png")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, plt.savefig, str(out_path))
        plt.close()
        return f"Chart saved to {out_path}"
    except Exception as e:
        return f"[Error] Chart generation failed: {e}"

def register_data_tools(registry: Any) -> None:
    tools = [
        {
            "name": "profile_dataset",
            "func": profile_dataset,
            "description": "Call this tool EXCLUSIVELY when you need to understand the shape, schema, and basic statistics of a CSV or Parquet dataset before running complex queries.",
            "schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute or relative path to CSV or Parquet data file"},
                },
                "required": ["file_path"],
            },
            "category": "data",
        },
        {
            "name": "execute_polars_query",
            "func": execute_polars_query,
            "description": "Call this tool EXCLUSIVELY when you need to extract specific rows, aggregate data, or compute metrics using a valid Python Polars expression on a dataset.",
            "schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the dataset file (CSV or Parquet)"},
                    "query": {"type": "string", "description": "Python Polars expression to evaluate on df"},
                },
                "required": ["file_path", "query"],
            },
            "category": "data",
        },
        {
            "name": "generate_chart",
            "func": generate_chart,
            "description": "Call this tool EXCLUSIVELY when the user explicitly requests a visual chart (bar, scatter, line) from a dataset. Ensure you have profiled the data first to know valid x and y columns.",
            "schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the CSV or Parquet dataset"},
                    "chart_type": {"type": "string", "enum": ["bar", "scatter", "line"], "description": "Type of chart to generate"},
                    "x_col": {"type": "string", "description": "Column name for x-axis"},
                    "y_col": {"type": "string", "description": "Column name for y-axis"},
                    "title": {"type": "string", "description": "Title of the rendered chart"},
                },
                "required": ["file_path", "chart_type", "x_col", "y_col", "title"],
            },
            "category": "data",
        },
    ]

    for t in tools:
        if hasattr(registry, "register"):
            registry.register(
                name=t["name"],
                func=t["func"],
                description=t["description"],
                schema=t["schema"],
                category=t["category"],
            )
        elif hasattr(registry, "add_tool"):
            registry.add_tool(
                name=t["name"],
                func=t["func"],
                description=t["description"],
                schema=t["schema"],
                category=t["category"],
            )
        else:
            registry[t["name"]] = t["func"]
    logger.info("Successfully registered 3 elite data tools.")
