import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Plain
from astrbot.api.event import filter, AstrMessageEvent


CHARACTERS_MAP = {
    "管理员": "endministrator_(arknights)",
    "佩丽卡": "perlica_(arknights)",
    "艾尔黛拉": "ardelia_(arknights)",
    "陈千语": "chen_qianyu_(arknights)",
    "胡桃": "hu_tao_(genshin_impact)",
    "雷电将军": "raiden_shogun_(genshin_impact)",
    "神里绫华": "kamisato_ayaka_(genshin_impact)",
    "拉姆": "ram_(re:zero)",
    "雷姆": "rem_(re:zero)",
    "玛奇玛": "makima_(chainsaw_man)",
    "帕瓦": "power_(chainsaw_man)",
    "02": "zero_two_(darling_in_the_franxx)",
    "阿尼亚": "anya_forger",
    "约尔": "yor_briar",
}

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


@register("astrbot_danbooru_downloader", "Zomedk", "Danbooru图片下载器", "1.0.0")
class DanbooruDownloaderPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_dir = Path(__file__).parent
        self.download_dir = self.plugin_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        self.download_status = {
            "is_running": False,
            "current_char": "",
            "progress": 0,
            "total": 0,
            "downloaded": 0,
            "errors": []
        }
    
    def _clean_filename(self, filename: str) -> str:
        invalid_chars = r'[\\/:*?"<>|]'
        return re.sub(invalid_chars, '_', filename)
    
    async def _fetch_posts(self, session: aiohttp.ClientSession, username: str, api_key: str, tag: str, limit: int) -> List[Dict]:
        url = "https://danbooru.donmai.us/posts.json"
        posts = []
        page = 1
        
        while len(posts) < limit:
            params = {
                "tags": tag,
                "limit": min(100, limit - len(posts)),
                "page": page,
                "login": username,
                "api_key": api_key
            }
            
            try:
                async with session.get(url, params=params, timeout=30) as response:
                    if response.status != 200:
                        logger.error(f"API请求失败: {response.status}")
                        break
                    data = await response.json()
                    if not data:
                        break
                    posts.extend(data)
                    page += 1
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"获取帖子失败: {e}")
                break
        
        return posts
    
    async def _download_image(self, session: aiohttp.ClientSession, url: str, save_path: Path) -> bool:
        try:
            async with session.get(url, timeout=60) as response:
                if response.status == 200:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(save_path, 'wb') as f:
                        f.write(await response.read())
                    return True
                else:
                    logger.warning(f"下载失败 {response.status}: {url}")
                    return False
        except Exception as e:
            logger.error(f"下载图片时发生异常: {e}")
            return False
    
    async def _download_single_character(self, username: str, api_key: str, char_name: str, count: int) -> str:
        self.download_status["is_running"] = True
        self.download_status["current_char"] = char_name
        
        char_tag = CHARACTERS_MAP[char_name]
        char_dir = self.download_dir / char_name
        char_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            async with aiohttp.ClientSession() as session:
                posts = await self._fetch_posts(session, username, api_key, char_tag, count * 2)
                
                image_posts = []
                for post in posts:
                    file_url = post.get('file_url')
                    if file_url and file_url.lower().endswith(IMAGE_EXTENSIONS):
                        image_posts.append(post)
                        if len(image_posts) >= count:
                            break
                
                if not image_posts:
                    return f"未找到角色 [{char_name}] 的图片"
                
                downloaded = 0
                total = len(image_posts)
                
                for i, post in enumerate(image_posts, 1):
                    file_url = post['file_url']
                    ext = Path(file_url).suffix
                    filename = f"{i:02d}_收藏{post.get('fav_count', 0)}_ID{post['id']}{ext}"
                    filename = self._clean_filename(filename)
                    save_path = char_dir / filename
                    
                    if await self._download_image(session, file_url, save_path):
                        downloaded += 1
                    
                    self.download_status["progress"] = int((i / total) * 100)
                    self.download_status["downloaded"] = downloaded
                
                return f"角色 [{char_name}] 下载完成！\n成功下载: {downloaded}/{total} 张\n保存位置: {char_dir}"
        
        except Exception as e:
            logger.error(f"下载角色 [{char_name}] 时出错: {e}")
            return f"下载失败: {str(e)}"
        
        finally:
            self.download_status["is_running"] = False
            self.download_status["current_char"] = ""
    
    @filter.command("danbooru下载")
    async def handle_download_command(self, event: AstrMessageEvent):
        args = event.message_str.replace("danbooru下载", "", 1).strip().split()
        
        if not args:
            yield event.plain_result("用法：danbooru下载 <角色名> [数量]\n例如：danbooru下载 管理员 50")
            return
        
        char_name = args[0]
        count = int(args[1]) if len(args) > 1 else 50
        
        if char_name not in CHARACTERS_MAP:
            chars = "\n".join(CHARACTERS_MAP.keys())
            yield event.plain_result(f"未知角色！支持的角色：\n{chars}")
            return
        
        if self.download_status["is_running"]:
            yield event.plain_result("下载任务正在进行中，请稍后再试")
            return
        
        config = self.context.plugin_config.get("config", {})
        username = config.get("username", "")
        api_key = config.get("api_key", "")
        
        if not username or not api_key:
            yield event.plain_result("请先在插件配置中填写Danbooru用户名和API Key")
            return
        
        yield event.plain_result(f"开始下载角色 [{char_name}] 的图片，目标数量：{count}")
        
        result = await self._download_single_character(username, api_key, char_name, count)
        yield event.plain_result(result)
    
    @filter.command("danbooru状态")
    async def handle_status_command(self, event: AstrMessageEvent):
        if self.download_status["is_running"]:
            msg = f"正在下载: {self.download_status['current_char']}\n"
            msg += f"进度: {self.download_status['progress']}%\n"
            msg += f"已下载: {self.download_status['downloaded']} 张"
        else:
            if self.download_status["total"] > 0:
                msg = f"上次下载完成！共 {self.download_status['total']} 张图片"
            else:
                msg = "当前无下载任务"
        yield event.plain_result(msg)
    
    @filter.command("danbooru角色")
    async def handle_characters_command(self, event: AstrMessageEvent):
        chars = "\n".join(CHARACTERS_MAP.keys())
        yield event.plain_result(f"支持的角色列表：\n{chars}")
    
    async def terminate(self):
        pass