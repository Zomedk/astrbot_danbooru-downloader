import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

# 1. Danbooru API 认证信息
USERNAME = "TBML_kmd"
API_KEY = "Jxt2VuyBxaiCpQScbpbAkotH"

# 2. 5星角色映射表（按优先级排序）- 使用Danbooru官方标签
CHARACTERS_MAP = [
    ("艾尔黛拉", "ardelia_(arknights)"),
    ("别礼", "alesh_(arknights)"),
    ("管理员", "endministrator_(arknights)"),
    ("洁尔佩塔", "gelpetta_(arknights)"),
    ("骏卫", "wulfgard_(arknights)"),
    ("莱万汀", "laevatain_(arknights)"),
    ("黎风", "lifeng_(arknights)"),
    ("洛茜", "roxy_(arknights)"),
    ("汤汤", "tangtang_(arknights)"),
    ("伊冯", "yvonne_(arknights)"),
    ("余烬", "ember_(arknights)"),
    ("庄方宜", "zhuang_fangyi_(arknights)"),
    ("陈千语", "chen_qianyu_(arknights)"),
]

BASE_URL = "https://danbooru.donmai.us/posts.json"
SAVE_DIR = "./明日方舟终末地同人图"

HEADERS = {
    "User-Agent": f"EndfieldImageDownloader/1.0 ({USERNAME})"
}

PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}

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
    query_tags = f"{ch_tag} rating:g order:score"
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
        image_posts = []
        
        for post in posts:
            img_url = post.get("file_url") or post.get("large_file_url")
            if not img_url:
                no_url_count += 1
                continue

            ext = os.path.splitext(img_url.split("?")[0])[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                video_count += 1
                continue

            image_posts.append({
                "url": img_url,
                "fav_count": post.get("fav_count", 0),
                "post_id": post.get("id"),
                "char_dir": char_dir
            })
        
        print(f"   📊 API返回: {len(posts)}个 | 视频: {video_count}个 | 无URL: {no_url_count}个 | 实际图片: {len(image_posts)}张")
        
        return image_posts

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
    print(f"\n======== 开始获取干员 [{ch_name}] 的精致二创 ========")

    image_posts = get_character_posts(ch_name, ch_tag, target_count=50)

    if not image_posts:
        print(f"⚠️  [{ch_name}] 未找到符合条件的图片")
        return 0

    print(f"✅ 找到 {len(image_posts)} 张图片，开始并发下载...")

    downloaded_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(download_single_image, img_info, idx): idx
            for idx, img_info in enumerate(image_posts)
        }

        for future in as_completed(futures):
            success, result = future.result()
            if success:
                print(f" -> ✅ 已下载: {result}")
                downloaded_count += 1
            else:
                print(f" -> ❌ 下载失败: {result}")

            time.sleep(0.05)

    print(f"📥 [{ch_name}] 完成，共下载 {downloaded_count}/{len(image_posts)} 张图片")
    return downloaded_count

if __name__ == "__main__":
    start_time = time.time()
    print("🚀 自动化终末地5星角色美图收集引擎（并发版）启动...")
    print(f"📌 共 {len(CHARACTERS_MAP)} 个角色待下载\n")

    total_downloaded = 0
    for idx, (chinese_name, english_tag) in enumerate(CHARACTERS_MAP, 1):
        print(f"[{idx}/{len(CHARACTERS_MAP)}]", end="")
        count = download_character_images(chinese_name, english_tag, max_workers=10)
        total_downloaded += count
        time.sleep(1.5)

    end_time = time.time()
    print(f"\n🎉 全部完成！共下载 {total_downloaded} 张图片")
    print(f"⏱️  总耗时: {int(end_time - start_time)} 秒")
    print(f"📂 保存位置: {os.path.abspath(SAVE_DIR)}")
