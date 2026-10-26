"""Turn second-phase commands into the first-phase structured search intent."""

from dataclasses import replace

if __package__:
    from .query_planner import SearchIntent
else:  # Direct local tests
    from query_planner import SearchIntent


def character_intent(record, scope: str, rating: str, *, mode: str = "random",
                     solo: bool = False, filtered: bool = False) -> SearchIntent:
    return SearchIntent(mode, record.id, record.base_tag, rating, solo, filtered, scope)


def with_curated(base: SearchIntent, minimum_score: int = 30) -> SearchIntent:
    """Add a score threshold to any character query without losing its options."""
    score = max(0, min(int(minimum_score), 1000))
    return replace(base, mode="new", extra_metatags=base.extra_metatags + (f"score:>{score}",))


def with_week(base: SearchIntent) -> SearchIntent:
    """Add a seven-day window without losing solo, rating or score options."""
    return replace(base, mode="new", extra_metatags=base.extra_metatags + ("age:<7d",),
                   max_age_days=7)


def curated_intent(record, scope: str, rating: str, minimum_score: int = 30,
                   *, solo: bool = False, filtered: bool = False) -> SearchIntent:
    return with_curated(character_intent(record, scope, rating, mode="new",
                                         solo=solo, filtered=filtered), minimum_score)


def week_intent(record, scope: str, rating: str, *, solo: bool = False,
                filtered: bool = False) -> SearchIntent:
    return with_week(character_intent(record, scope, rating, mode="new",
                                      solo=solo, filtered=filtered))


def duo_intent(first, second, scope: str, rating: str, *, mode: str = "new",
               filtered: bool = False) -> SearchIntent:
    if first.id == second.id:
        raise ValueError("请选择两个不同的人物")
    if " " in first.base_tag or " " in second.base_tag:
        raise ValueError("合照需要两个可单独查询的人物本体标签")
    return SearchIntent(mode, f"{first.id}+{second.id}",
                        f"{first.base_tag} {second.base_tag}", rating, False, filtered,
                        scope, (), (first.base_tag, second.base_tag))


def variant_intent(character, variant, scope: str, rating: str, *, mode: str = "random",
                   solo: bool = False, filtered: bool = False) -> SearchIntent:
    if variant.parent_character_id != character.id or not (variant.relation_verified or variant.manual_approved):
        raise ValueError("装扮关系尚未核实，不能用于查询")
    tags = variant.tag if variant.relation_verified else f"{character.base_tag} {variant.tag}"
    return SearchIntent(mode, f"{character.id}/{variant.id}", tags, rating,
                        solo, filtered, scope, (), (character.base_tag, variant.tag))
