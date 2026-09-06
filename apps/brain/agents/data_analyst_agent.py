"""
Makima v7.2 — Elite Data Analyst Agent
Enterprise-grade data manipulation (Polars/Pandas), statistical inference, 
and autonomous chart generation engine.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# ━━━ Zero-Crash Resilience: Heavy Data Science Libraries ━━━
try:
    import polars as pl
    HAS_POLARS = True
except ImportError:
    pl = None
    HAS_POLARS = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None
    HAS_PANDAS = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend for server environments
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAS_PLOT = True
except ImportError:
    plt = None
    sns = None
    HAS_PLOT = False

try:
    from scipy import stats as scipy_stats
    HAS_SCIPY = True
except ImportError:
    scipy_stats = None
    HAS_SCIPY = False

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.data_analyst")


class CachedDataFrame:
    """Wrapper to cache loaded dataframes with engine metadata and modification time."""
    __slots__ = ("df", "engine", "mtime", "shape")

    def __init__(self, df: Any, engine: str, mtime: float, shape: tuple[int, int]):
        self.df = df
        self.engine = engine
        self.mtime = mtime
        self.shape = shape


class DataAnalystAgent(BaseAgent):
    """
    Elite Data Analyst Agent capable of high-performance data profiling, 
    SQL/DataFrame querying, statistical hypothesis testing, and visualization.
    """
    
    AGENT_NAME = "data_analyst"
    DESCRIPTION = "Enterprise data analysis, Polars/Pandas manipulation, statistical inference, charting"
    CAPABILITIES = ["data_analysis", "statistical_metrics", "csv_processing", "json_processing", "data_visualization", "chart_generation"]
    AGENT_TOOLS = ["profile_dataset", "execute_query", "statistical_test", "generate_chart", "export_dataset"]
    TAGS = ["data", "pandas", "polars", "csv", "json", "chart"]

    SYSTEM_PROMPT = """You are Makima's Elite Data Analyst & Statistical Inference Engine.
You analyze datasets, perform rigorous statistical tests, run high-performance SQL/DataFrame queries, and generate publication-quality charts.

AVAILABLE TOOLS:
- profile_dataset(file_path): Inspect schema, row count, null counts, memory usage, and descriptive statistics.
- execute_query(file_path, query, engine="polars"): Run SQL / DataFrame queries (first 50 rows returned).
- statistical_test(file_path, test_type, col1, col2, group_col): Run t_test, pearson, spearman, chi_square, anova.
- generate_chart(file_path, chart_type, x, y, hue=None, title=None): Generate scatter, line, bar, hist, box, violin, heatmap.
- export_dataset(file_path, output_path, format="csv"): Export processed dataset.

