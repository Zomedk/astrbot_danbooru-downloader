"""Shared planning and candidate selection for every image command."""

from dataclasses import dataclass
from typing import Awaitable, Callable

if __package__:
    from .query_planner import SearchIntent, build_query_plan, recent_fallback
    from .selector import Selection, choose_new, choose_random
else:  # Direct local module tests
    from query_planner import SearchIntent, build_query_plan, recent_fallback
    from selector import Selection, choose_new, choose_random


@dataclass(frozen=True)
class SearchService:
    new_limit: int = 100
    random_sample_limit: int = 30
    extra_pages: int = 2

    async def select(self, intent: SearchIntent,
                     fetch: Callable[[object, str | None], Awaitable[list[dict]]],
                     history, *, excluded: set[int], allowed_ratings: frozenset[str],
                     sample_attempts: int = 2,
                     extra_pages: int | None = None) -> Selection:
        """Plan one intent, then use the same selection rules for all commands."""
        plan = build_query_plan(intent, new_limit=self.new_limit,
                                random_sample_limit=self.random_sample_limit)
        pages = self.extra_pages if extra_pages is None else extra_pages
        if intent.mode == "new":
            return await choose_new(plan, fetch, history, intent.scope_id, intent.rating,
                                    extra_pages=pages, excluded=excluded,
                                    allowed_ratings=allowed_ratings)
        fallback = (recent_fallback(intent, new_limit=self.new_limit)
                    if intent.solo else None)
        return await choose_random(plan, fetch, history, intent.scope_id, intent.rating,
                                   fallback=fallback, extra_pages=pages, excluded=excluded,
                                   sample_attempts=sample_attempts,
                                   allowed_ratings=allowed_ratings)
