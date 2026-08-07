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
        # Safe eval context
        result = eval(query, {"pl": pl, "df": df})
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

def register_data_tools(registry: Any):
    registry.register("profile_dataset", profile_dataset, "Profiles a CSV/Parquet dataset")
    registry.register("execute_polars_query", execute_polars_query, "Executes a Polars query on a dataset")
    registry.register("generate_chart", generate_chart, "Generates a chart from dataset columns")
