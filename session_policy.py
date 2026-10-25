"""Small, local policy for image requests. No chat content is retained."""

from dataclasses import dataclass
from time import monotonic

RATINGS = ("g", "s", "q", "e")


def _seconds(value, default):
    try:
        return max(0.0, min(float(value), 300.0))
    except (ValueError, TypeError):
        return default


def _level(value, default="g"):
    return value if value in RATINGS else default


@dataclass(frozen=True)
class RatingDecision:
    allowed: frozenset[str]
    query_rating: str
    error: str = ""


class SessionPolicy:
    def __init__(self, config, clock=monotonic):
        self.clock = clock
        self.user_seconds = _seconds(config.get("cooldown_user_seconds"), 3.0)
        self.scope_seconds = _seconds(config.get("cooldown_session_seconds"), 1.5)
        self.feedback_seconds = _seconds(config.get("cooldown_feedback_suppress_seconds"), 10.0)
        self.group_default = _level(config.get("rating_group_default"))
        self.private_default = _level(config.get("rating_private_default"))
        overrides = config.get("rating_group_overrides")
        self.group_overrides = overrides if isinstance(overrides, dict) else {}
        self.allow_group_e = config.get("allow_explicit_group", config.get("allow_group_r18", False)) is True
        self.allow_private_e = config.get("allow_explicit_private", False) is True
        self.user_until = {}
        self.scope_until = {}
        self.feedback_until = {}

    def default_request(self, scope, is_group):
        # A broad request is only a request; the final post still passes allowed().
        level = self._scope_level(scope, is_group)
        return "all" if level in {"s", "q"} else "safe"

    def _scope_level(self, scope, is_group):
        if is_group:
            return _level(self.group_overrides.get(scope), self.group_default)
        return self.private_default

    def decide(self, scope, is_group, request):
        level = self._scope_level(scope, is_group)
        cap = RATINGS.index(level)
        permitted = set(RATINGS[:cap + 1])
        explicit = self.allow_group_e if is_group else self.allow_private_e
        if not explicit:
            permitted.discard("e")
        requested = {"safe": {"g"}, "all": {"g", "s", "q"}, "r18": {"e"}}.get(request)
        if requested is None:
            return RatingDecision(frozenset(), "safe", "不支持的分级")
        allowed = frozenset(permitted & requested)
        if not allowed:
            return RatingDecision(allowed, "safe", "本会话未开启该类图片")
        query_rating = "r18" if request == "r18" else "safe" if allowed == {"g"} else "all"
        return RatingDecision(allowed, query_rating)

    def check_cooldown(self, scope, sender):
        now = self.clock()
        remaining = max(self.user_until.get((scope, sender), 0), self.scope_until.get(scope, 0)) - now
        if remaining <= 0:
            return 0.0, False
        show = self.feedback_until.get((scope, sender), 0) <= now
        if show:
            self.feedback_until[(scope, sender)] = now + self.feedback_seconds
        return remaining, show

    def record(self, scope, sender, *, success):
        now = self.clock()
        delay_user = self.user_seconds if success else min(1.0, self.user_seconds)
        delay_scope = self.scope_seconds if success else min(1.0, self.scope_seconds)
        self.user_until[(scope, sender)] = now + delay_user
        self.scope_until[scope] = now + delay_scope
        if len(self.user_until) > 1000:
            self.user_until = {key: value for key, value in self.user_until.items() if value > now}
            self.feedback_until = {key: value for key, value in self.feedback_until.items() if value > now}
        if len(self.scope_until) > 500:
            self.scope_until = {key: value for key, value in self.scope_until.items() if value > now}
