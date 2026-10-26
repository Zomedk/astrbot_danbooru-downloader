"""Build Danbooru Member searches before making an API request."""

import re
from dataclasses import dataclass, replace

POST_FIELDS = (
    "id,rating,score,tag_string_general,tag_string_character,"
    "file_ext,file_size,image_width,image_height,"
    "file_url,large_file_url,created_at,is_deleted,tag_string_artist"
)
FREE_METATAGS = {
    "rating", "status", "is", "age", "date", "id", "limit", "score",
    "downvotes", "favcount", "width", "height", "ratio", "mpixels",
    "filesize", "filetype", "duration", "md5", "pixiv_id", "pixiv",
    "parent", "child", "upvote", "embedded", "tagcount",
}
MAX_MEMBER_TAGS = 2


class PlanError(ValueError):
    """The requested mapping/options cannot fit a free Member search."""


@dataclass(frozen=True)
class SearchIntent:
    mode: str  # new or random
    character_key: str
    raw_tag_query: str
    rating: str  # safe, all (g/s/q), or r18
    solo: bool
    filtered: bool
    scope_id: str
    extra_metatags: tuple[str, ...] = ()
    required_character_tags: tuple[str, ...] = ()
    max_age_days: int | None = None


@dataclass(frozen=True)
class QueryPlan:
    api_tags: str
    limit: int
    page: str | None
    local_filters: frozenset[str]
    sampling_kind: str  # site_random or recent
    counted_tags: int
    notice: str = ""
    required_character_tags: tuple[str, ...] = ()
    max_age_days: int | None = None
    min_score: int | None = None

    def params(self, page: str | None = None) -> dict:
        params = {"tags": self.api_tags, "limit": self.limit, "only": POST_FIELDS}
        if page:
            params["page"] = page
        return params


def _mapping_tokens(raw: str) -> list[str]:
    tokens = raw.strip().split()
    if not tokens:
        raise PlanError("这个角色没有可查询的标签")
    # Existing mapping entries may use ~ OR terms and negative tags. Count each
    # operand conservatively; do not silently drop any part of the expression.
    if any(token.lower() == "or" or token in {"(", ")"} for token in tokens):
        raise PlanError("这个角色的映射表达式暂不支持免费账户查询，请调整映射")
    return tokens


def _counted(tokens: list[str]) -> int:
    count = 0
    for token in tokens:
        if token == "~":
            continue
        term = token.lstrip("~-+")
        key = term.split(":", 1)[0].lower() if ":" in term else ""
        if key not in FREE_METATAGS:
            count += 1
    return count


def _add_solo(tokens: list[str], intent: SearchIntent, base_count: int,
              local_filters: set[str]) -> None:
    if intent.solo:
        if intent.mode == "new" and base_count < MAX_MEMBER_TAGS:
            tokens.append("solo")
        else:
            local_filters.add("solo")


def _add_sampling(tokens: list[str], intent: SearchIntent, base_count: int,
                  new_limit: int, random_sample_limit: int) -> tuple[str, int, str]:
    if intent.mode == "random" and base_count < MAX_MEMBER_TAGS:
        sample = max(1, min(int(random_sample_limit), 200))
        if intent.solo:
            sample = max(sample, 50)
        tokens.append(f"random:{sample}")
        return "site_random", sample, ""
    notice = "本次改为近期随机：角色映射已占满免费标签名额" if intent.mode == "random" else ""
    return "recent", max(1, min(int(new_limit), 200)), notice


def _add_rating_and_score(tokens: list[str], intent: SearchIntent) -> int | None:
    if intent.rating == "safe":
        tokens.append("rating:g")
    elif intent.rating == "r18":
        tokens.append("rating:e")
    if intent.filtered:
        tokens.append("score:>30")
    return 30 if intent.filtered else None


def _add_extra_metatags(tokens: list[str], intent: SearchIntent,
                        minimum_score: int | None) -> int | None:
    for extra in intent.extra_metatags:
        if extra == "age:<7d":
            if intent.max_age_days != 7:
                raise PlanError("本周新图时间条件无效")
        elif re.fullmatch(r"score:>[0-9]{1,4}", extra):
            value = int(extra.split(">", 1)[1])
            minimum_score = max(minimum_score or 0, value)
        else:
            raise PlanError("不支持的额外查询条件")
        if extra not in tokens:
            tokens.append(extra)
    return minimum_score


def build_query_plan(intent: SearchIntent, *, new_limit: int = 100,
                     random_sample_limit: int = 30) -> QueryPlan:
    """Compose mapping, options, sampling, rating and free metatags once."""
    if intent.mode not in {"new", "random"} or intent.rating not in {"safe", "all", "r18"}:
        raise PlanError("不支持的查询方式或分级")
    tokens = _mapping_tokens(intent.raw_tag_query)
    base_count = _counted(tokens)
    if base_count > MAX_MEMBER_TAGS:
        raise PlanError(f"角色映射需要 {base_count} 个标签，免费账户最多支持 2 个")

    local_filters: set[str] = set()
    _add_solo(tokens, intent, base_count, local_filters)
    kind, limit, notice = _add_sampling(tokens, intent, base_count,
                                        new_limit, random_sample_limit)
    minimum_score = _add_rating_and_score(tokens, intent)
    minimum_score = _add_extra_metatags(tokens, intent, minimum_score)

    counted = _counted(tokens)
    if counted > MAX_MEMBER_TAGS:
        raise PlanError("查询超过免费账户的两标签限制，请调整角色映射或选项")
    return QueryPlan(" ".join(tokens), limit, None, frozenset(local_filters),
                     kind, counted, notice, intent.required_character_tags,
                     intent.max_age_days, minimum_score)


def recent_fallback(intent: SearchIntent, *, new_limit: int = 100) -> QueryPlan:
    """Keep solo when a random sample contains no usable solo posts."""
    plan = build_query_plan(replace(intent, mode="new"), new_limit=new_limit)
    return replace(plan, notice="本次改为近期随机（保留单人条件）")
