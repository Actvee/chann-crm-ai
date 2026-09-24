"""The PDF/screenshot renderer seam.

Re-exported here because `from .pdf import PdfOptions, get_renderer` read
naturally and was written that way in `reports_ai.publish_files` — against
an empty `__init__`, so it raised ImportError into an `except Exception`
and every AI report quietly came back without its PDF (found 23 ก.ย. 2569,
round 21C).
"""
from __future__ import annotations

from .base import (
    NullPdfRenderer, PdfOptions, PdfRenderer, PdfResult, RendererUnavailable, get_renderer,
)

__all__ = [
    "NullPdfRenderer", "PdfOptions", "PdfRenderer", "PdfResult",
    "RendererUnavailable", "get_renderer",
]
