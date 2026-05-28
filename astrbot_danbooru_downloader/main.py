import json
import os
from pathlib import Path
from astrbot.api.star import Star, Context
from astrbot.api.logger import Logger

PLUGIN_NAME = "astrbot_danbooru_downloader"

class DanbooruDownloaderPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.logger = Logger()
        
        # 获取插件数据目录
        self.plugin_data_dir = Path(f"./data/plugins/{PLUGIN_NAME}")
        self.download_dir = self.plugin_data_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        # 注册插件API
        context.register_web_api(f"/{PLUGIN_NAME}/characters", self.get_characters, ["GET"], "获取角色列表")
        context.register_web_api(f"/{PLUGIN_NAME}/download", self.start_download, ["POST"], "开始下载")
        context.register_web_api(f"/{PLUGIN_NAME}/status", self.get_status, ["GET"], "获取下载状态")
        context.register_web_api(f"/{PLUGIN_NAME}/characters/save", self.save_characters, ["POST"], "保存角色列表")
        
        # 默认角色映射表
        self.character_map = {
            "拉姆": "rem_(re:zero)",
            "雷姆": "ram_(re:zero)",
            "胡桃": "hutao_(genshin_impact)",
            "雷电将军": "raiden_shogun",
            "神里绫华": "kamisato_ayaka",
            "玛奇玛": "makima_(chainsaw_man)",
            "帕瓦": "power_(chainsaw_man)",
            "02": "zero_two_(darling_in_the_franxx)",
            "阿尼亚": "anya_forger",
            "约尔": "yor_forger",
            "管理员": "endministrator_(arknights)",
            "艾尔黛拉": "ardelia_(arknights)",
            "佩丽卡": "perlica_(arknights)",
            "陈千语": "chen_qianyu_(arknights)",
        }
        
        self.download_status = {
            "is_running": False,
            "current": "",
            "downloaded": 0,
            "total": 0,
            "errors": []
        }
    
    async def get_characters(self) -> dict:
        """获取所有可用角色列表"""
        return {
            "success": True,
            "characters": list(self.character_map.keys())
        }
    
    async def save_characters(self) -> dict:
        """保存选中的角色列表"""
        from quart import request
        data = await request.get_json()
        selected = data.get("characters", [])
        
        # 保存到插件配置
        if "config" not in self.context.plugin_config:
            self.context.plugin_config["config"] = {}
        self.context.plugin_config["config"]["selected_characters"] = selected
        
        # 保存配置到文件
        config_path = self.plugin_data_dir / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"selected_characters": selected}, f, ensure_ascii=False, indent=2)
        
        return {"success": True, "message": "角色列表已保存"}
    
    async def start_download(self) -> dict:
        """开始下载流程"""
        if self.download_status["is_running"]:
            return {"success": False, "message": "下载已在运行中"}
        
        config = self.context.plugin_config.get("config", {})
        username = config.get("username", "")
        api_key = config.get("api_key", "")
        
        if not username or not api_key:
            return {"success": False, "message": "请先配置用户名和API Key"}
        
        # 启动后台下载任务
        import asyncio
        asyncio.create_task(self._download_task(username, api_key, config))
        
        return {"success": True, "message": "下载任务已启动"}
    
    async def _download_task(self, username: str, api_key: str, config: dict):
        """后台下载任务"""
        import requests
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        self.download_status["is_running"] = True
        self.download_status["errors"] = []
        
        try:
            # 获取配置
            target_count = config.get("target_count", 50)
            max_workers = config.get("max_workers", 10)
            
            headers = {
                "User-Agent": f"DanbooruDownloader/1.0 ({username})"
            }
            proxies = {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
            }
            
            # 获取要下载的角色列表
            selected_chars = config.get("characters", [])
            if isinstance(selected_chars, str):
                try:
                    selected_chars = json.loads(selected_chars)
                except:
                    selected_chars = list(self.character_map.keys())[:5]
            
            if not selected_chars:
                selected_chars = ["拉姆", "雷姆", "胡桃"]
            
            total_downloaded = 0
            
            for char_name in selected_chars:
                if char_name not in self.character_map:
                    continue
                
                self.download_status["current"] = char_name
                char_dir = self.download_dir / char_name
                char_dir.mkdir(parents=True, exist_ok=True)
                
                # 获取角色标签
                tag = self.character_map[char_name]
                query_tags = f"{tag} rating:g order:score"
                params = {"tags": query_tags, "limit": target_count}
                
                try:
                    # 获取帖子列表
                    response = requests.get(
                        "https://danbooru.donmai.us/posts.json",
                        params=params,
                        headers=headers,
                        auth=(username, api_key),
                        proxies=proxies,
                        timeout=15
                    )
                    
                    if response.status_code != 200:
                        self.download_status["errors"].append(f"{char_name}: API请求失败")
                        continue
                    
                    posts = response.json()
                    self.download_status["total"] = len(posts)
                    self.download_status["downloaded"] = 0
                    
                    # 下载图片
                    downloaded = 0
                    for idx, post in enumerate(posts):
                        img_url = post.get("file_url") or post.get("large_file_url")
                        if not img_url:
                            continue
                        
                        ext = os.path.splitext(img_url.split("?")[0])[1].lower()
                        if ext not in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}:
                            continue
                        
                        fav_count = post.get("fav_count", 0)
                        post_id = post.get("id")
                        filename = f"{idx+1:02d}_收藏{fav_count}_ID{post_id}{ext}"
                        file_path = char_dir / filename
                        
                        try:
                            img_response = requests.get(
                                img_url,
                                headers=headers,
                                auth=(username, api_key),
                                proxies=proxies,
                                timeout=10
                            )
                            if img_response.status_code == 200:
                                with open(file_path, "wb") as f:
                                    f.write(img_response.content)
                                downloaded += 1
                                self.download_status["downloaded"] = downloaded
                        except Exception as e:
                            pass
                        
                        await asyncio.sleep(0.05)
                    
                    total_downloaded += downloaded
                    self.logger.info(f"{char_name}: 下载完成 {downloaded} 张图片")
                    
                except Exception as e:
                    self.download_status["errors"].append(f"{char_name}: {str(e)}")
                    self.logger.error(f"{char_name}: {str(e)}")
                
                await asyncio.sleep(1.5)
            
            self.download_status["is_running"] = False
            self.logger.info(f"全部完成！共下载 {total_downloaded} 张图片")
            
        except Exception as e:
            self.download_status["is_running"] = False
            self.download_status["errors"].append(str(e))
            self.logger.error(f"下载任务失败: {str(e)}")
    
    async def get_status(self) -> dict:
        """获取下载状态"""
        return {
            "success": True,
            "status": self.download_status,
            "download_dir": str(self.download_dir)
        }
    
    def __del__(self):
        pass
