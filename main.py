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
        """从 Danbooru API 获取随机图片 URL"""
        url = "https://danbooru.donmai.us/posts.json"
        
        # 使用 Basic Auth（与本地脚本一致）
        auth = aiohttp.BasicAuth(username, api_key)
        
        params = {
            "tags": f"{tag} rating:g",
            "limit": 50,
            "order": "random"
        }
        
        # 完整的请求头，模拟浏览器
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        
        logger.info(f"[DEBUG] 请求 Danbooru API: url={url}, tag={tag}, username={username}")
        
        try:
            async with session.get(
                url, 
                params=params, 
                headers=headers, 
                auth=auth, 
                timeout=30
            ) as response:
                logger.info(f"[DEBUG] API 响应状态码: {response.status}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"[DEBUG] API请求失败: status={response.status}, response={error_text[:500]}")
                    return ""
                
                data = await response.json()
                logger.info(f"[DEBUG] API返回数据量: {len(data)} 条")
                
                if not data:
                    logger.warning(f"[DEBUG] API返回空数据: tag={tag}")
                    return ""
                
                # 过滤有效图片（有 file_url 且是图片格式）
                valid_posts = []
                for idx, post in enumerate(data):
                    file_url = post.get('file_url')
                    if file_url:
                        ext = os.path.splitext(file_url.split("?")[0])[1].lower()
                        if ext in IMAGE_EXTENSIONS:
                            valid_posts.append(post)
                            logger.debug(f"[DEBUG] 有效图片 #{idx+1}: {file_url}")
                    else:
                        logger.debug(f"[DEBUG] 跳过无 URL 的帖子: id={post.get('id')}")
                
                logger.info(f"[DEBUG] 有效图片数量: {len(valid_posts)}/{len(data)}")
                
                if not valid_posts:
                    logger.warning(f"[DEBUG] 无有效图片: tag={tag}")
                    return ""
                
                # 随机选择一张
                selected = random.choice(valid_posts)
                selected_url = selected.get('file_url', '')
                logger.info(f"[DEBUG] 选中图片: {selected_url}, 收藏数: {selected.get('fav_count', 0)}, ID: {selected.get('id')}")
                
                return selected_url
                
        except asyncio.TimeoutError:
            logger.error(f"[DEBUG] 请求超时: tag={tag}, timeout=30s")
            return ""
        except aiohttp.ClientResponseError as e:
            logger.error(f"[DEBUG] HTTP 响应错误: {e.status} - {e.message}, tag={tag}")
            return ""
        except aiohttp.ClientConnectorError as e:
            logger.error(f"[DEBUG] 连接错误: {e}, tag={tag}")
            return ""
        except aiohttp.ClientError as e:
            logger.error(f"[DEBUG] 网络请求错误: {type(e).__name__}: {e}, tag={tag}")
            return ""
        except Exception as e:
            logger.error(f"[DEBUG] 未知错误: {type(e).__name__}: {e}, tag={tag}")
            import traceback
            logger.error(f"[DEBUG] 堆栈跟踪: {traceback.format_exc()}")
            return ""
    
    async def _download_image(self, session: aiohttp.ClientSession, url: str) -> str:
        """下载图片到本地临时目录"""
        logger.info(f"[DEBUG] 开始下载图片: {url}")
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
            
            async with session.get(url, headers=headers, timeout=60) as response:
                logger.info(f"[DEBUG] 下载响应状态码: {response.status}")
                
                if response.status != 200:
                    logger.error(f"[DEBUG] 下载失败: HTTP {response.status}")
                    return ""
                
                content = await response.read()
                logger.info(f"[DEBUG] 下载内容大小: {len(content)} bytes")
                
                # 从 URL 中提取扩展名
                ext = os.path.splitext(url.split("?")[0])[1]
                if not ext or ext.lower() not in IMAGE_EXTENSIONS:
                    # 尝试从 Content-Type 获取
                    content_type = response.headers.get('Content-Type', '')
                    if 'png' in content_type:
                        ext = '.png'
                    elif 'jpeg' in content_type or 'jpg' in content_type:
                        ext = '.jpg'
                    elif 'gif' in content_type:
                        ext = '.gif'
                    elif 'webp' in content_type:
                        ext = '.webp'
                    else:
                        ext = '.jpg'  # 默认
                        logger.warning(f"[DEBUG] 未知扩展名，使用默认 .jpg, URL: {url}")
                
                filename = f"temp_{uuid.uuid4().hex}{ext}"
                save_path = self.temp_dir / filename
                
                logger.info(f"[DEBUG] 保存图片到: {save_path}")
                
                # 异步写入文件
                await asyncio.to_thread(self._write_file, save_path, content)
                
                logger.info(f"[DEBUG] 图片下载完成: {filename}")
                return str(save_path)
                
        except asyncio.TimeoutError:
            logger.error(f"[DEBUG] 下载超时: {url}")
            return ""
        except aiohttp.ClientError as e:
            logger.error(f"[DEBUG] 下载网络错误: {type(e).__name__}: {e}")
            return ""
        except Exception as e:
            logger.error(f"[DEBUG] 下载未知错误: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[DEBUG] 堆栈跟踪: {traceback.format_exc()}")
            return ""
    
    def _write_file(self, path: Path, content: bytes):
        """同步写入文件"""
        with open(path, 'wb') as f:
            f.write(content)
    
    async def _send_as_forward(self, event: AstrMessageEvent, image_path: str, character_name: str) -> None:
        """使用转发消息发送图片（更安全，不易被风控）"""
        logger.info(f"[DEBUG] 使用转发消息模式发送图片: {character_name}, path={image_path}")
        
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
            logger.info(f"[DEBUG] 转发消息发送成功: {character_name}")
            
        except Exception as e:
            logger.error(f"[DEBUG] 转发消息发送失败: {e}，降级为直接发送")
            # 降级处理
            yield event.image_result(image_path)
    
    async def _send_as_direct(self, event: AstrMessageEvent, image_path: str, character_name: str) -> None:
        """直接发送图片"""
        logger.info(f"[DEBUG] 使用直接发送模式: {character_name}, path={image_path}")
        yield event.image_result(image_path)
        logger.info(f"[DEBUG] 直接发送成功: {character_name}")

    @filter.command("美图")
    async def handle_meitu_command(self, event: AstrMessageEvent):
        # 获取命令参数
        char_name = event.message_str.replace("美图", "", 1).strip()
        
        logger.info(f"[DEBUG] 收到美图命令: char_name='{char_name}', 原始消息='{event.message_str}'")
        
        if not char_name:
            chars = "\n".join(CHARACTERS_MAP.keys())
            logger.info(f"[DEBUG] 未指定角色名，返回帮助信息")
            yield event.plain_result(f"请指定角色名！\n用法：美图 <角色名>\n支持的角色：\n{chars}")
            return
        
        if char_name not in CHARACTERS_MAP:
            chars = "\n".join(CHARACTERS_MAP.keys())
            logger.warning(f"[DEBUG] 未知角色: {char_name}")
            yield event.plain_result(f"未知角色！支持的角色：\n{chars}")
            return
        
        # 检查配置
        if not self.username or not self.api_key:
            logger.error(f"[DEBUG] 配置缺失: username={bool(self.username)}, api_key={bool(self.api_key)}")
            yield event.plain_result("请先在插件配置中填写Danbooru用户名和API Key")
            return
        
        logger.info(f"[DEBUG] 配置检查通过: username={self.username}")
        yield event.plain_result(f"正在获取 [{char_name}] 的美图...")
        
        local_path = ""
        try:
            # 创建带超时配置的 ClientSession
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            timeout = aiohttp.ClientTimeout(total=60)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                logger.info(f"[DEBUG] 开始获取图片URL: tag={CHARACTERS_MAP[char_name]}")
                image_url = await self._fetch_random_image(session, self.username, self.api_key, CHARACTERS_MAP[char_name])
                
                if not image_url:
                    logger.error(f"[DEBUG] 获取图片URL失败: {char_name}")
                    yield event.plain_result(f"未找到角色 [{char_name}] 的图片")
                    return
                
                logger.info(f"[DEBUG] 获取到图片URL: {image_url}")
                local_path = await self._download_image(session, image_url)
                
                if not local_path:
                    logger.error(f"[DEBUG] 下载图片失败: {image_url}")
                    yield event.plain_result("下载图片失败")
                    return
                
                logger.info(f"[DEBUG] 图片下载完成: {local_path}")
                
                # 根据配置选择发送方式
                if self.send_mode == "forward":
                    await self._send_as_forward(event, local_path, char_name)
                else:
                    await self._send_as_direct(event, local_path, char_name)
                
        except Exception as e:
            logger.error(f"[DEBUG] 发送美图异常: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[DEBUG] 堆栈跟踪: {traceback.format_exc()}")
            yield event.plain_result(f"发送失败: {str(e)}")
        finally:
            # 清理临时文件
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                    logger.info(f"[DEBUG] 临时文件已删除: {local_path}")
                except Exception as e:
                    logger.error(f"[DEBUG] 清理临时文件失败: {e}")
    
    @filter.command("美图角色")
    async def handle_characters_command(self, event: AstrMessageEvent):
        chars = "\n".join(CHARACTERS_MAP.keys())
        logger.info(f"[DEBUG] 返回支持的角色列表，共 {len(CHARACTERS_MAP)} 个")
        yield event.plain_result(f"支持的角色列表：\n{chars}")
    
    async def terminate(self):
        logger.info("[DEBUG] Danbooru插件正在卸载...")
        pass
