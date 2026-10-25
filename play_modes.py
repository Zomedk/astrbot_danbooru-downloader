"""Turn second-phase commands into the first-phase structured search intent."""

if __package__:
    from .query_planner import SearchIntent
else:  # Direct local tests
    from query_planner import SearchIntent


def character_intent(record, scope: str, rating: str, *, mode: str = "random",
                     solo: bool = False, filtered: bool = False) -> SearchIntent:
    return SearchIntent(mode, record.id, record.base_tag, rating, solo, filtered, scope)


def curated_intent(record, scope: str, rating: str, minimum_score: int = 30) -> SearchIntent:
    score = max(0, min(int(minimum_score), 1000))
    return SearchIntent("new", record.id, record.base_tag, rating, False, False,
                        scope, (f"score:>{score}",))


def week_intent(record, scope: str, rating: str) -> SearchIntent:
    return SearchIntent("new", record.id, record.base_tag, rating, False, False,
                        scope, ("age:<7d",), (), 7)


def duo_intent(first, second, scope: str, rating: str) -> SearchIntent:
    if first.id == second.id:
        raise ValueError("请选择两个不同的人物")
    if " " in first.base_tag or " " in second.base_tag:
        raise ValueError("合照需要两个可单独查询的人物本体标签")
    return SearchIntent("new", f"{first.id}+{second.id}",
                        f"{first.base_tag} {second.base_tag}", rating, False, False,
                        scope, (), (first.base_tag, second.base_tag))


def variant_intent(character, variant, scope: str, rating: str) -> SearchIntent:
    if variant.parent_character_id != character.id or not (variant.relation_verified or variant.manual_approved):
        raise ValueError("装扮关系尚未核实，不能用于查询")
    tags = variant.tag if variant.relation_verified else f"{character.base_tag} {variant.tag}"
    return SearchIntent("random", f"{character.id}/{variant.id}", tags, rating,
                        False, False, scope, (), (character.base_tag, variant.tag))
