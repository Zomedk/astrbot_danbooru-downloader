import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.api.web import WebPage


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
        
        self._register_pages()
    
    def _register_pages(self):
        page = WebPage(
            name="danbooru_downloader",
            title="Danbooru图片下载器",
            path="/danbooru-downloader",
            template=self.plugin_dir / "pages" / "index.html"
        )
        self.context.web.register_page(page)
    
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
    
    async def _download_character_images(self, session: aiohttp.ClientSession, username: str, api_key: str, 
                                         char_name: str, char_tag: str, target_count: int) -> int:
        logger.info(f"开始获取角色 [{char_name}] 的图片")
        
        posts = await self._fetch_posts(session, username, api_key, char_tag, target_count * 2)
        
        image_posts = []
        for post in posts:
            file_url = post.get('file_url')
            if file_url and file_url.lower().endswith(IMAGE_EXTENSIONS):
                image_posts.append(post)
                if len(image_posts) >= target_count:
                    break
        
        if not image_posts:
            logger.info(f"未找到 [{char_name}] 的图片")
            return 0
        
        char_dir = self.download_dir / char_name
        char_dir.mkdir(parents=True, exist_ok=True)
        
        downloaded = 0
        tasks = []
        
        for i, post in enumerate(image_posts, 1):
            file_url = post['file_url']
            ext = Path(file_url).suffix
            filename = f"{i:02d}_收藏{post.get('fav_count', 0)}_ID{post['id']}{ext}"
            filename = self._clean_filename(filename)
            save_path = char_dir / filename
            
            tasks.append(self._download_image(session, file_url, save_path))
            
            if len(tasks) >= 5 or i == len(image_posts):
                results = await asyncio.gather(*tasks)
                downloaded += sum(results)
                tasks = []
                
                self.download_status["progress"] = int((i / len(image_posts)) * 100)
                self.download_status["downloaded"] = downloaded
                
                await asyncio.sleep(0.5)
        
        logger.info(f"角色 [{char_name}] 下载完成，共 {downloaded} 张")
        return downloaded
    
    async def _download_task(self, username: str, api_key: str, config: Dict):
        self.download_status["is_running"] = True
        self.download_status["errors"] = []
        
        max_workers = config.get("max_workers", 10)
        target_count = config.get("target_count", 50)
        
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=max_workers)) as session:
            total_downloaded = 0
            
            for char_name, char_tag in CHARACTERS_MAP.items():
                self.download_status["current_char"] = char_name
                self.download_status["progress"] = 0
                self.download_status["downloaded"] = 0
                
                try:
                    downloaded = await self._download_character_images(session, username, api_key, 
                                                                       char_name, char_tag, target_count)
                    total_downloaded += downloaded
                except Exception as e:
                    logger.error(f"下载角色 [{char_name}] 时出错: {e}")
                    self.download_status["errors"].append(f"{char_name}: {str(e)}")
                
                await asyncio.sleep(1)
        
        self.download_status["is_running"] = False
        self.download_status["current_char"] = ""
        self.download_status["total"] = total_downloaded
        
        logger.info(f"全部下载完成，共 {total_downloaded} 张图片")
    
    def api_start_download(self, config: Dict) -> Dict:
        """开始下载流程"""
        if self.download_status["is_running"]:
            return {"success": False, "message": "下载已在运行中"}
        
        username = config.get("username", "")
        api_key = config.get("api_key", "")
        
        if not username or not api_key:
            return {"success": False, "message": "请先配置Danbooru用户名和API Key"}
        
        asyncio.create_task(self._download_task(username, api_key, config))
        return {"success": True, "message": "下载任务已启动"}
    
    def api_get_status(self) -> Dict:
        """获取下载状态"""
        return self.download_status
    
    def api_get_characters(self) -> List[Dict]:
        """获取支持的角色列表"""
        return [{"name": name, "tag": tag} for name, tag in CHARACTERS_MAP.items()]
