"""One way to turn what a person typed into a WHERE clause.

Every list screen in the dashboard filtered in JavaScript, over whatever
rows had already been fetched. That is fine while a shop is small and
becomes a data-loss bug the moment a list is capped: round 20j put a
ceiling of 500 on customers and deals, so a shop with 800 customers could
type the name of the 600th, be told "ไม่พบ", and believe it. A cap
without server-side search is worse than no cap at all — it does not slow
the page down, it hides records (18 ก.ย. 2569).

The rule this module exists to enforce: **the page and its total are
filtered by the same clause.** A count taken without the search term is a
number the screen then prints — "แสดง 50 จาก 1,240" when the search
matched three — and there is no way for the reader to tell.
"""
from __future__ import annotations

from sqlalchemy import or_


def like_any(q: str | None, *columns):
    """`ILIKE %q%` across the columns a person would actually type into.

    None when there is nothing to search for, so the caller writes

        clause = like_any(q, *SEARCH)
        if clause is not None:
            query = query.where(clause)

    in both the list and the count, from the same tuple of columns.

    A `%` or `_` the person typed is escaped: searching for "50%" must
    look for the characters "50%", not for "50 followed by anything".
    """
    needle = (q or "").strip()
    if not needle:
        return None
    pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return or_(*[column.ilike(pattern, escape="\\") for column in columns])


def since(query, model, updated_since):
    """`updated_at >= stamp`, or the query untouched. One place, so every
    list that offers an ERP a sync cursor means the same thing by it."""
    if updated_since is None:
        return query
    return query.where(model.updated_at >= updated_since)


def page(query, *, limit: int | None, offset: int | None):
    """The window a caller asked for, applied in the one right order.

    Offset before limit, both clamped: a negative offset is a caller bug
    that must not become a database error in front of a person, and a
    limit of zero would answer an empty list that reads as "nothing here".
    """
    if offset:
        query = query.offset(max(0, int(offset)))
    if limit is not None:
        query = query.limit(max(1, int(limit)))
    return query
