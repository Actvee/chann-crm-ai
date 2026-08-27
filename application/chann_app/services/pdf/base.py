"""PDF renderer interface.

Introduced in Phase 1 with a null implementation on purpose. The engine has
already been swapped once (Carbone -> SmartBrowz, ADR-021); putting the seam
in before it is needed means the next swap costs one class instead of a
rewrite of Phase 10 and Phase 13.

Domain code must depend on PdfRenderer, never on a vendor SDK. The boundary
test enforces this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class PdfOptions:
    page_format: str = "A4"
    landscape: bool = False
    print_background: bool = True
    password: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class PdfResult:
    content: bytes | None
    url: str | None
    renderer: str


class PdfRenderer(Protocol):
    async def render(self, html: str, options: PdfOptions, idempotency_key: str) -> PdfResult: ...
    async def preview_image(self, html: str, options: PdfOptions) -> PdfResult: ...


class NullPdfRenderer:
    """Phase 1 placeholder. Fails loudly rather than returning an empty PDF,
    because a blank quotation reaching a customer is worse than an error."""

    name = "null"

    async def render(self, html: str, options: PdfOptions, idempotency_key: str) -> PdfResult:
        raise NotImplementedError("PDF rendering arrives in Phase 10 (ADR-021, SmartBrowz)")

    async def preview_image(self, html: str, options: PdfOptions) -> PdfResult:
        raise NotImplementedError("PDF preview arrives in Phase 10 (ADR-021, SmartBrowz)")


def get_renderer(name: str) -> PdfRenderer:
    if name == "null":
        return NullPdfRenderer()
    if name == "smartbrowz":
        from .smartbrowz import SmartBrowzPdfRenderer  # local import — see that
        # module's docstring for why it, not this file, is the one allowed to
        # import zcatalyst_sdk (the boundary test enforces this).
        return SmartBrowzPdfRenderer()
    raise NotImplementedError(f"renderer {name!r} is not implemented until Phase 10")
