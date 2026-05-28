import asyncio
import os
import random
import re
import uuid
from pathlib import Path
from typing import Dict, List

import aiohttp
from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Image


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


@register("astrbot_danbooru_downloader", "Zomedk", "Danbooru美图插件", "1.0.0")
class DanbooruDownloaderPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_dir = Path(__file__).parent
        self.temp_dir = self.plugin_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    async def _fetch_random_image(self, session: aiohttp.ClientSession, username: str, api_key: str, tag: str) -> str:
        url = "https://danbooru.donmai.us/posts.json"
        
        params = {
            "tags": f"{tag} rating:g",
            "limit": 50,
            "order": "random",
            "login": username,
            "api_key": api_key
        }
        
        try:
            async with session.get(url, params=params, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"API请求失败: {response.status}")
                    return ""
                
                data = await response.json()
                if not data:
                    return ""
                
                image_posts = [p for p in data if p.get('file_url') and p['file_url'].lower().endswith(IMAGE_EXTENSIONS)]
                
                if not image_posts:
                    return ""
                
                selected = random.choice(image_posts)
                return selected.get('file_url', "")
                
        except Exception as e:
            logger.error(f"获取图片失败: {e}")
            return ""
    
    async def _download_image(self, session: aiohttp.ClientSession, url: str) -> str:
        try:
            async with session.get(url, timeout=60) as response:
                if response.status != 200:
                    return ""
                
                ext = Path(url).suffix
                filename = f"temp_{uuid.uuid4().hex}{ext}"
                save_path = self.temp_dir / filename
                
                content = await response.read()
                await asyncio.to_thread(self._write_file, save_path, content)
                
                return str(save_path)
                
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            return ""
    
    def _write_file(self, path: Path, content: bytes):
        with open(path, 'wb') as f:
            f.write(content)

    @filter.command("美图")
    async def handle_meitu_command(self, event: AstrMessageEvent):
        # 获取命令参数
        char_name = event.message_str.replace("美图", "", 1).strip()
        
        if not char_name:
            chars = "\n".join(CHARACTERS_MAP.keys())
            yield event.plain_result(f"请指定角色名！\n用法：美图 <角色名>\n支持的角色：\n{chars}")
            return
        
        if char_name not in CHARACTERS_MAP:
            chars = "\n".join(CHARACTERS_MAP.keys())
            yield event.plain_result(f"未知角色！支持的角色：\n{chars}")
            return
        
        # 获取配置（与赛尔号插件相同的方式）
        username = self.context.plugin_config.get("username", "") if hasattr(self.context, 'plugin_config') else ""
        api_key = self.context.plugin_config.get("api_key", "") if hasattr(self.context, 'plugin_config') else ""
        
        if not username or not api_key:
            yield event.plain_result("请先在插件配置中填写Danbooru用户名和API Key")
            return
        
        yield event.plain_result(f"正在获取 [{char_name}] 的美图...")
        
        local_path = ""
        try:
            async with aiohttp.ClientSession() as session:
                image_url = await self._fetch_random_image(session, username, api_key, CHARACTERS_MAP[char_name])
                
                if not image_url:
                    yield event.plain_result(f"未找到角色 [{char_name}] 的图片")
                    return
                
                local_path = await self._download_image(session, image_url)
                
                if not local_path:
                    yield event.plain_result("下载图片失败")
                    return
                
                # 发送图片（使用旧版API的方式）
                yield event.image_result(local_path)
                
        except Exception as e:
            logger.error(f"发送美图失败: {e}")
            yield event.plain_result(f"发送失败: {str(e)}")
        finally:
            # 确保无论如何都清理临时文件
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {e}")
    
    @filter.command("美图角色")
    async def handle_characters_command(self, event: AstrMessageEvent):
        chars = "\n".join(CHARACTERS_MAP.keys())
        yield event.plain_result(f"支持的角色列表：\n{chars}")
    
    async def terminate(self):
        pass