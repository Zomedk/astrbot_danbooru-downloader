"""Small per-conversation, post-ID history for the AstrBot data directory."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class HistoryStore:
    def __init__(self, path: Path, max_per_scope: int = 1000, days: int = 30,
                 clock=None, logger=None):
        self.path = Path(path)
        self.max_per_scope = max(1, int(max_per_scope))
        self.days = max(1, int(days))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.logger = logger
        self.scopes: dict[str, list[dict]] = {}
        self._load()

    def _warn(self, message: str) -> None:
        if self.logger:
            self.logger.warning(message)

    def _prune(self) -> None:
        cutoff = self.clock() - timedelta(days=self.days)
        for scope, records in list(self.scopes.items()):
            kept = []
            for record in records:
                try:
                    stamp = datetime.fromisoformat(record["ts"])
                    post_id = int(record["id"])
                    if stamp.tzinfo and stamp >= cutoff and post_id > 0:
                        kept.append({"id": post_id, "ts": record["ts"]})
                except (KeyError, TypeError, ValueError):
                    continue
            if kept:
                self.scopes[scope] = kept[-self.max_per_scope:]
            else:
                self.scopes.pop(scope, None)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("scopes"), dict):
                raise ValueError("unsupported history format")
            self.scopes = {key: value for key, value in data["scopes"].items()
                           if isinstance(key, str) and isinstance(value, list)}
            self._prune()
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.scopes = {}
            backup = self.path.with_name(self.path.name + ".corrupt-" + self.clock().strftime("%Y%m%d%H%M%S"))
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            self._warn(f"[Danbooru] 历史文件损坏，已重建: {type(exc).__name__}")

    def seen(self, scope: str, post_id: int) -> bool:
        self._prune()
        return any(item["id"] == post_id for item in self.scopes.get(scope, ()))

    def ids(self, scope: str) -> set[int]:
        self._prune()
        return {item["id"] for item in self.scopes.get(scope, ())}

    def mark_sent(self, scope: str, post_id: int) -> bool:
        self._prune()
        records = self.scopes.setdefault(scope, [])
        records[:] = [item for item in records if item["id"] != post_id]
        records.append({"id": post_id, "ts": self.clock().isoformat()})
        records[:] = records[-self.max_per_scope:]
        return self._save()

    def _save(self) -> bool:
        temp_name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".history-", suffix=".tmp", delete=False) as handle:
                temp_name = handle.name
                json.dump({"version": 1, "scopes": self.scopes}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            return True
        except OSError as exc:
            self._warn(f"[Danbooru] 历史写入失败: {type(exc).__name__}")
            return False
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass


class PendingSendStore:
    """Persist uncertain submissions; expired holds remain unresolved for 30 days."""

    def __init__(self, path: Path, hold_minutes: int = 30, clock=None, logger=None):
        self.path = Path(path)
        self.hold_minutes = max(1, int(hold_minutes))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.logger = logger
        self.records: dict[str, dict] = {}
        self.healthy = True
        self._load()

    @staticmethod
    def _key(scope, post_id):
        return f"{scope}\u001f{int(post_id)}"

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("records"), dict):
                raise ValueError("unsupported pending format")
            self.records = data["records"]
            self._prune()
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # Keep the old file for diagnosis. Never infer that a submission failed.
            self.healthy = False
            if self.logger:
                self.logger.error(f"[Danbooru] 待确认记录读取失败: {type(exc).__name__}")
            self.records = {}

    def _prune(self):
        now = self.clock()
        cutoff = now - timedelta(days=30)
        for key, record in list(self.records.items()):
            try:
                stamp = datetime.fromisoformat(record["created_at"])
                if stamp.tzinfo is None or stamp < cutoff:
                    self.records.pop(key, None)
                elif stamp < now - timedelta(minutes=self.hold_minutes):
                    record["state"] = "unresolved"
            except (KeyError, TypeError, ValueError, AttributeError):
                self.records.pop(key, None)

    def ids(self, scope):
        self._prune()
        return {record["post_id"] for record in self.records.values()
                if record.get("scope") == scope and isinstance(record.get("post_id"), int)}

    def reserve(self, scope, post_id, req_id):
        if not self.healthy:
            return False
        key = self._key(scope, post_id)
        self.records[key] = {"scope": scope, "post_id": int(post_id), "req_id": req_id,
                             "state": "sending", "created_at": self.clock().isoformat()}
        self.healthy = self._save()
        return self.healthy

    def hold_uncertain(self, scope, post_id, req_id):
        key = self._key(scope, post_id)
        if key not in self.records:
            self.reserve(scope, post_id, req_id)
        self.records[key]["state"] = "uncertain"
        self.healthy = self._save()
        return self.healthy

    def release(self, scope, post_id, req_id):
        key = self._key(scope, post_id)
        if self.records.get(key, {}).get("req_id") == req_id:
            self.records.pop(key)
            self.healthy = self._save()
            return self.healthy
        return False

    def _save(self):
        temp_name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".pending-", suffix=".tmp", delete=False) as handle:
                temp_name = handle.name
                json.dump({"version": 1, "records": self.records}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            return True
        except OSError as exc:
            if self.logger:
                self.logger.error(f"[Danbooru] 待确认记录写入失败: {type(exc).__name__}")
            return False
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
