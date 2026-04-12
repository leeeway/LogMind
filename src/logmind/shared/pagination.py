"""
Pagination Utilities

Query parameter parsing and paginated response construction.
"""

from dataclasses import dataclass

from fastapi import Query


@dataclass
class PaginationParams:
    """Parsed pagination parameters."""
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def get_pagination(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> PaginationParams:
    """FastAPI dependency for pagination parameters."""
    return PaginationParams(page=page, page_size=page_size)
