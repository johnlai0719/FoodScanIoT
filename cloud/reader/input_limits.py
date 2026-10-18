"""Bound image allocations without resizing the caller's image."""
import base64
import binascii
import io
import os


class InputRejected(ValueError):
    def __init__(self, message, status_code=413):
        super().__init__(message)
        self.status_code = status_code


MAX_IMAGES = int(os.getenv("READER_MAX_IMAGES", "4"))
MAX_IMAGE_BYTES = int(os.getenv("READER_MAX_IMAGE_BYTES", str(6 * 1024**2)))
MAX_TOTAL_BYTES = int(os.getenv("READER_MAX_TOTAL_IMAGE_BYTES", str(12 * 1024**2)))
MAX_PIXELS = int(os.getenv("READER_MAX_IMAGE_PIXELS", "5000000"))
MAX_TOTAL_PIXELS = int(os.getenv("READER_MAX_TOTAL_PIXELS", "12000000"))
MAX_SIDE = int(os.getenv("READER_MAX_IMAGE_SIDE", "4096"))


def decode_images(values):
    from PIL import Image, UnidentifiedImageError
    if not values or len(values) > MAX_IMAGES:
        raise InputRejected("圖片數量必須為 1–%d 張" % MAX_IMAGES)
    images, total_bytes, total_pixels = [], 0, 0
    try:
        for value in values:
            if not isinstance(value, str):
                raise InputRejected("圖片必須是 Base64 字串", 400)
            payload = value.split("base64,", 1)[-1]
            if len(payload) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
                raise InputRejected("單張圖片檔案過大")
            try:
                raw = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise InputRejected("圖片 Base64 無效", 400) from exc
            total_bytes += len(raw)
            if len(raw) > MAX_IMAGE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise InputRejected("圖片檔案總量超過限制")
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ("JPEG", "PNG", "WEBP") or getattr(image, "n_frames", 1) != 1:
                        raise InputRejected("只接受靜態 JPEG、PNG、WEBP 圖片", 415)
                    w, h = image.size
                    total_pixels += w * h
                    if max(w, h) > MAX_SIDE or w * h > MAX_PIXELS or total_pixels > MAX_TOTAL_PIXELS:
                        raise InputRejected("圖片像素過大，請先使用 App 壓縮後再送出")
                    images.append(image.convert("RGB"))
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise InputRejected("圖片無法解碼或尺寸過大", 400) from exc
        return images
    except BaseException:
        for image in images:
            image.close()
        raise
