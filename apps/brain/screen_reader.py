"""
Makima v7.1 — Screen Reader

Orchestrates C++ DXGI screen capture service.
Primary: Gemini Vision API with frame as base64. Fallback: Tesseract OCR + text-only LLM.
Frame delta check: skips vision call entirely if < 2% change.
Privacy mode: returns None immediately without calling C++ service at all.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("makima.screen_reader")

class ScreenReader:
    def __init__(self, config: dict, screen_service=None, ai_handler=None):
        self.config = config.get("screen", {})
        self.privacy_mode = config.get("privacy", {}).get("mode", False)
        self.delta_threshold = self.config.get("frame_delta_threshold_pct", 2.0)
        self.screen_service = screen_service
        self.ai_handler = ai_handler

    async def get_context(self) -> Optional[str]:
        """Get textual context describing the current screen."""
        if self.privacy_mode:
            return None
            
        if not self.screen_service:
            logger.warning("Screen service not available")
            return None
            
        try:
            # 1. Check delta (N5)
            # This would be an async gRPC call in production
            delta = await self.screen_service.get_frame_delta()
            if delta.get("delta_percent", 100.0) < self.delta_threshold:
                return "[Screen unchanged since last capture]"
                
            # 2. Capture Frame
            frame = await self.screen_service.capture_frame(
                max_width=self.config.get("max_width", 1280),
                max_height=self.config.get("max_height", 720)
            )
            
            if frame.get("is_drm_blocked"):
                return "[Screen content blocked by DRM]"
                
            # 3. Vision API (Gemini/Claude)
            import base64
            b64_img = base64.b64encode(frame.get("frame_data", b"")).decode('utf-8')
            
            prompt = "Describe the UI and content currently visible on the screen. Be concise but capture text, active windows, and context."
            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]}
            ]
            
            try:
                response = await self.ai_handler.generate(messages, task="vision")
                return response.text
            except Exception as e:
                logger.error(f"Vision API failed: {e}")
                if self.config.get("ocr_fallback", True):
                    # Fallback to OCR (Tesseract via pytesseract)
                    return self._fallback_ocr(frame.get("frame_data"))
                return "[Screen analysis failed]"
                
        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            return None
            
    def _fallback_ocr(self, img_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(img_bytes))
            text = pytesseract.image_to_string(img)
            return f"[OCR Fallback Output]\n{text}" if text.strip() else "[Screen unreadable via OCR]"
        except Exception as e:
            logger.error(f"OCR fallback failed: {e}")
            return "[Screen unreadable via OCR]"
