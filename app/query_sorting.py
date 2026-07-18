"""Safe, reusable ordering for paginated SQLAlchemy queries."""
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Query


def apply_sorting(
    query: Query,
    *,
    sort_by: str | None,
    sort_dir: str | None,
    columns: Mapping[str, Any],
    default_key: str,
    default_dir: str = "asc",
    tie_breaker: Any | None = None,
) -> Query:
    """Apply ordering through an allow-list so request values never become raw SQL."""
    key = sort_by if sort_by in columns else default_key
    direction = sort_dir if sort_dir in {"asc", "desc"} else default_dir
    expression = columns[key]
    ordered = (expression.desc() if direction == "desc" else expression.asc()).nullslast()

    clauses = [ordered]
    if tie_breaker is not None and expression is not tie_breaker:
        clauses.append(tie_breaker.desc() if direction == "desc" else tie_breaker.asc())
    return query.order_by(*clauses)
