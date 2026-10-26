"""AstrBot Danbooru commands with shared planning, selection and delivery."""

import asyncio
import hashlib
import random
import re
import secrets
from pathlib import Path
from time import perf_counter, time

import requests

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image as AstrImage, Node, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .character_index import Ambiguous, CharacterIndex, LegacyQuery, NotFound, Resolved
from .danbooru_api import DanbooruApiClient, FetchError, QueryBudgetError
from .history_store import HistoryStore, PendingSendStore
from .mapping import RAW_DICT_GENSHIN_IMPACT
from .media import choose_media_url, download_image, prepare_image
from .play_modes import character_intent, duo_intent, variant_intent, with_curated, with_week
from .query_planner import PlanError, SearchIntent
from .search_service import SearchService
from .session_state import SessionState
from .session_policy import SessionPolicy
from .send_service import DeliveryStatus, submit_one_image

PLUGIN_NAME = "astrbot_danbooru_downloader"
async def delay_delete_file(path: Path, delay: int = 60) -> None:
    await asyncio.sleep(delay)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"[Danbooru] 临时文件清理失败: {type(exc).__name__}")


def _bounded(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


@register(PLUGIN_NAME, "Zomedk", "Danbooru美图插件", "4.2.0")
class DanbooruDownloaderPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.username = self.config.get("username") or ""
        self.api_key = self.config.get("api_key") or ""
        self.proxy = self.config.get("proxy") or ""
        self.new_limit = _bounded(self.config.get("new_limit"), 100, 1, 200)
        self.random_sample_limit = _bounded(self.config.get("random_sample_limit"), 30, 1, 200)
        self.new_max_extra_pages = _bounded(self.config.get("new_max_extra_pages"), 2, 0, 5)
        self.prefer_large_above_mib = _bounded(self.config.get("prefer_large_above_mib"), 3, 1, 16)
        self.featured_min_score = _bounded(self.config.get("featured_min_score"), 30, 0, 1000)
        self.policy = SessionPolicy(self.config)
        self.send_mode = self.config.get("send_mode", "direct")
        if self.send_mode not in {"direct", "forward"}:
            self.send_mode = "direct"
        self.max_download_bytes = _bounded(self.config.get("max_download_mib"), 32, 1, 32) * 1024 * 1024
        self.max_image_pixels = _bounded(self.config.get("max_image_megapixels"), 40, 1, 40) * 1000000
        self.media_retry_candidates = _bounded(self.config.get("media_retry_candidates"), 1, 0, 2)
        self.api_transient_retry_limit = _bounded(self.config.get("api_transient_retry_limit"), 1, 0, 1)
        self.is_sending = False
        self._last_delivery_status = ""
        self.in_flight: set[int] = set()
        self.session = requests.Session()
        identity = self.username or "anonymous"
        self.session.headers.update({"User-Agent": f"AstrBot-Danbooru/4.2 ({identity})",
                                     "Accept": "application/json"})
        self.api = DanbooruApiClient(
            self.session, auth=self._auth(), proxies=self._get_proxy_dict(),
            retry_limit=self.api_transient_retry_limit,
        )
        self.search = SearchService(self.new_limit, self.random_sample_limit,
                                    self.new_max_extra_pages)
        self.characters_map: dict[str, str] = {}
        for aliases, tag in RAW_DICT_GENSHIN_IMPACT.items():
            for alias in aliases.split("|"):
                alias = alias.strip()
                if alias:
                    self.characters_map.setdefault(alias, tag.strip())

        folder = Path(__file__).resolve().parent
        self.index = CharacterIndex.from_files(folder / "mapping.py", folder / "character_overrides.json")
        self.state = SessionState()
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.history = HistoryStore(
            data_dir / "history.json",
            max_per_scope=_bounded(self.config.get("history_max_per_session"), 1000, 1, 10000),
            days=_bounded(self.config.get("history_days"), 30, 1, 365), logger=logger,
        )
        self.pending = PendingSendStore(
            data_dir / "pending_send.json",
            hold_minutes=_bounded(self.config.get("uncertain_hold_minutes"), 30, 1, 1440), logger=logger,
        )
        self.temp_dir = data_dir / "temp"
        self._cleanup_old_temp()
        logger.info(f"[Danbooru] 已索引 {len(self.index.characters)} 个人物候选、{len(self.index.alias_to_candidates)} 个别名")

    def _get_proxy_dict(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def _auth(self):
        return (self.username, self.api_key) if self.username and self.api_key else None

    @staticmethod
    def _tail(event: AstrMessageEvent, command: str) -> str:
        raw = event.message_str.strip()
        return raw.split(command, 1)[1].strip() if command in raw else raw

    @staticmethod
    def _sender(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_sender_id", None)
        return str(getter()) if callable(getter) else "unknown"

    def _default_rating(self, event: AstrMessageEvent) -> str:
        return self.policy.default_request(event.unified_msg_origin, bool(event.get_group_id()))

    def _cleanup_old_temp(self) -> None:
        if not self.temp_dir.exists():
            return
        cutoff = time() - 3600
        for path in self.temp_dir.iterdir():
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    pass

    async def terminate(self):
        # Do not close a Session while its single active download still uses it.
        for _ in range(50):
            if not self.is_sending:
                self.session.close()
                return
            await asyncio.sleep(0.1)
        logger.warning("[Danbooru] 卸载时仍有发图任务，保留会话至任务退出")

    def _parse_args(self, parts: list[str], default_rating: str):
        rating, solo, filtered = default_rating, False, False
        parts = list(parts)
        while parts:
            option = parts[-1].lower()
            if parts[-1] == "单人":
                solo = True
            elif parts[-1] == "筛选":
                filtered = True
            elif option == "r18":
                rating = "r18"
            elif option in {"safe", "全年龄"}:
                rating = "safe"
            else:
                break
            parts.pop()
        name = " ".join(parts)
        compact = "".join(parts)
        if name not in self.characters_map and compact in self.characters_map:
            name = compact
        return name, rating, solo, filtered

    @staticmethod
    def _resolution_error(name: str, result) -> str:
        if isinstance(result, Ambiguous):
            options = "、".join(f"{item.franchise_name}:{item.display_name}" for item in result.candidates[:5])
            return f"“{name}”对应多个人物，请指定作品：{options}"
        if isinstance(result, NotFound):
            if result.reason:
                return result.reason
            hints = "、".join(result.suggestions[:5])
            return f"没有找到“{name}”" + (f"；你可能想找：{hints}" if hints else "")
        return "角色解析失败"

    def _excluded_ids(self, scope: str) -> set[int]:
        return self.in_flight | self.pending.ids(scope)

    def _message_chain(self, event: AstrMessageEvent, path: Path, caption: str) -> MessageChain:
        image = AstrImage(file=str(path))
        content = [Plain(caption), image] if caption else [image]
        if self.send_mode != "forward":
            return MessageChain(content)
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        bot_id = raw.get("self_id") if isinstance(raw, dict) else None
        node = Node(uin=int(bot_id or 3159302040), name="艾莉丝", content=content)
        return MessageChain([node])

    async def _deliver(self, event: AstrMessageEvent, intent: SearchIntent,
                       *, caption: str = "", sample_attempts: int = 2,
                       extra_pages: int | None = None) -> tuple[bool, str]:
        scope, sender = intent.scope_id, self._sender(event)
        req_id = secrets.token_hex(4)
        started = perf_counter()
        trace = {"api_ms": 0, "download_ms": 0, "process_ms": 0, "send_ms": 0,
                 "request_count": 0, "bytes_in": 0, "bytes_out": 0, "post_id": 0,
                 "source_rating": "-", "outcome": "failed", "reason": "preflight"}
        self._last_delivery_status = "blocked"
        try:
            decision = self.policy.decide(scope, bool(event.get_group_id()), intent.rating)
            if decision.error:
                trace["reason"] = "rating_denied"
                return False, decision.error
            remaining, show = self.policy.check_cooldown(scope, sender)
            if remaining > 0:
                trace["reason"] = "cooldown"
                return False, f"⏳ 发图冷却中，请 {remaining:.1f} 秒后重试" if show else ""
            if not self.pending.healthy:
                trace["reason"] = "pending_store_unavailable"
                return False, "发图记录暂不可用，请管理员检查日志"
            from dataclasses import replace
            effective_intent = replace(intent, rating=decision.query_rating)
            async def fetch(plan, page):
                return await self.api.fetch(plan, page, trace=trace)

            try:
                selection = await self.search.select(
                    effective_intent, fetch, self.history,
                    excluded=self._excluded_ids(scope), allowed_ratings=decision.allowed,
                    sample_attempts=sample_attempts, extra_pages=extra_pages,
                )
            except PlanError as exc:
                trace["reason"] = "query_plan"
                return False, f"无法查询：{exc}"
            except FetchError as exc:
                trace["reason"] = f"api_{exc.status}"
                logger.warning(f"[Danbooru] req={req_id} API HTTP {exc.status}")
                return False, "Danbooru 查询失败，请稍后重试或调整角色标签"
            except QueryBudgetError:
                trace["reason"] = "api_budget"
                return False, "这次查询尝试次数已达上限，请稍后再试"
            except (requests.RequestException, ValueError) as exc:
                trace["reason"] = "api_" + type(exc).__name__
                logger.warning(f"[Danbooru] req={req_id} API 异常: {type(exc).__name__}")
                return False, "Danbooru 查询暂时不可用，请稍后重试"
            if not selection.posts:
                trace["reason"] = "no_candidate"
                self._last_delivery_status = "no_candidate"
                return False, "最近候选已抽完或没有符合条件的图片，可稍后等新投稿"
            for post in selection.posts[:1 + self.media_retry_candidates]:
                post_id = int(post["id"])
                trace["post_id"] = post_id
                trace["source_rating"] = post.get("rating", "-")
                if post.get("rating") not in decision.allowed:
                    continue
                self.in_flight.add(post_id)
                path = None
                try:
                    try:
                        url = choose_media_url(post, self.prefer_large_above_mib)
                        download_started = perf_counter()
                        data = await asyncio.to_thread(download_image, self.session, url,
                                                       auth=self._auth(), proxies=self._get_proxy_dict(),
                                                       max_bytes=self.max_download_bytes)
                        trace["download_ms"] += int((perf_counter() - download_started) * 1000)
                        trace["bytes_in"] = len(data)
                        process_started = perf_counter()
                        path = await asyncio.to_thread(prepare_image, data, self.temp_dir,
                                                       max_pixels=self.max_image_pixels,
                                                       max_output_bytes=self.max_download_bytes)
                        trace["process_ms"] += int((perf_counter() - process_started) * 1000)
                        trace["bytes_out"] = path.stat().st_size
                        chain = self._message_chain(event, path, caption or selection.notice)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        trace["reason"] = "media_" + type(exc).__name__
                        logger.warning(f"[Danbooru] req={req_id} 媒体准备失败: {type(exc).__name__}")
                        continue
                    outcome = await submit_one_image(event, chain, self.history, self.pending,
                                                     self.state, scope, sender, intent, post, req_id)
                    trace["send_ms"] = outcome.elapsed_ms
                    trace["outcome"], trace["reason"] = outcome.status.value, outcome.reason
                    self._last_delivery_status = outcome.status.value
                    if outcome.status == DeliveryStatus.ACCEPTED:
                        self.policy.record(scope, sender, success=True)
                        if outcome.reason == "history_write_failed":
                            logger.warning(f"[Danbooru] req={req_id} 已提交但历史写入失败")
                        return True, ""
                    if outcome.status == DeliveryStatus.UNCERTAIN:
                        self.policy.record(scope, sender, success=True)
                        logger.warning(f"[Danbooru] req={req_id} QQ 结果不明 reason={outcome.reason} auto_retry=false")
                        return False, "QQ 发送结果暂时无法确认，本次不会自动重试"
                    return False, "发图记录暂不可用，请管理员检查日志"
                finally:
                    self.in_flight.discard(post_id)
                    if path is not None:
                        asyncio.create_task(delay_delete_file(path))
            self.policy.record(scope, sender, success=False)
            self._last_delivery_status = "media_failed"
            return False, "候选图片下载或处理失败，请稍后重试"
        finally:
            scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:8]
            logger.info(f"[Danbooru] req={req_id} mode={intent.mode} scope={scope_hash} "
                        f"post={trace['post_id']} rating={trace['source_rating']} "
                        f"api_ms={trace['api_ms']} download_ms={trace['download_ms']} "
                        f"process_ms={trace['process_ms']} send_ms={trace['send_ms']} "
                        f"bytes_in={trace['bytes_in']} bytes_out={trace['bytes_out']} "
                        f"requests={trace['request_count']} outcome={trace['outcome']} "
                        f"reason={trace['reason']} total_ms={int((perf_counter()-started)*1000)}")

    def _standard_intent(self, name: str, scope: str, rating: str,
                         mode: str, solo: bool, filtered: bool, *, allow_variant: bool = True):
        legacy_variant = re.fullmatch(r"(.+)[(（]([^()（）]+)[)）]", name) if allow_variant else None
        if legacy_variant:
            parent = self.index.resolve_character(legacy_variant.group(1))
            if isinstance(parent, Resolved):
                variant = self.index.resolve_variant(parent.character.id, legacy_variant.group(2))
                if variant:
                    return variant_intent(parent.character, variant, scope, rating), None
        resolved = self.index.resolve_character(name, allow_legacy=True)
        if isinstance(resolved, Resolved):
            return character_intent(resolved.character, scope, rating, mode=mode,
                                    solo=solo, filtered=filtered), None
        if isinstance(resolved, LegacyQuery):
            return SearchIntent(mode, resolved.alias, resolved.query, rating, solo, filtered, scope), None
        return None, self._resolution_error(name, resolved)

    async def _picture_command(self, event: AstrMessageEvent, command: str, kind: str):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        self.is_sending = True
        try:
            name, rating, solo, filtered = self._parse_args(self._tail(event, command).split(), self._default_rating(event))
            if not name:
                yield event.plain_result(f"用法：{command} 角色名 [单人] [safe/r18] [筛选]")
                return
            scope = event.unified_msg_origin
            mode = "random" if kind == "random" else "new"
            intent, error = self._standard_intent(
                name, scope, rating, mode, solo, filtered,
                allow_variant=kind in {"random", "new"},
            )
            if error:
                yield event.plain_result(error)
                return
            if kind == "curated":
                intent = with_curated(intent, self.featured_min_score)
            elif kind == "week":
                intent = with_week(intent)
            success, message = await self._deliver(event, intent)
            if not success:
                if "没有符合条件" in message and kind == "curated":
                    message = f"没有找到评分高于 {self.featured_min_score} 的未发图片，可降低精选评分门槛"
                elif "没有符合条件" in message and kind == "week":
                    message = "过去 7 天没有符合条件的未发图片，可稍后等新投稿"
                if message:
                    yield event.plain_result(message)
        finally:
            self.is_sending = False

    @filter.command("美图")
    async def meitu(self, event: AstrMessageEvent):
        async for reply in self._picture_command(event, "美图", "random"):
            yield reply

    @filter.command("新图")
    async def new_image(self, event: AstrMessageEvent):
        async for reply in self._picture_command(event, "新图", "new"):
            yield reply

    @filter.command("精选")
    async def curated(self, event: AstrMessageEvent):
        async for reply in self._picture_command(event, "精选", "curated"):
            yield reply

    @filter.command("本周新图")
    async def week_new(self, event: AstrMessageEvent):
        async for reply in self._picture_command(event, "本周新图", "week"):
            yield reply

    @filter.command("查角色")
    async def lookup_character(self, event: AstrMessageEvent):
        name = self._tail(event, "查角色")
        if not name:
            yield event.plain_result("用法：查角色 胡桃 或 查角色 原神:胡桃")
            return
        found = self.index.resolve_character(name, allow_legacy=True)
        if isinstance(found, Resolved):
            record = found.character
            aliases = " / ".join(record.aliases[:5])
            variants = " / ".join(item.display_name for item in self.index.list_variants(record.id)[:8])
            others = "、".join(f"{item.franchise_name}·{item.display_name}" for item in found.alternatives[:3])
            text = f"📚 {record.display_name}｜{record.franchise_name}\n主标签：{record.base_tag}"
            if aliases:
                text += f"\n别名：{aliases}"
            if variants:
                text += f"\n已核对装扮：{variants}"
            if others:
                text += f"\n其他同名：{others}"
            if record.tag_status != "active_exact":
                text += "\n远程标签尚未核验"
            text += f"\n用法：新图 {record.franchise_name}:{record.display_name}"
            yield event.plain_result(text)
        elif isinstance(found, LegacyQuery):
            yield event.plain_result(f"“{name}”在旧映射中对应 {found.query}；尚未核验为人物")
        else:
            yield event.plain_result(self._resolution_error(name, found))

    @filter.command("再来一张")
    async def redraw(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        last = self.state.get_intent(event.unified_msg_origin, self._sender(event))
        if not last:
            yield event.plain_result("你还没抽过图，先试试 新图 胡桃")
            return
        self.is_sending = True
        try:
            success, message = await self._deliver(event, last.intent)
            if not success and message:
                yield event.plain_result(message)
        finally:
            self.is_sending = False

    @filter.command("换一张")
    async def redraw_alias(self, event: AstrMessageEvent):
        async for reply in self.redraw(event):
            yield reply

    @filter.command("图片出处")
    async def image_source(self, event: AstrMessageEvent):
        post = self.state.get_delivered(event.unified_msg_origin)
        decision = self.policy.decide(event.unified_msg_origin, bool(event.get_group_id()),
                                      "r18" if post and post.rating == "e" else "all")
        if not post or post.rating not in decision.allowed:
            yield event.plain_result("本会话还没有可查询的已发送图片")
            return
        text = f"出处：https://danbooru.donmai.us/posts/{post.post_id}"
        if post.artist_tags:
            text += "\n画师标签：" + " / ".join(post.artist_tags[:3])
        if post.created_at:
            text += "\n收录：" + post.created_at.split("T", 1)[0]
        yield event.plain_result(text)

    @filter.command("上一张出处")
    async def image_source_alias(self, event: AstrMessageEvent):
        async for reply in self.image_source(event):
            yield reply

    @filter.command("随机角色")
    async def random_character(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        franchise = self._tail(event, "随机角色")
        pool = self.index.eligible_characters(franchise)
        if not pool:
            yield event.plain_result("该作品暂无已核验的随机人物池；例如：随机角色 原神")
            return
        self.is_sending = True
        try:
            choices = random.sample(pool, min(2, len(pool)))
            for record in choices:
                intent = character_intent(record, event.unified_msg_origin, self._default_rating(event))
                success, message = await self._deliver(event, intent,
                                                 caption=f"随机角色：{record.franchise_name}·{record.display_name}",
                                                 sample_attempts=1, extra_pages=0)
                if success:
                    return
                if self._last_delivery_status in {"uncertain", "blocked", "failed"}:
                    if message:
                        yield event.plain_result(message)
                    return
            yield event.plain_result("这次随机人物没有找到可用新图，稍后再试")
        finally:
            self.is_sending = False

    def _split_duo(self, text: str):
        parts = text.split()
        pairs = []
        for split_at in range(1, len(parts)):
            left_name, right_name = " ".join(parts[:split_at]), " ".join(parts[split_at:])
            left, right = self.index.resolve_character(left_name), self.index.resolve_character(right_name)
            if not isinstance(left, Resolved) or not isinstance(right, Resolved):
                continue
            if (left.alternatives and ":" not in left_name) or (right.alternatives and ":" not in right_name):
                continue
            pairs.append((left.character, right.character))
        return pairs[0] if len(pairs) == 1 else None

    @filter.command("合照")
    async def duo(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        names, rating, solo, filtered = self._parse_args(self._tail(event, "合照").split(), self._default_rating(event))
        if solo:
            yield event.plain_result("合照需要两个人物，不能同时指定单人")
            return
        pair = self._split_duo(names)
        if not pair:
            yield event.plain_result("请指定两个明确人物；例如：合照 原神:胡桃 原神:芙宁娜")
            return
        try:
            intent = duo_intent(*pair, event.unified_msg_origin, rating, filtered=filtered)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        self.is_sending = True
        try:
            success, message = await self._deliver(event, intent)
            if not success and message:
                yield event.plain_result("没有查到这两位同框的投稿" if "没有符合条件" in message else message)
        finally:
            self.is_sending = False

    def _split_variant(self, text: str):
        parts = text.split()
        matches = []
        for split_at in range(1, len(parts)):
            char_name, variant_name = " ".join(parts[:split_at]), " ".join(parts[split_at:])
            character = self.index.resolve_character(char_name)
            if isinstance(character, Resolved):
                variant = self.index.resolve_variant(character.character.id, variant_name)
                if variant:
                    matches.append((character.character, variant))
        return matches[0] if len(matches) == 1 else None

    @filter.command("装扮")
    async def variant(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        names, rating, solo, filtered = self._parse_args(self._tail(event, "装扮").split(), self._default_rating(event))
        if solo or filtered:
            yield event.plain_result("装扮暂不组合单人或筛选；例如：装扮 原神:胡桃 银河商店")
            return
        pair = self._split_variant(names)
        if not pair:
            yield event.plain_result("未找到已核对的该人物装扮；可先用 查角色 查看可选名称")
            return
        try:
            intent = variant_intent(*pair, event.unified_msg_origin, rating)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        self.is_sending = True
        try:
            success, message = await self._deliver(event, intent)
            if not success and message:
                yield event.plain_result(message)
        finally:
            self.is_sending = False
