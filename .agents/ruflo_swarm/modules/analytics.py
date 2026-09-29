"""
Code Analytics module — Wraps Ruflo AST complexity, risk, and boundary analysis.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class CodeAnalytics:
    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()

    def _run(self, args: List[str], timeout: int = 40) -> Dict[str, Any]:
        cmd = ["ruflo.cmd"] + args if sys.platform == "win32" else ["ruflo"] + args
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {"exit_code": res.returncode, "stdout": res.stdout.strip(), "stderr": res.stderr.strip()}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    def complexity(self, target_path: str, threshold: int = 10) -> Dict[str, Any]:
        """Calculates Cyclomatic & Cognitive complexity metrics."""
        return self._run(["analyze", "complexity", target_path, "--threshold", str(threshold)])

    def symbols(self, target_path: str) -> Dict[str, Any]:
        """Extracts functions, classes, and types using AST parsing."""
        return self._run(["analyze", "symbols", target_path])

    def circular(self, target_dir: str) -> Dict[str, Any]:
        """Detects circular import dependencies across modules."""
        return self._run(["analyze", "circular", target_dir])
