"""
Makima v7.1 — Multimodal Handler

Processes files dropped into chat.
PNG/JPG/WEBP → base64 → Vision API
PDF → pdfminer text extract
Code files → inject with syntax label
CSV/JSON → first 50 rows + schema summary
Files never persisted to memory unless user asks explicitly.
"""
from __future__ import annotations
import base64
import logging
from typing import Any, Optional

logger = logging.getLogger("makima.multimodal_handler")

class MultimodalHandler:
    def __init__(self, ai_handler, config: dict):
        self.ai_handler = ai_handler
        self.max_tokens_per_file = config.get("context_budget", {}).get("max_attached_file_tokens", 4000)

    async def process_file(self, name: str, mime: str, data_b64: str) -> Optional[str]:
        """Process an uploaded file and return a textual representation for context."""
        try:
            raw_data = base64.b64decode(data_b64)
            
            if mime.startswith("image/"):
                return await self._process_image(name, mime, data_b64)
            elif mime == "application/pdf":
                return self._process_pdf(name, raw_data)
            elif mime in ("text/csv", "application/json"):
                return self._process_data_file(name, mime, raw_data)
            elif mime.startswith("text/") or name.endswith((".py", ".ts", ".rs", ".js", ".md", ".txt")):
                return self._process_text_file(name, raw_data)
            else:
                logger.warning(f"Unsupported file type: {mime} for {name}")
                return f"[Unsupported file: {name}]"
                
        except Exception as e:
            logger.error(f"Failed to process file {name}: {e}")
            return f"[Error processing {name}]"

    async def _process_image(self, name: str, mime: str, data_b64: str) -> str:
        prompt = "Describe this image in detail."
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data_b64}"}}
            ]}
        ]
        
        try:
            # Requires a vision-capable backend like Gemini or GPT-4o
            response = await self.ai_handler.generate(messages, task="vision")
            return f"[Image: {name}]\n{response.text}"
        except Exception as e:
            logger.error(f"Image vision analysis failed for {name}: {e}")
            return f"[Image: {name} (Vision analysis failed)]"

    def _process_pdf(self, name: str, raw_data: bytes) -> str:
        try:
            from pdfminer.high_level import extract_text
            import io
            text = extract_text(io.BytesIO(raw_data))
            # Rough token truncation
            truncated = text[:self.max_tokens_per_file * 3] 
            return f"[PDF File: {name}]\n{truncated}"
        except ImportError:
            return f"[PDF File: {name} (pdfminer.six not installed)]"

    def _process_text_file(self, name: str, raw_data: bytes) -> str:
        ext = name.split(".")[-1] if "." in name else "txt"
        text = raw_data.decode('utf-8', errors='replace')
        truncated = text[:self.max_tokens_per_file * 3]
        return f"[File: {name}]\n```{ext}\n{truncated}\n```"

    def _process_data_file(self, name: str, mime: str, raw_data: bytes) -> str:
        # Simplified: just return as text with truncation
        text = raw_data.decode('utf-8', errors='replace')
        lines = text.split("\n")
        summary = "\n".join(lines[:50])
        if len(lines) > 50:
            summary += "\n... (truncated)"
        return f"[Data File: {name}]\n```\n{summary}\n```"