OPERATIONAL RULES:
1. Profile First: Use profile_dataset to understand data types, distributions, and null values before computing metrics.
2. Structured Thinking: Always perform step-by-step reasoning in <thinking>...</thinking> before selecting tools or synthesizing insights.
3. Statistical Rigor: Report sample size ($N$), test statistics ($t$, $F$, $\\chi^2$), and $p$-values with clear plain-language interpretations.
4. Clean Output: When a chart is generated, reference its path clearly; format tabular findings cleanly using Markdown tables.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    _TOOLS = frozenset({
        "profile_dataset", "execute_query", "statistical_test", 
        "generate_chart", "export_dataset"
    })

    def __init__(
        self,
        ai_handler=None,
        memory=None,
        tool_registry=None,
        ws_broadcast=None,
        orchestrator=None,
        guardrails=None,
        **kwargs
    ):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        self._df_cache: Dict[str, CachedDataFrame] = {}
        self._tool_registry = {
            "profile_dataset": self._tool_profile_dataset,
            "execute_query": self._tool_execute_query,
            "statistical_test": self._tool_statistical_test,
            "generate_chart": self._tool_generate_chart,
            "export_dataset": self._tool_export_dataset,
        }
        def _to_thread_wrapper(fn):
            async def _wrapped(**kw):
                return await asyncio.to_thread(fn, **kw)
            return _wrapped

        self._TOOL_MAP = {
            k: _to_thread_wrapper(v)
            for k, v in self._tool_registry.items()
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Core Execution Loop
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """Main ReAct execution loop for data analysis tasks using unified BaseAgent ReAct engine."""
        self._reset_state()

        # ── P1 Bridge 4B: Consume structured AgentTask parameters directly ────
        agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
        if agent_task and hasattr(agent_task, "operation") and agent_task.operation:
            op = str(agent_task.operation or "").lower().strip()
            params = dict(agent_task.parameters or {})
            
            tool_name = None
            if any(k in op for k in ("profile", "summary", "stats", "load_csv")):
                tool_name = "profile_dataset"
            elif any(k in op for k in ("query", "sql", "filter")):
                tool_name = "execute_query"
            elif any(k in op for k in ("test", "correlation", "regression", "hypothesis")):
                tool_name = "statistical_test"
            elif any(k in op for k in ("chart", "plot", "graph", "visualize")):
                tool_name = "generate_chart"
            elif any(k in op for k in ("export", "save_dataset")):
                tool_name = "export_dataset"

            if tool_name and tool_name in self._tool_registry:
                logger.info("[data_analyst] P1-4B: Direct execution of tool '%s'", tool_name)
                try:
                    res = await self._use_tool(tool_name, context=context, **params)
                    res_str = str(res) if not isinstance(res, (dict, list)) else json.dumps(res, indent=2)
                    self._partial_result = res_str
                    return res_str
                except Exception as ex:
                    logger.warning("[data_analyst] Direct tool '%s' failed, falling back to ReAct: %s", tool_name, ex)

        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=8,
                task_type="data_analysis",
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.error("[data_analyst] SDK error: %s", sdk_exc)
            return f"Task complete nahi hua: {str(sdk_exc)}"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Data Loading & Caching
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _resolve_path(self, file_path: str, context: Optional[dict[str, Any]] = None) -> Path:
        """Resolves file path relative to the workspace directory."""
        p = Path(file_path)
        if p.is_absolute():
            return p
        eff_ctx = context if context is not None else (getattr(self, "_current_context", {}) or {})
        workspace = eff_ctx.get("workspace_dir", os.getcwd()) if isinstance(eff_ctx, dict) else os.getcwd()
        return Path(workspace) / p

    def _load_dataframe(self, file_path: str, context: Optional[dict[str, Any]] = None) -> CachedDataFrame:
        """Loads and caches a dataframe, checking for file modifications."""
        resolved_path = self._resolve_path(file_path, context)
        if not resolved_path.exists():
            raise FileNotFoundError(f"Dataset not found: {resolved_path}")

        mtime = resolved_path.stat().st_mtime
        cache_key = str(resolved_path.absolute())

        if cache_key in self._df_cache:
            cached = self._df_cache[cache_key]
            if cached.mtime == mtime:
                return cached

        ext = resolved_path.suffix.lower()
        df, engine, shape = None, "unknown", (0, 0)

        if HAS_POLARS:
            engine = "polars"
            if ext == ".csv":
                df = pl.read_csv(resolved_path, infer_schema_length=10000)
            elif ext == ".parquet":
                df = pl.read_parquet(resolved_path)
            elif ext in [".jsonl", ".ndjson"]:
                df = pl.read_ndjson(resolved_path)
            elif ext == ".json":
                try:
                    df = pl.read_json(resolved_path)
                except Exception:
                    df = pl.read_ndjson(resolved_path)
            else:
                raise ValueError(f"Unsupported file extension for Polars: {ext}")
            shape = df.shape
        elif HAS_PANDAS:
            engine = "pandas"
            if ext == ".csv":
                df = pd.read_csv(resolved_path)
            elif ext == ".parquet":
                df = pd.read_parquet(resolved_path)
            elif ext in [".jsonl", ".ndjson"]:
                df = pd.read_json(resolved_path, lines=True)
            elif ext == ".json":
                try:
                    df = pd.read_json(resolved_path)
                except Exception:
                    df = pd.read_json(resolved_path, lines=True)
            else:
                raise ValueError(f"Unsupported file extension for Pandas: {ext}")
            shape = df.shape
        else:
            raise RuntimeError("Neither Polars nor Pandas is installed. Cannot load data.")

        cached_obj = CachedDataFrame(df=df, engine=engine, mtime=mtime, shape=shape)
        self._df_cache[cache_key] = cached_obj
        return cached_obj

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tool Implementations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _tool_profile_dataset(self, file_path: str, context: Optional[dict[str, Any]] = None, **kwargs) -> str:
        """Generates a comprehensive statistical profile of the dataset."""
        cached = self._load_dataframe(file_path, context)
        df, engine = cached.df, cached.engine
        
        profile = {"engine": engine, "shape": list(cached.shape), "columns": []}

        if engine == "polars":
            schema = df.schema
            null_counts = df.null_count().to_dict(as_series=False)
            
            for col_name, dtype in schema.items():
                col_info = {
                    "name": col_name,
                    "dtype": str(dtype),
                    "nulls": null_counts.get(col_name, 0),
                    "unique": df[col_name].n_unique()
                }
                if dtype in [pl.Float32, pl.Float64, pl.Int32, pl.Int64]:
                    desc = df.select(pl.col(col_name)).describe()
                    stats_rows = desc.to_dict(as_series=False)
                    stat_names = stats_rows[desc.columns[0]]
                    stat_values = stats_rows[col_name]
                    col_info["stats"] = {
                        n: (float(v) if v is not None else None)
                        for n, v in zip(stat_names, stat_values)
                    }
                profile["columns"].append(col_info)
                
        elif engine == "pandas":
            for col_name in df.columns:
                col_info = {
                    "name": col_name,
                    "dtype": str(df[col_name].dtype),
                    "nulls": int(df[col_name].isnull().sum()),
                    "unique": int(df[col_name].nunique())
                }
                if pd.api.types.is_numeric_dtype(df[col_name]):
                    desc = df[col_name].describe().to_dict()
                    col_info["stats"] = {k: float(v) for k, v in desc.items()}
                profile["columns"].append(col_info)

        return json.dumps(profile, indent=2, default=str)

    def _tool_execute_query(self, file_path: str, query: str, context: Optional[dict[str, Any]] = None, engine: str = "auto", **kwargs) -> str:
        """Executes a SQL or DataFrame query against the dataset."""
        cached = self._load_dataframe(file_path, context)
        df, current_engine = cached.df, cached.engine
        
        if engine == "auto":
            engine = current_engine

        result_df = None
        
        if engine == "polars" and HAS_POLARS:
            # Use Polars SQLContext for safe SQL execution.
            # Register the frame under BOTH a sanitized file-stem table name
            # (models query `FROM expenses` for expenses.csv) and `df`
            # (the historical name) so either works.
            table_name = re.sub(r"[^a-zA-Z0-9_]", "_", Path(file_path).stem).lower()
            if not table_name or not table_name[0].isalpha() and table_name[0] != "_":
                table_name = f"t_{table_name}"
            frames = {
                table_name: df,
                "df": df,
                "data": df,
                "dataset": df,
                "table": df,
                "t": df,
            }
            try:
                ctx = pl.SQLContext(**frames, eager=True)
            except (TypeError, ValueError):
                ctx = pl.SQLContext(**frames, eager_execution=True)
            result_df = ctx.execute(query)
        elif engine == "pandas" and HAS_PANDAS:
            # Fallback to pandas query
            try:
                result_df = df.query(query)
            except Exception as e:
                raise ValueError(f"Pandas query failed: {e}")
        else:
            raise ValueError(f"Engine '{engine}' not available or mismatch.")

        shape = result_df.shape
        if engine == "polars":
            head_data = result_df.head(50).to_dicts()
        else:
            head_data = json.loads(result_df.head(50).to_json(orient="records", date_format="iso"))

        return json.dumps({"shape": list(shape), "preview": head_data}, indent=2, default=str)

    def _tool_statistical_test(self, file_path: str, test_type: str, col1: str, context: Optional[dict[str, Any]] = None, col2: Optional[str] = None, group_col: Optional[str] = None, **kwargs) -> str:
        """Performs statistical hypothesis testing using SciPy."""
        if not HAS_SCIPY or not HAS_NUMPY:
            return "SciPy or NumPy not installed. Cannot perform statistical tests."

        cached = self._load_dataframe(file_path, context)
        df, engine = cached.df, cached.engine
        
        # Convert to numpy arrays and drop nulls
        if engine == "polars":
            arr1 = df[col1].drop_nulls().to_numpy()
            arr2 = df[col2].drop_nulls().to_numpy() if col2 else None
            groups = df[group_col].drop_nulls().to_numpy() if group_col else None
        else:
            arr1 = df[col1].dropna().to_numpy()
            arr2 = df[col2].dropna().to_numpy() if col2 else None
            groups = df[group_col].dropna().to_numpy() if group_col else None

        result = {"test": test_type, "col1": col1, "col2": col2}

        if test_type == "t_test":
            if arr2 is None:
                raise ValueError("t_test requires col2.")
            stat, p_val = scipy_stats.ttest_ind(arr1, arr2, equal_var=False)
            result.update({"statistic": float(stat), "p_value": float(p_val)})
            
        elif test_type in ["pearson", "spearman"]:
            if arr2 is None:
                raise ValueError(f"{test_type} requires col2.")
            func = scipy_stats.pearsonr if test_type == "pearson" else scipy_stats.spearmanr
            stat, p_val = func(arr1, arr2)
            result.update({"correlation": float(stat), "p_value": float(p_val)})
            
        elif test_type == "chi_square":
            if engine == "polars":
                contingency = df.pivot(index=col1, columns=col2, values=col1, aggregate_function="len").fill_null(0).to_numpy()
            else:
                contingency = pd.crosstab(df[col1], df[col2]).to_numpy()
            stat, p_val, dof, expected = scipy_stats.chi2_contingency(contingency)
            result.update({"statistic": float(stat), "p_value": float(p_val), "dof": int(dof)})
            
        elif test_type == "anova":
            if groups is None:
                raise ValueError("anova requires group_col.")
            unique_groups = np.unique(groups)
            group_arrays = [arr1[groups == g] for g in unique_groups]
            stat, p_val = scipy_stats.f_oneway(*group_arrays)
            result.update({"statistic": float(stat), "p_value": float(p_val), "groups": len(unique_groups)})
        else:
            raise ValueError(f"Unsupported test_type: {test_type}")

        return json.dumps(result, indent=2)

    def _tool_generate_chart(
        self,
        file_path: str,
        chart_type: str,
        x: str,
        y: str,
        context: Optional[dict[str, Any]] = None,
        hue: Optional[str] = None,
        title: str = "Chart",
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generates and saves a visualization using Seaborn/Matplotlib."""
        if not HAS_PLOT:
            return "Matplotlib or Seaborn not installed. Cannot generate charts."

        cached = self._load_dataframe(file_path, context)
        df, engine = cached.df, cached.engine
        
        # Convert Polars to Pandas for Seaborn compatibility
        plot_df = df.to_pandas() if engine == "polars" else df.copy()

        fig, ax = plt.subplots(figsize=(10, 6))
        sns.set_theme(style="whitegrid")

        try:
            if chart_type == "scatter":
                sns.scatterplot(data=plot_df, x=x, y=y, hue=hue, ax=ax)
            elif chart_type == "line":
                sns.lineplot(data=plot_df, x=x, y=y, hue=hue, ax=ax)
            elif chart_type == "bar":
                sns.barplot(data=plot_df, x=x, y=y, hue=hue, ax=ax)
            elif chart_type == "hist":
                sns.histplot(data=plot_df, x=x, hue=hue, kde=True, ax=ax)
            elif chart_type == "box":
                sns.boxplot(data=plot_df, x=x, y=y, hue=hue, ax=ax)
            elif chart_type == "violin":
                sns.violinplot(data=plot_df, x=x, y=y, hue=hue, ax=ax)
            elif chart_type == "heatmap":
                corr_matrix = plot_df.select_dtypes(include=[np.number]).corr()
                sns.heatmap(corr_matrix, annot=True, cmap="coolwarm", ax=ax)
            else:
                raise ValueError(f"Unsupported chart_type: {chart_type}")

            ax.set_title(title, fontsize=14, fontweight="bold")
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()

            dest_path = output_path or kwargs.get("output_path") or kwargs.get("target_path") or kwargs.get("destination")
            if dest_path:
                final_output_path = Path(dest_path)
                final_output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                eff_ctx = context if context is not None else (getattr(self, "_current_context", {}) or {})
                workspace = eff_ctx.get("workspace_dir", os.getcwd()) if isinstance(eff_ctx, dict) else os.getcwd()
                output_dir = Path(workspace) / "charts"
                output_dir.mkdir(parents=True, exist_ok=True)
                filename = f"chart_{uuid.uuid4().hex[:8]}.png"
                final_output_path = output_dir / filename

            fig.savefig(str(final_output_path), dpi=150, bbox_inches="tight")
            
            return json.dumps({"status": "success", "path": str(final_output_path), "chart_type": chart_type})
            
        finally:
            plt.close(fig)

    def _tool_export_dataset(self, file_path: str, output_path: str, context: Optional[dict[str, Any]] = None, format: str = "csv", **kwargs) -> str:
        """Exports the processed dataset to a specified format."""
        cached = self._load_dataframe(file_path, context)
        df, engine = cached.df, cached.engine
        
        resolved_out = self._resolve_path(output_path, context)
        resolved_out.parent.mkdir(parents=True, exist_ok=True)

        if format == "csv":
            if engine == "polars":
                df.write_csv(resolved_out)
            else:
                df.to_csv(resolved_out, index=False)
        elif format == "parquet":
            if engine == "polars":
                df.write_parquet(resolved_out)
            else:
                df.to_parquet(resolved_out, index=False)
        else:
            raise ValueError(f"Unsupported export format: {format}")

        return json.dumps({"status": "success", "exported_to": str(resolved_out), "format": format})

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Utility & Parsing Helpers
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _parse_json(self, text: str) -> Optional[dict]:
        """Robust JSON parser using unified try_parse_json."""
        if not text:
            return None
        if self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
            return self.ai_handler.try_parse_json(text)
        try:
            return json.loads(text)
        except Exception:
            return None
