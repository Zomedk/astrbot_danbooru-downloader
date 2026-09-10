import os, random, io, asyncio, uuid
from collections import deque
from PIL import Image as PILImage
from pathlib import Path
from typing import Tuple
from time import time

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage, Node, Plain
from .mapping import RAW_DICT_GENSHIN_IMPACT

BASE_URL = "https://danbooru.donmai.us/posts.json"
RATING_MAP = {"all": "rating:g~rating:q", "safe": "rating:g", "r18": "rating:e"}

R18_COOLDOWN = 30
DEFAULT_NEW_LIMIT = 100
MAX_API_LIMIT = 200
RECENT_HISTORY = 3
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

async def delay_delete_file(file_path: str, delay: int = 60):
    try:
        await asyncio.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"[Danbooru] 已删除缓存 {os.path.basename(file_path)}")
    except asyncio.CancelledError:
        raise
    except OSError as e:
        logger.warning(f"[Danbooru] 删除缓存失败 {os.path.basename(file_path)}: {e}")

@register("astrbot_danbooru_downloader", "Zomedk", "Danbooru美图插件", "3.7.0")
class DanbooruDownloaderPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.username = self.config.get("username")
        self.api_key = self.config.get("api_key")
        self.proxy = self.config.get("proxy")
        self.is_sending = False
        self.last_r18_time = 0
        self.recent_post_ids = {}
        try:
            self.new_limit = max(
                1,
                min(int(self.config.get("new_limit", DEFAULT_NEW_LIMIT)), MAX_API_LIMIT),
            )
        except (TypeError, ValueError):
            self.new_limit = DEFAULT_NEW_LIMIT
        
        import requests
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"Danbooru/1.0 ({self.username})", "Accept": "application/json"})
        
        self.characters_map = {}
        for keys, tag in RAW_DICT_GENSHIN_IMPACT.items():
            for alias in keys.split("|"):
                if alias.strip():
                    self.characters_map[alias.strip()] = tag
        
        # 固定机器人信息
        self.bot_uin = 2807007579
        self.bot_name = "艾莉丝"
        
        logger.info(f"[Danbooru] 已加载 {len(self.characters_map)} 个角色")
        logger.info(f"[Danbooru] 机器人 UIN: {self.bot_uin}, 名称: {self.bot_name}")

    def _get_proxy_dict(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def _parse_args(self, args: list) -> Tuple[str, str, bool, bool]:
        if not args:
            return None, "all", False, False
        name = args[0]
        rating = "all"
        solo = False
        filtered = False
        for p in args[1:]:
            if p == "单人":
                solo = True
            elif p == "筛选":
                filtered = True
            elif p.lower() == "r18":
                rating = "r18"
            elif p.lower() in ["safe", "全年龄"]:
                rating = "safe"
        return name, rating, solo, filtered

    def _fetch_image(self, tag: str, rating: str, order: str, limit: int, filtered: bool = False) -> dict | None:
        tag_parts = [tag]
        if rating != "all" and RATING_MAP.get(rating):
            tag_parts.append(RATING_MAP[rating])
        if filtered:
            tag_parts.append("score:>30")
        tag_parts.append(f"order:{order}")
        tags = " ".join(tag_parts)
        limit = max(1, min(int(limit), MAX_API_LIMIT))
        
        try:
            resp = self.session.get(BASE_URL, params={"tags": tags, "limit": limit},
                                   auth=(self.username, self.api_key),
                                   proxies=self._get_proxy_dict(), timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[Danbooru] 查询失败，HTTP {resp.status_code}")
                return None
            posts = resp.json()
            candidates = [
                p for p in posts
                if isinstance(p, dict)
                and p.get("id") is not None
                and (p.get("file_url") or p.get("large_file_url"))
            ]
            logger.info(f"[Danbooru] 获取到 {len(candidates)} 张图片" + (" (筛选模式)" if filtered else ""))
            return random.choice(candidates) if candidates else None
        except Exception as e:
            logger.error(f"[Danbooru] API错误: {e}")
            return None

    def _download(self, url: str) -> bytes:
        try:
            resp = self.session.get(url, auth=(self.username, self.api_key),
                                   proxies=self._get_proxy_dict(), timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[Danbooru] 图片下载失败，HTTP {resp.status_code}")
                return b""
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                logger.warning("[Danbooru] 图片超过下载大小限制，已跳过")
                return b""
            content = resp.content
            if len(content) > MAX_DOWNLOAD_BYTES:
                logger.warning("[Danbooru] 图片超过下载大小限制，已跳过")
                return b""
            return content
        except (TypeError, ValueError):
            logger.warning("[Danbooru] 图片响应大小字段无效")
            return b""
        except Exception as e:
            logger.warning(f"[Danbooru] 图片下载异常: {e}")
            return b""

    def _compress(self, img_bytes: bytes) -> str:
        tmp_dir = Path("/tmp/astrbot_img")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        file_path = None
        
        try:
            img = PILImage.open(io.BytesIO(img_bytes))
            width, height = img.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素数超过限制")

            if getattr(img, "is_animated", False):
                image_format = (img.format or "").lower()
                if image_format not in {"gif", "webp"}:
                    raise ValueError(f"不支持的动图格式: {image_format or 'unknown'}")
                file_path = tmp_dir / f"{uuid.uuid4().hex}.{image_format}"
                with open(file_path, "wb") as f:
                    f.write(img_bytes)
                return str(file_path)
            
            file_path = tmp_dir / f"{uuid.uuid4().hex}.jpg"
            img.load()
            if img.mode in ('RGBA', 'LA', 'P') or img.format == 'PNG':
                img = img.convert('RGB')
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            with open(file_path, "wb") as f:
                f.write(buf.getvalue())
            
            logger.info(f"[Danbooru] 压缩: {len(img_bytes)//1024}KB -> {len(buf.getvalue())//1024}KB")
        except Exception as e:
            logger.warning(f"[Danbooru] 图片处理失败: {e}")
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass
            return ""
        return str(file_path)

    def _choose_post(self, posts: list[dict], query_key: str) -> dict | None:
        if not posts:
            return None

        recent = self.recent_post_ids.setdefault(
            query_key,
            deque(maxlen=RECENT_HISTORY),
        )
        fresh = [post for post in posts if post.get("id") not in recent]
        pool = fresh if fresh else posts
        chosen = random.choice(pool)
        recent.append(chosen["id"])
        return chosen

    async def _send_image(self, event: AstrMessageEvent, name: str, rating: str, solo: bool, order: str, icon: str, filtered: bool = False):
        tag = self.characters_map[name]
        if solo:
            tag = f"{tag} solo"

        if rating == "r18" and self.last_r18_time > 0:
            remaining = R18_COOLDOWN - (time() - self.last_r18_time)
            if remaining > 0:
                wait_time = int(remaining) + 1
                yield event.plain_result(f"⏳ R18 发送冷却中，请 {wait_time} 秒后重试")
                return
        
        rating_display = {"all": "全随机", "r18": "R18", "safe": "全年龄"}[rating]
        filter_text = " (筛选)" if filtered else ""
        yield event.plain_result(f"{icon} {rating_display}{' (单人)' if solo else ''}{filter_text} | 正在获取 {name} 的图片...")
        
        for attempt in range(2):
            if attempt > 0:
                logger.info(f"[Danbooru] 第 {attempt+1} 次重试...")
            
            posts = await asyncio.to_thread(self._fetch_image, tag, rating, order, self.new_limit if order == "id_desc" else 200, filtered)
            if not posts:
                continue

            query_key = f"{tag}|{rating}|{order}|{filtered}"
            post = self._choose_post(posts, query_key)
            if not post:
                continue
            url = post.get("file_url") or post.get("large_file_url")
            
            img_bytes = await asyncio.to_thread(self._download, url)
            if not img_bytes:
                continue
            
            tmp_file = ""
            try:
                tmp_file = await asyncio.to_thread(self._compress, img_bytes)
                if not tmp_file:
                    continue
                logger.info("[Danbooru] 发送中...")
                
                if rating == "r18":
                    logger.info("[Danbooru] R18 模式，使用合并转发...")
                    
                    # 固定使用机器人自己的信息
                    node = Node(
                        uin=3159302040,
                        name="艾莉丝",
                        content=[Plain("📸"), AstrImage(file=tmp_file)]
                    )
                    
                    self.last_r18_time = time()
                    result = event.chain_result([node])
                else:
                    chain = event.plain_result("")
                    chain.chain = [AstrImage(file=tmp_file)]
                    result = chain

                try:
                    yield result
                finally:
                    asyncio.create_task(delay_delete_file(tmp_file, 60))
                return
            except asyncio.TimeoutError:
                logger.warning(f"[Danbooru] 发送超时")
                if tmp_file and os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception as e:
                logger.warning(f"[Danbooru] 发送失败: {e}")
                if tmp_file and os.path.exists(tmp_file):
                    os.remove(tmp_file)
        
        yield event.plain_result("❌ 发送失败，请稍后重试")

    @filter.command("美图")
    async def meitu(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        self.is_sending = True
        try:
            parts = event.message_str.replace("美图", "").strip().split()
            name, rating, solo, filtered = self._parse_args(parts)
            if not name:
                yield event.plain_result("用法: 美图 角色名 [单人] [r18/safe] [筛选]")
                return
            if name not in self.characters_map:
                yield event.plain_result(f"角色 [{name}] 不存在")
                return
            async for result in self._send_image(event, name, rating, solo, "random", "🎲", filtered):
                yield result
        finally:
            self.is_sending = False

    @filter.command("新图")
    async def new_image(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张还在发送，请稍等")
            return
        self.is_sending = True
        try:
            parts = event.message_str.replace("新图", "").strip().split()
            name, rating, solo, filtered = self._parse_args(parts)
            if not name:
                yield event.plain_result("用法: 新图 角色名 [r18/safe] [筛选]")
                return
            if name not in self.characters_map:
                yield event.plain_result(f"角色 [{name}] 不存在")
                return
            async for result in self._send_image(event, name, rating, solo, "id_desc", "🆕", filtered):
                yield result
        finally:
            self.is_sending = False
