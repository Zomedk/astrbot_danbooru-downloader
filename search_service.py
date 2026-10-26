"""Shared planning and candidate selection for every image command."""

import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable

if __package__:
    from .query_planner import SearchIntent, build_query_plan
    from .selector import Selection, choose_new, choose_random
else:  # Direct local module tests
    from query_planner import SearchIntent, build_query_plan
    from selector import Selection, choose_new, choose_random

MAX_MEMBER_PAGES = 1000


@dataclass(frozen=True)
class SearchService:
    new_limit: int = 100
    random_sample_limit: int = 100
    extra_pages: int = 2
    _last_random_page: dict[str, int] = field(default_factory=dict, repr=False, compare=False)

    def _random_pages(self, tags: str, count: int, limit: int,
                      attempts: int) -> list[str]:
        page_count = min(MAX_MEMBER_PAGES, (count + limit - 1) // limit)
        if page_count <= 0:
            return []
        pages = random.sample(range(1, page_count + 1), min(max(1, attempts), page_count))
        last = self._last_random_page.get(tags)
        if page_count > 1 and pages[0] == last:
            if len(pages) > 1:
                pages[0], pages[1] = pages[1], pages[0]
            else:
                alternative = random.randrange(1, page_count)
                pages[0] = alternative if alternative < last else alternative + 1
        self._last_random_page[tags] = pages[0]
        return [str(page) for page in pages]

    async def select(self, intent: SearchIntent,
                     fetch: Callable[[object, str | None], Awaitable[list[dict]]],
                     history, *, excluded: set[int], allowed_ratings: frozenset[str],
                     count: Callable[[str], Awaitable[int]] | None = None,
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
        sample_pages = None
        if plan.sampling_kind == "random_page":
            if count is None:
                raise ValueError("双标签随机查询缺少投稿数量接口")
            post_count = await count(plan.api_tags)
            sample_pages = self._random_pages(plan.api_tags, post_count, plan.limit,
                                              sample_attempts)
            if not sample_pages:
                return Selection([])
        return await choose_random(plan, fetch, history, intent.scope_id, intent.rating,
                                   pages=sample_pages, excluded=excluded,
                                   sample_attempts=sample_attempts,
                                   allowed_ratings=allowed_ratings)
