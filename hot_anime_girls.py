import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 1. Danbooru API 认证信息（从环境变量读取）
USERNAME = os.getenv("DANBOORU_USERNAME", "")
API_KEY = os.getenv("DANBOORU_API_KEY", "")

if not USERNAME or not API_KEY:
    raise ValueError("请设置环境变量 DANBOORU_USERNAME 和 DANBOORU_API_KEY")

# 2. 热门二次元角色映射表（Danbooru官方标签）
CHARACTERS_MAP = [
    ("拉姆", "rem_(re:zero)"),
    ("雷姆", "ram_(re:zero)"),
    ("胡桃", "hutao_(genshin_impact)"),
    ("雷电将军", "raiden_shogun"),
    ("神里绫华", "kamisato_ayaka"),
    ("玛奇玛", "makima_(chainsaw_man)"),
    ("帕瓦", "power_(chainsaw_man)"),
    ("02", "zero_two_(darling_in_the_franxx)"),
    ("阿尼亚", "anya_forger"),
    ("间谍过家家", "yor_forger"),
]

BASE_URL = "https://danbooru.donmai.us/posts.json"
SAVE_DIR = "./hot_anime_girls"  # 新文件夹

HEADERS = {
    "User-Agent": f"HotAnimeDownloader/1.0 ({USERNAME})"
}

PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}

# 下载所有类型，包括图片和视频
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}

def sanitize_filename(filename):
    illegal_chars = r'[<>:"/\\|?*]'
    filename = re.sub(illegal_chars, '_', filename)
    filename = filename.replace(' ', '_')
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:196] + ext
    return filename

def get_character_posts(ch_name, ch_tag, target_count=50):
    # 不添加 rating:g 过滤，包括所有类型（包括R18）
    query_tags = f"{ch_tag} order:score"
    params = {
        "tags": query_tags,
        "limit": target_count
    }

    char_dir = os.path.join(SAVE_DIR, ch_name)
    os.makedirs(char_dir, exist_ok=True)

    try:
        response = requests.get(
            BASE_URL,
            params=params,
            headers=HEADERS,
            auth=(USERNAME, API_KEY),
            proxies=PROXIES,
            timeout=15
        )

        if response.status_code != 200:
            print(f"❌ [{ch_name}] 请求失败，状态码: {response.status_code}")
            return []

        posts = response.json()
        video_count = 0
        no_url_count = 0
        image_count = 0
        all_posts = []
        
        for post in posts:
            img_url = post.get("file_url") or post.get("large_file_url")
            if not img_url:
                no_url_count += 1
                continue

            ext = os.path.splitext(img_url.split("?")[0])[1].lower()
            if ext in {'.mp4', '.webm'}:
                video_count += 1
            elif ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}:
                image_count += 1
            
            if ext in DOWNLOAD_EXTENSIONS:
                all_posts.append({
                    "url": img_url,
                    "fav_count": post.get("fav_count", 0),
                    "post_id": post.get("id"),
                    "char_dir": char_dir
                })
        
        print(f"   📊 API返回: {len(posts)}个 | 图片: {image_count}个 | 视频: {video_count}个 | 无URL: {no_url_count}个 | 可下载: {len(all_posts)}个")
        
        return all_posts

    except Exception as e:
        print(f"❌ [{ch_name}] 获取列表失败: {e}")
        return []

def download_single_image(img_info, idx):
    img_url = img_info["url"]
    fav_count = img_info["fav_count"]
    post_id = img_info["post_id"]
    char_dir = img_info["char_dir"]

    ext = os.path.splitext(img_url.split("?")[0])[1]
    filename = f"{idx+1:02d}_收藏{fav_count}_ID{post_id}{ext}"
    filename = sanitize_filename(filename)
    file_path = os.path.join(char_dir, filename)

    try:
        img_response = requests.get(
            img_url,
            headers=HEADERS,
            auth=(USERNAME, API_KEY),
            proxies=PROXIES,
            timeout=10
        )
        if img_response.status_code == 200:
            with open(file_path, "wb") as f:
                f.write(img_response.content)
            return True, filename
        else:
            return False, f"状态码: {img_response.status_code}"
    except Exception as e:
        return False, str(e)

def download_character_images(ch_name, ch_tag, max_workers=10):
    print(f"\n======== 开始获取角色 [{ch_name}] ========")

    all_posts = get_character_posts(ch_name, ch_tag, target_count=100)  # 请求100个，增加成功率

    if not all_posts:
        print(f"⚠️  [{ch_name}] 未找到可下载的内容")
        return 0

    print(f"✅ 找到 {len(all_posts)} 个，开始并发下载...")

    downloaded_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(download_single_image, img_info, idx): idx
            for idx, img_info in enumerate(all_posts)
        }

        for future in as_completed(futures):
            success, result = future.result()
            if success:
                print(f" -> ✅ 已下载: {result}")
                downloaded_count += 1
            else:
                print(f" -> ❌ 下载失败: {result}")

            time.sleep(0.05)

    print(f"📥 [{ch_name}] 完成，共下载 {downloaded_count}/{len(all_posts)} 个")
    return downloaded_count

if __name__ == "__main__":
    start_time = time.time()
    print("🚀 热门二次元角色收集引擎（全类型）启动...")
    print(f"📌 共 {len(CHARACTERS_MAP)} 个角色待下载\n")

    total_downloaded = 0
    for idx, (chinese_name, english_tag) in enumerate(CHARACTERS_MAP, 1):
        print(f"[{idx}/{len(CHARACTERS_MAP)}]", end="")
        count = download_character_images(chinese_name, english_tag, max_workers=10)
        total_downloaded += count
        time.sleep(1.5)

    end_time = time.time()
    print(f"\n🎉 全部完成！共下载 {total_downloaded} 个文件")
    print(f"⏱️  总耗时: {int(end_time - start_time)} 秒")
    print(f"📂 保存位置: {os.path.abspath(SAVE_DIR)}")
