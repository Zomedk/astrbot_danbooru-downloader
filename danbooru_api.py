"""Danbooru post requests. Query syntax belongs to query_planner, not here."""

import asyncio
from time import monotonic, perf_counter, sleep

import requests


BASE_URL = "https://danbooru.donmai.us/posts.json"
COUNT_URL = "https://danbooru.donmai.us/counts/posts.json"
TRANSIENT_STATUSES = {429, 500, 502, 503}
COUNT_CACHE_SECONDS = 300


class FetchError(RuntimeError):
    def __init__(self, status: int):
        super().__init__(f"Danbooru HTTP {status}")
        self.status = status


class QueryBudgetError(RuntimeError):
    pass


class DanbooruApiClient:
    """Apply one request policy to every image command.

    The requests session is owned by the plugin so media downloads can reuse it.
    A trace is supplied per command; no request statistics leak between commands.
    """

    def __init__(self, session, *, auth=None, proxies=None, retry_limit: int = 1,
                 request_budget: int = 6, min_interval: float = 1.0,
                 clock=monotonic, sleeper=sleep, timer=perf_counter):
        self.session = session
        self.auth = auth
        self.proxies = proxies
        self.retry_limit = retry_limit
        self.request_budget = request_budget
        self.min_interval = min_interval
        self.clock = clock
        self.sleeper = sleeper
        self.timer = timer
        self._last_call = 0.0
        self._count_cache: dict[str, tuple[float, int]] = {}

    def _get_json(self, url: str, params: dict, trace: dict | None):
        for attempt in range(self.retry_limit + 1):
            if trace is not None and trace["request_count"] >= self.request_budget:
                raise QueryBudgetError("per-command API budget exhausted")
            wait = self.min_interval - (self.clock() - self._last_call)
            if wait > 0:
                self.sleeper(wait)
            self._last_call = self.clock()
            if trace is not None:
                trace["request_count"] += 1
            started = self.timer()
            try:
                response = self.session.get(
                    url, params=params, auth=self.auth,
                    proxies=self.proxies, timeout=(5, 15),
                )
                try:
                    status = response.status_code
                    if status != 200:
                        retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                        try:
                            retry_seconds = float(retry_after)
                        except (TypeError, ValueError):
                            retry_seconds = 1.0
                        if status in TRANSIENT_STATUSES and attempt < self.retry_limit:
                            if status != 429 or 0 <= retry_seconds <= 2:
                                self.sleeper(max(1.0, retry_seconds))
                                continue
                        raise FetchError(status)
                    return response.json()
                finally:
                    response.close()
            except requests.RequestException:
                if attempt >= self.retry_limit:
                    raise
                self.sleeper(1.0)
            finally:
                if trace is not None:
                    trace["api_ms"] += int((self.timer() - started) * 1000)
        raise RuntimeError("Danbooru request retry loop exhausted")

    def fetch_posts(self, plan, page: str | None = None, *, trace: dict | None = None) -> list[dict]:
        posts = self._get_json(BASE_URL, plan.params(page), trace)
        if not isinstance(posts, list):
            raise ValueError("Danbooru 返回内容不是帖子列表")
        return posts

    def count_posts(self, tags: str, *, trace: dict | None = None) -> int:
        cached = self._count_cache.get(tags)
        if cached and self.clock() - cached[0] < COUNT_CACHE_SECONDS:
            return cached[1]
        result = self._get_json(COUNT_URL, {"tags": tags}, trace)
        try:
            count = int(result["counts"]["posts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Danbooru 返回的投稿数量无效") from exc
        if count < 0:
            raise ValueError("Danbooru 返回的投稿数量无效")
        self._count_cache[tags] = (self.clock(), count)
        return count

    async def fetch(self, plan, page: str | None = None, *, trace: dict | None = None) -> list[dict]:
        return await asyncio.to_thread(self.fetch_posts, plan, page, trace=trace)

    async def count(self, tags: str, *, trace: dict | None = None) -> int:
        return await asyncio.to_thread(self.count_posts, tags, trace=trace)
