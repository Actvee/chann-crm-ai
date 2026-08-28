"""Phase 10 — turning business data into a rendered document.

Split into two modules on purpose:

  * `snapshot.py` decides WHAT the document says (money, tax, identity) and
    is pure, deterministic and database-free, so the arithmetic can be
    tested exhaustively without a render or a network call;
  * `html.py` decides HOW it looks, taking an already-frozen snapshot and
    producing HTML.

Keeping them apart means a template change can never alter a total, and a
pricing-rule change can never depend on the layout.
"""
