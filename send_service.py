"""Classify one awaited AstrBot image submission conservatively."""

import asyncio
from dataclasses import dataclass
from enum import Enum
from time import perf_counter


class DeliveryStatus(str, Enum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class DeliveryOutcome:
    status: DeliveryStatus
    reason: str
    elapsed_ms: int
    platform_message_id: str | None = None


def classify_send_exception(exc):
    # Once event.send starts, adapter errors generally cannot prove QQ did not
    # display the image. A local chain/path failure must be caught before send.
    return DeliveryStatus.UNCERTAIN


def safe_error_code(exc):
    name = type(exc).__name__
    if isinstance(exc, TimeoutError):
        return "qq_timeout"
    if "1200" in str(exc):
        return "qq_1200"
    if isinstance(exc, (ConnectionError, ConnectionResetError)):
        return "qq_connection_lost"
    return "qq_" + name[:40]


async def submit_one_image(event, chain, history, pending, state, scope, sender, intent, post, req_id):
    post_id = int(post["id"])
    if not pending.reserve(scope, post_id, req_id):
        return DeliveryOutcome(DeliveryStatus.FAILED, "pending_write_failed", 0)
    started = perf_counter()
    try:
        await event.send(chain)
    except asyncio.CancelledError:
        pending.hold_uncertain(scope, post_id, req_id)
        raise
    except Exception as exc:
        status = classify_send_exception(exc)
        pending.hold_uncertain(scope, post_id, req_id)
        return DeliveryOutcome(status, safe_error_code(exc), int((perf_counter() - started) * 1000))
    elapsed = int((perf_counter() - started) * 1000)
    # The adapter returned. A disk failure cannot turn this into a safe retry.
    try:
        history_saved = history.mark_sent(scope, post_id)
    except Exception:
        history_saved = False
    if history_saved:
        pending.release(scope, post_id, req_id)
        state.record_success(scope, sender, intent, post)
        return DeliveryOutcome(DeliveryStatus.ACCEPTED, "adapter_returned", elapsed)
    pending.hold_uncertain(scope, post_id, req_id)
    state.record_success(scope, sender, intent, post)
    return DeliveryOutcome(DeliveryStatus.ACCEPTED, "history_write_failed", elapsed)
