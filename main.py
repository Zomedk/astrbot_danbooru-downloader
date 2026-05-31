import os
import random
import io
import asyncio
import uuid
from PIL import Image as PILImage 
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage 

from .mapping import RAW_DICT_GENSHIN_IMPACT

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
BASE_URL = "https://danbooru.donmai.us/posts.json"

async def delay_delete_file(file_path: str, delay: int = 60):
    try:
        await asyncio.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"[Danbooru 生命周期] 临时缓存文件 {os.path.basename(file_path)} 已在后台安全期满销毁。")
    except Exception as e:
        logger.error(f"[Danbooru 生命周期] 后台清理缓存失败: {e}")

@register("astrbot_danbooru_downloader", "Zomedk", "Danbooru美图插件", "3.7.0")
class DanbooruDownloaderPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        self.username = self.config.get("username")
        self.api_key = self.config.get("api_key")
        self.proxy = self.config.get("proxy")

        import requests
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"EndfieldImageDownloader/1.0 ({self.username})",
            "Accept": "application/json"
        })

        # 硬性原子锁
        self.is_sending = False
        self.characters_map = self._parse_characters_map(RAW_DICT_GENSHIN_IMPACT)

        logger.info(f"[Danbooru INIT] 代理: {self.proxy} | 用户: {self.username}")
        logger.info(f"[Danbooru INIT] 已加载 {len(self.characters_map)} 个角色")

    def _parse_characters_map(self, raw_dict: dict) -> dict:
        flattened_map = {}
        for keys, tag in raw_dict.items():
            aliases = [alias.strip() for alias in keys.split("|")]
            for alias in aliases:
                if alias:
                    flattened_map[alias] = tag
        return flattened_map

    def _get_proxy_dict(self):
        if self.proxy:
            return {"http": self.proxy, "https": self.proxy}
        return None

    def _fetch_random_image(self, tag: str, rating: str = "all", order: str = "random", limit: int = 200) -> str:
        rating_map = {
            "all": "",
            "safe": "rating:g",
            "r18": "rating:e"
        }
        rating_param = rating_map.get(rating, "")
        
        tags = tag
        if rating_param:
            tags = f"{tags} {rating_param}"
        
        params = {
            "tags": tags,
            "limit": limit,
            "order": order
        }

        try:
            resp = self.session.get(
                BASE_URL,
                params=params,
                auth=(self.username, self.api_key),
                proxies=self._get_proxy_dict(),
                timeout=15
            )
            if resp.status_code != 200: return ""
            posts = resp.json()
            if not posts: return ""
            
            valid_urls = []
            for post in posts:
                img_url = post.get("file_url") or post.get("large_file_url")
                if img_url: valid_urls.append(img_url)
                
            logger.info(f"[Danbooru API 响应] 标签: [{tags}] | API返回原始贴文数: {len(posts)} | 成功解析出有效图片URL数: {len(valid_urls)}")
            
            if not valid_urls: return ""
            return random.choice(valid_urls)
        except Exception as e:
            logger.error(f"[Danbooru API 异常] 获取贴文列表失败: {e}")
            return ""

    def _download_bytes(self, url: str) -> bytes:
        try:
            resp = self.session.get(
                url,
                auth=(self.username, self.api_key),
                proxies=self._get_proxy_dict(),
                timeout=15
            )
            if resp.status_code != 200: return b""
            return resp.content
        except:
            return b""

    def _create_temp_file(self, img_bytes: bytes) -> str:
        tmp_dir = Path("/tmp/astrbot_img")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        file_path = tmp_dir / f"{uuid.uuid4().hex}.jpg"
        
        raw_size_kb = len(img_bytes) // 1024
        
        # 【方案 B 核心：本地智能压缩引擎】
        try:
            # 读入原始图片数据流
            image = PILImage.open(io.BytesIO(img_bytes))
            
            # 如果是动图（如 GIF），不进行压缩破坏，直接原始写入
            if getattr(image, "is_animated", False):
                with open(file_path, "wb") as f:
                    f.write(img_bytes)
                logger.info(f"[PIL 引擎] 检测到动图格式，已跳过压缩直接交付原始文件。")
                return str(file_path)
                
            # 如果是 PNG 等带有透明通道的，强制转成普通 RGB 模式以转存为 JPG
            if image.mode in ('RGBA', 'LA', 'P') or image.format == 'PNG':
                image = image.convert('RGB')
                
            # 核心魔法：保持原分辨率，使用 85% 压缩比保存为高品质 JPEG（肉眼无损的黄金分割点）
            output_buffer = io.BytesIO()
            image.save(output_buffer, format="JPEG", quality=85)
            compressed_bytes = output_buffer.getvalue()
            
            # 写入本地缓存文件
            with open(file_path, "wb") as f:
                f.write(compressed_bytes)
                
            comp_size_kb = len(compressed_bytes) // 1024
            logger.info(f"[PIL 压缩引擎] 原图体积: {raw_size_kb} KB -> 压缩后体积: {comp_size_kb} KB (分辨率保持 {image.width}x{image.height} 绝对无损)")
            
        except Exception as e:
            # 万一 PIL 遇到一些诡异格式报错，触发安全兜底，直接写入原图，确保机器人不罢工
            logger.warning(f"[PIL 引擎异常] 智能压缩失败: {e}，触发安全机制，改用原始体积递交。")
            with open(file_path, "wb") as f:
                f.write(img_bytes)
                
        return str(file_path)

    @filter.command("美图")
    async def meitu(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张美图正在努力上传中，请等发完再试！")
            return
        
        self.is_sending = True
        try:
            parts = event.message_str.replace("美图", "").strip().split()
            if not parts:
                yield event.plain_result("用法: 美图 角色名 [r18/safe]")
                return
            
            name = parts[0]
            rating = "all"
            if len(parts) > 1:
                param = parts[1].lower()
                if param == "r18": rating = "r18"
                elif param in ["safe", "全年龄"]: rating = "safe"
            
            if name not in self.characters_map:
                yield event.plain_result(f"角色 [{name}] 不存在")
                return
            
            tag = self.characters_map[name]
            msg_map = {"all": "全随机", "r18": "R18", "safe": "全年龄"}
            yield event.plain_result(f"🎲 {msg_map[rating]} | 正在获取 {name} 的图片...")
            
            success = False
            for attempt in range(2):
                if attempt > 0:
                    logger.info(f"[Danbooru 重试机制] 捕获到内核假死超时，正在触发第 {attempt+1} 次自动换图重试...")
                
                url = await asyncio.to_thread(self._fetch_random_image, tag, rating=rating, order="random", limit=200)
                if not url: continue
                
                img_bytes = await asyncio.to_thread(self._download_bytes, url)
                if not img_bytes: continue
                
                tmp_file = ""
                try:
                    # 内部已包含方案 B 智能压图
                    tmp_file = await asyncio.to_thread(self._create_temp_file, img_bytes)
                    logger.info(f"[Danbooru 递交] 文件准备就绪. 正在硬性等待内核回执...")
                    
                    out_chain = event.plain_result("")
                    out_chain.chain = [AstrImage(file=tmp_file)]
                    
                    await event.send(out_chain)
                    
                    asyncio.create_task(delay_delete_file(tmp_file, 60))
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"[Danbooru 递交失败] 成功捕获到内核偶发超时或异常: {e}，准备进行重试。")
                    if tmp_file and os.path.exists(tmp_file):
                        try: os.remove(tmp_file)
                        except: pass
            
            if not success:
                yield event.plain_result("❌ 发送失败，QQ 内核卡死超时，请稍后重试。")
                
        finally:
            self.is_sending = False
            logger.info("[Danbooru 锁机制] 流程完全结束，原子锁已释放。")

    @filter.command("新图")
    async def new_image(self, event: AstrMessageEvent):
        if self.is_sending:
            yield event.plain_result("⚠️ 上一张美图正在努力上传中，请等发完再试！")
            return
        
        self.is_sending = True
        try:
            parts = event.message_str.replace("新图", "").strip().split()
            if not parts:
                yield event.plain_result("用法: 新图 角色名 [r18/safe]")
                return
            
            name = parts[0]
            rating = "all"
            if len(parts) > 1:
                param = parts[1].lower()
                if param == "r18": rating = "r18"
                elif param in ["safe", "全年龄"]: rating = "safe"
            
            if name not in self.characters_map:
                yield event.plain_result(f"角色 [{name}] 不存在")
                return
            
            tag = self.characters_map[name]
            msg_map = {"all": "最新", "r18": "最新R18", "safe": "最新全年龄"}
            yield event.plain_result(f"🆕 {msg_map[rating]} | 正在获取 {name} 的图片...")
            
            success = False
            for attempt in range(2):
                if attempt > 0:
                    logger.info(f"[Danbooru 重试机制] 捕获到内核假死超时，正在触发第 {attempt+1} 次自动换图重试...")
                
                url = await asyncio.to_thread(self._fetch_random_image, tag, rating=rating, order="id", limit=30)
                if not url: continue
                
                img_bytes = await asyncio.to_thread(self._download_bytes, url)
                if not img_bytes: continue
                
                tmp_file = ""
                try:
                    tmp_file = await asyncio.to_thread(self._create_temp_file, img_bytes)
                    logger.info(f"[Danbooru 递交] 文件准备就绪. 正在硬性等待内核回执...")
                    
                    out_chain = event.plain_result("")
                    out_chain.chain = [AstrImage(file=tmp_file)]
                    
                    await event.send(out_chain)
                    
                    asyncio.create_task(delay_delete_file(tmp_file, 60))
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"[Danbooru 递交失败] 成功捕获到内核偶发超时或异常: {e}，准备进行重试。")
                    if tmp_file and os.path.exists(tmp_file):
                        try: os.remove(tmp_file)
                        except: pass
            
            if not success:
                yield event.plain_result("❌ 发送失败，QQ 内核卡死超时，请稍后重试。")
        finally:
            self.is_sending = False
            logger.info("[Danbooru 锁机制] 流程完全结束，原子锁已释放。")
