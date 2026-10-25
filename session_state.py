"""Small in-memory context for redraw and the most recently delivered post."""

from collections import OrderedDict
from dataclasses import dataclass
from time import time


@dataclass(frozen=True)
class LastIntent:
    intent: object
    post_id: int
    updated_at: float


@dataclass(frozen=True)
class LastDeliveredPost:
    post_id: int
    character_key: str
    artist_tags: tuple[str, ...]
    created_at: str
    rating: str
    sent_at: float


class SessionState:
    def __init__(self, max_users: int = 500, max_scopes: int = 200):
        self.last_intents: OrderedDict[tuple[str, str], LastIntent] = OrderedDict()
        self.last_delivered: OrderedDict[str, LastDeliveredPost] = OrderedDict()
        self.max_users = max_users
        self.max_scopes = max_scopes

    def record_success(self, scope: str, sender: str, intent, post: dict) -> None:
        post_id = int(post["id"])
        key = (scope, sender)
        self.last_intents.pop(key, None)
        self.last_intents[key] = LastIntent(intent, post_id, time())
        while len(self.last_intents) > self.max_users:
            self.last_intents.popitem(last=False)
        self.last_delivered.pop(scope, None)
        self.last_delivered[scope] = LastDeliveredPost(
            post_id, intent.character_key,
            tuple(str(post.get("tag_string_artist") or "").split()),
            str(post.get("created_at") or ""), str(post.get("rating") or ""), time(),
        )
        while len(self.last_delivered) > self.max_scopes:
            self.last_delivered.popitem(last=False)

    def get_intent(self, scope: str, sender: str) -> LastIntent | None:
        return self.last_intents.get((scope, sender))

    def get_delivered(self, scope: str) -> LastDeliveredPost | None:
        return self.last_delivered.get(scope)
