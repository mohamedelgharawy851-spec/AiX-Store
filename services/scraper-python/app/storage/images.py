from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from PIL import Image
from PIL import UnidentifiedImageError

from ..config import IMAGE_CACHE_DIR, PROXY_URL, REQUEST_TIMEOUT_SECONDS, USER_AGENTS
from ..utils import normalize_whitespace


def prepare_image_cache_dir() -> None:
    if IMAGE_CACHE_DIR.exists():
        for file_path in IMAGE_CACHE_DIR.iterdir():
            if file_path.is_file():
                file_path.unlink(missing_ok=True)
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _extension_for_format(image_format: str | None) -> str:
    mapping = {
        "avif": ".avif",
        "jpeg": ".jpg",
        "jpg": ".jpg",
        "png": ".png",
        "webp": ".webp",
        "gif": ".gif",
    }
    return mapping.get((image_format or "").lower(), ".jpg")


def _extension_for_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/avif": ".avif",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(normalized, ".jpg")


async def cache_image(image_url: str) -> dict | None:
    normalized_url = normalize_whitespace(image_url)
    if not normalized_url:
        return None

    headers = {"user-agent": USER_AGENTS[0], "accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    transport_args = {"proxy": PROXY_URL} if PROXY_URL else {}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True, **transport_args) as client:
        response = await client.get(normalized_url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            return None
        content = response.content
        if not content:
            return None

    from io import BytesIO

    width = 0
    height = 0
    image_format = None
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            width, height = image.size
            image_format = image.format
    except (UnidentifiedImageError, OSError, ValueError):
        if "image/" not in content_type.lower():
            return None

    image_key = hashlib.sha1(normalized_url.encode("utf-8")).hexdigest()
    extension = _extension_for_format(image_format)
    if width <= 0 or height <= 0:
        extension = _extension_for_content_type(content_type)
    file_path = IMAGE_CACHE_DIR / f"{image_key}{extension}"
    file_path.write_bytes(content)
    return {
        "local_image_key": image_key,
        "image_mime": content_type or f"image/{(image_format or 'jpeg').lower()}",
        "image_width": width,
        "image_height": height,
        "file_path": str(file_path),
    }


def resolve_image_path(local_image_key: str) -> Path | None:
    for file_path in IMAGE_CACHE_DIR.glob(f"{local_image_key}.*"):
        if file_path.is_file():
            return file_path
    return None
