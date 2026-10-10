from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImageError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedImage:
    data_url: str
    width: int
    height: int
    original_filename: str


async def prepare_image(
    upload: UploadFile, *, max_bytes: int, max_pixels: int
) -> PreparedImage:
    raw = await upload.read(max_bytes + 1)
    if not raw:
        raise InvalidImageError("上传文件为空")
    if len(raw) > max_bytes:
        raise InvalidImageError(f"图片超过大小限制（最大 {max_bytes // 1024 // 1024} MB）")

    try:
        Image.MAX_IMAGE_PIXELS = max_pixels
        with Image.open(BytesIO(raw)) as source:
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            if width * height > max_pixels:
                raise InvalidImageError("图片像素总数超过限制")
            if width < 256 or height < 256:
                raise InvalidImageError("图片边长至少为 256 像素")
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image.convert("RGB"))
                image = background
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("文件不是有效或受支持的图片") from exc

    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return PreparedImage(
        data_url=f"data:image/jpeg;base64,{encoded}",
        width=width,
        height=height,
        original_filename=upload.filename or "unnamed",
    )
