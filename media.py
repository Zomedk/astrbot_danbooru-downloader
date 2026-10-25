"""Download one chosen image and avoid unnecessary re-encoding."""

import io
import uuid
import warnings
from pathlib import Path

from PIL import Image as PILImage

if __package__:
    from .selector import MAX_DOWNLOAD_BYTES, MAX_IMAGE_PIXELS
else:  # Direct local module tests and offline benchmark
    from selector import MAX_DOWNLOAD_BYTES, MAX_IMAGE_PIXELS

DIRECT_BYTES = 3 * 1024 * 1024
MAX_SEND_SIDE = 2560


def choose_media_url(post: dict, prefer_large_above_mib: int = 3) -> str:
    try:
        original_size = int(post.get("file_size") or 0)
    except (TypeError, ValueError):
        original_size = 0
    large = post.get("large_file_url")
    original = post.get("file_url")
    if large and (not original or original_size > max(1, prefer_large_above_mib) * 1024 * 1024):
        return large
    return original or large or ""


def download_image(session, url: str, *, auth=None, proxies=None,
                   max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    response = session.get(url, auth=auth, proxies=proxies,
                           timeout=(5, 20), stream=True)
    try:
        if response.status_code != 200:
            raise ValueError(f"图片 HTTP {response.status_code}")
        try:
            reported_size = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            reported_size = 0
        if reported_size > max_bytes:
            raise ValueError("图片超过下载限制")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            if len(data) + len(chunk) > max_bytes:
                raise ValueError("图片超过下载限制")
            data.extend(chunk)
        return bytes(data)
    finally:
        response.close()


def prepare_image(data: bytes, directory: Path, *, direct_bytes: int = DIRECT_BYTES,
                  max_side: int = MAX_SEND_SIDE, max_pixels: int = MAX_IMAGE_PIXELS,
                  max_output_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
    if not data:
        raise ValueError("空图片")
    directory = Path(directory)
    with warnings.catch_warnings():
        warnings.simplefilter("error", PILImage.DecompressionBombWarning)
        try:
            image = PILImage.open(io.BytesIO(data))
        except (PILImage.DecompressionBombWarning, PILImage.DecompressionBombError) as exc:
            raise ValueError("图片像素数超过限制") from exc
    with image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise ValueError("图片像素数超过限制")
        fmt = (image.format or "").lower()
        if fmt not in {"jpeg", "png", "gif", "webp"}:
            raise ValueError("不支持的图片格式")
        animated = bool(getattr(image, "is_animated", False))
        extension = "jpg" if fmt == "jpeg" else fmt
        output = data
        if not animated and (len(data) > direct_bytes or max(width, height) > max_side):
            image.load()
            if max(width, height) > max_side:
                resampling = getattr(PILImage, "Resampling", PILImage)
                image.thumbnail((max_side, max_side), resampling.LANCZOS)
            has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
            buffer = io.BytesIO()
            if has_alpha:
                image.save(buffer, format="PNG")
                extension = "png"
            else:
                image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
                extension = "jpg"
            encoded = buffer.getvalue()
            # A larger re-encode only helps if the original dimensions exceeded
            # the send-side limit; otherwise send the existing valid file.
            if len(encoded) < len(data) or max(width, height) > max_side:
                output = encoded
            else:
                extension = "jpg" if fmt == "jpeg" else fmt
    if len(output) > max_output_bytes:
        raise ValueError("发送图片超过限制")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.{extension}"
    path.write_bytes(output)
    return path
