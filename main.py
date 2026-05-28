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
from astrbot.core.message.components import Node, Nodes


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
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.plugin_dir = Path(__file__).parent
        self.temp_dir = self.plugin_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存配置
        self.config = config if config else {}
        
        # 读取配置
        self.username = self.config.get("username", "")
        self.api_key = self.config.get("api_key", "")
        # 发送方式：direct=直接发送，forward=转发消息（更安全）
        self.send_mode = self.config.get("send_mode", "forward")
        
        if self.username:
            logger.info(f"Danbooru插件配置加载成功: username={self.username}, send_mode={self.send_mode}")
        else:
            logger.warning("Danbooru插件配置未找到 username 或 api_key")
    
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
    
    async def _send_as_forward(self, event: AstrMessageEvent, image_path: str, character_name: str) -> None:
        """使用转发消息发送图片（更安全，不易被风控）"""
        try:
            # 获取机器人自身ID
            self_id = str(event.get_self_id() or "123456")
            
            # 构建转发消息节点
            node = Node(
                name="Danbooru美图",
                uin=self_id,
                content=[
                    Plain(f"🎨 {character_name} 的美图\n"),
                    Image(file=image_path),
                    Plain(f"\n📝 来源: Danbooru | 仅供欣赏")
                ]
            )
            
            forward_msg = Nodes(nodes=[node])
            yield event.chain_result([forward_msg])
            logger.info(f"已通过转发消息发送图片: {character_name}")
            
        except Exception as e:
            logger.error(f"转发消息发送失败，降级为直接发送: {e}")
            # 降级处理
            yield event.image_result(image_path)
    
    async def _send_as_direct(self, event: AstrMessageEvent, image_path: str, character_name: str) -> None:
        """直接发送图片"""
        yield event.image_result(image_path)
        logger.info(f"已直接发送图片: {character_name}")

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
        
        # 检查配置
        if not self.username or not self.api_key:
            yield event.plain_result("请先在插件配置中填写Danbooru用户名和API Key")
            return
        
        yield event.plain_result(f"正在获取 [{char_name}] 的美图...")
        
        local_path = ""
        try:
            async with aiohttp.ClientSession() as session:
                image_url = await self._fetch_random_image(session, self.username, self.api_key, CHARACTERS_MAP[char_name])
                
                if not image_url:
                    yield event.plain_result(f"未找到角色 [{char_name}] 的图片")
                    return
                
                local_path = await self._download_image(session, image_url)
                
                if not local_path:
                    yield event.plain_result("下载图片失败")
                    return
                
                # 根据配置选择发送方式
                if self.send_mode == "forward":
                    await self._send_as_forward(event, local_path, char_name)
                else:
                    await self._send_as_direct(event, local_path, char_name)
                
        except Exception as e:
            logger.error(f"发送美图失败: {e}")
            yield event.plain_result(f"发送失败: {str(e)}")
        finally:
            # 清理临时文件
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