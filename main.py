import os, random, io, asyncio, uuid
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

async def delay_delete_file(file_path: str, delay: int = 60):
    await asyncio.sleep(delay)
    if os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"[Danbooru] 已删除缓存 {os.path.basename(file_path)}")

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

    def _fetch_image(self, tag: str, rating: str, order: str, limit: int, filtered: bool = False) -> str:
        tags = f"{tag} {RATING_MAP.get(rating, '')}" if rating != "all" else tag
        if filtered:
            tags = f"{tags} score:>30"
        
        try:
            resp = self.session.get(BASE_URL, params={"tags": tags, "limit": limit, "order": order},
                                   auth=(self.username, self.api_key),
                                   proxies=self._get_proxy_dict(), timeout=15)
            if resp.status_code != 200:
                return ""
            posts = resp.json()
            urls = [p.get("file_url") or p.get("large_file_url") for p in posts if p.get("file_url") or p.get("large_file_url")]
            logger.info(f"[Danbooru] 获取到 {len(urls)} 张图片" + (" (筛选模式)" if filtered else ""))
            return random.choice(urls) if urls else ""
        except Exception as e:
            logger.error(f"[Danbooru] API错误: {e}")
            return ""

    def _download(self, url: str) -> bytes:
        try:
            resp = self.session.get(url, auth=(self.username, self.api_key),
                                   proxies=self._get_proxy_dict(), timeout=15)
            return resp.content if resp.status_code == 200 else b""
        except:
            return b""

    def _compress(self, img_bytes: bytes) -> str:
        tmp_dir = Path("/tmp/astrbot_img")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        file_path = tmp_dir / f"{uuid.uuid4().hex}.jpg"
        
        try:
            img = PILImage.open(io.BytesIO(img_bytes))
            if getattr(img, "is_animated", False):
                with open(file_path, "wb") as f:
                    f.write(img_bytes)
                return str(file_path)
            
            if img.mode in ('RGBA', 'LA', 'P') or img.format == 'PNG':
                img = img.convert('RGB')
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            with open(file_path, "wb") as f:
                f.write(buf.getvalue())
            
            logger.info(f"[Danbooru] 压缩: {len(img_bytes)//1024}KB -> {len(buf.getvalue())//1024}KB")
        except Exception as e:
            logger.warning(f"[Danbooru] 压缩失败: {e}, 使用原始文件")
            with open(file_path, "wb") as f:
                f.write(img_bytes)
        return str(file_path)

    async def _send_image(self, event: AstrMessageEvent, name: str, rating: str, solo: bool, order: str, icon: str, filtered: bool = False):
        tag = self.characters_map[name]
        if solo:
            tag = f"{tag} solo"
        
        rating_display = {"all": "全随机", "r18": "R18", "safe": "全年龄"}[rating]
        filter_text = " (筛选)" if filtered else ""
        yield event.plain_result(f"{icon} {rating_display}{' (单人)' if solo else ''}{filter_text} | 正在获取 {name} 的图片...")
        
        for attempt in range(2):
            if attempt > 0:
                logger.info(f"[Danbooru] 第 {attempt+1} 次重试...")
            
            url = await asyncio.to_thread(self._fetch_image, tag, rating, order, 30 if order == "id" else 200, filtered)
            if not url:
                continue
            
            img_bytes = await asyncio.to_thread(self._download, url)
            if not img_bytes:
                continue
            
            tmp_file = ""
            try:
                tmp_file = await asyncio.to_thread(self._compress, img_bytes)
                logger.info("[Danbooru] 发送中...")
                
                if rating == "r18":
                    now = time()
                    elapsed = now - self.last_r18_time
                    if elapsed < R18_COOLDOWN and self.last_r18_time > 0:
                        wait_time = int(R18_COOLDOWN - elapsed) + 1
                        logger.info(f"[Danbooru] R18 冷却中，等待 {wait_time} 秒...")
                        yield event.plain_result(f"⏳ R18 发送冷却中，请 {wait_time} 秒后重试")
                        return
                    
                    logger.info("[Danbooru] R18 模式，使用合并转发...")
                    
                    # 固定使用机器人自己的信息
                    node = Node(
                        uin=3159302040,
                        name="艾莉丝",
                        content=[Plain("📸"), AstrImage(file=tmp_file)]
                    )
                    
                    self.last_r18_time = now
                    yield event.chain_result([node])
                else:
                    chain = event.plain_result("")
                    chain.chain = [AstrImage(file=tmp_file)]
                    yield chain
                
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
            async for result in self._send_image(event, name, rating, solo, "id", "🆕", filtered):
                yield result
        finally:
            self.is_sending = False
