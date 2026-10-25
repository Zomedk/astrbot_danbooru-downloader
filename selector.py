"""Select usable, unseen posts without downloading multiple images."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

if __package__:
    from .query_planner import QueryPlan
else:  # Direct local module tests
    from query_planner import QueryPlan

MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
SUPPORTED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


@dataclass(frozen=True)
class Selection:
    posts: list[dict]
    notice: str = ""
    page_index: int = 0


def valid_post(post: dict, rating: str, local_filters: frozenset[str],
               required_character_tags: tuple[str, ...] = (), max_age_days: int | None = None,
               min_score: int | None = None,
               allowed_ratings: frozenset[str] | None = None) -> bool:
    if not isinstance(post, dict) or post.get("is_deleted"):
        return False
    try:
        if int(post.get("id", 0)) <= 0:
            return False
        width, height = int(post.get("image_width", 0)), int(post.get("image_height", 0))
        if width <= 0 or height <= 0 or (width * height > MAX_IMAGE_PIXELS and not post.get("large_file_url")):
            return False
        size = post.get("file_size")
        if size is not None and int(size) > MAX_DOWNLOAD_BYTES and not post.get("large_file_url"):
            return False
    except (TypeError, ValueError):
        return False
    if str(post.get("file_ext", "")).lower() not in SUPPORTED_EXTENSIONS:
        return False
    if not (post.get("file_url") or post.get("large_file_url")):
        return False
    allowed = {"safe": {"g"}, "all": {"g", "s", "q"}, "r18": {"e"}}[rating]
    if post.get("rating") not in allowed or (allowed_ratings is not None and post.get("rating") not in allowed_ratings):
        return False
    if "solo" in local_filters and "solo" not in str(post.get("tag_string_general", "")).split():
        return False
    if required_character_tags and not set(required_character_tags).issubset(
            str(post.get("tag_string_character", "")).split()):
        return False
    if min_score is not None:
        try:
            if int(post.get("score", -1)) <= min_score:
                return False
        except (TypeError, ValueError):
            return False
    if max_age_days is not None:
        try:
            created = datetime.fromisoformat(str(post["created_at"]).replace("Z", "+00:00"))
            if not created.tzinfo or created < datetime.now(timezone.utc) - timedelta(days=max_age_days):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


def eligible_posts(posts: list[dict], rating: str, plan: QueryPlan,
                   history, scope: str, excluded: set[int],
                   allowed_ratings: frozenset[str] | None = None) -> list[dict]:
    seen = history.ids(scope)
    eligible = [post for post in posts
                if valid_post(post, rating, plan.local_filters, plan.required_character_tags,
                              plan.max_age_days, plan.min_score, allowed_ratings)
                and int(post["id"]) not in excluded
                and int(post["id"]) not in seen]
    random.shuffle(eligible)
    return eligible


async def choose_new(plan: QueryPlan, fetch: Callable[[QueryPlan, str | None], Awaitable[list[dict]]],
                     history, scope: str, rating: str, *, extra_pages: int = 2,
                     excluded: set[int] | None = None,
                     allowed_ratings: frozenset[str] | None = None) -> Selection:
    excluded = excluded or set()
    page = None
    for index in range(max(0, int(extra_pages)) + 1):
        posts = await fetch(plan, page)
        candidates = eligible_posts(posts, rating, plan, history, scope, excluded, allowed_ratings)
        if candidates:
            return Selection(candidates, plan.notice, index)
        ids = [int(post["id"]) for post in posts if isinstance(post, dict)
               and str(post.get("id", "")).isdigit()]
        if not ids or len(posts) < plan.limit:
            break
        page = f"b{min(ids)}"
    return Selection([], plan.notice)


async def choose_random(plan: QueryPlan, fetch: Callable[[QueryPlan, str | None], Awaitable[list[dict]]],
                        history, scope: str, rating: str, *, excluded: set[int] | None = None,
                        fallback: QueryPlan | None = None, extra_pages: int = 2,
                        sample_attempts: int = 2,
                        allowed_ratings: frozenset[str] | None = None) -> Selection:
    if plan.sampling_kind == "recent":
        return await choose_new(plan, fetch, history, scope, rating,
                                extra_pages=extra_pages, excluded=excluded,
                                allowed_ratings=allowed_ratings)
    for _ in range(max(1, sample_attempts)):
        posts = await fetch(plan, None)
        candidates = eligible_posts(posts, rating, plan, history, scope, excluded or set(), allowed_ratings)
        if candidates:
            return Selection(candidates)
    if fallback:
        return await choose_new(fallback, fetch, history, scope, rating,
                                extra_pages=extra_pages, excluded=excluded,
                                allowed_ratings=allowed_ratings)
    return Selection([])
