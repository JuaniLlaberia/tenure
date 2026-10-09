"""
Image resizing with Pillow: under Bluesky's size limit, and small thumbnails for the dashboard.
"""

from io import BytesIO

from PIL import Image, ImageOps

BLUESKY_LIMIT = 2_000_000
MAX_SIDE = 2000
KEEP_AS_IS = ("image/jpeg", "image/png", "image/webp")

def _open(data: bytes) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(BytesIO(data)))
    return image.convert("RGB") if image.mode not in ("RGB", "L") else image

def _jpeg(image: Image.Image, quality: int) -> bytes:
    out = BytesIO()
    image.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()

def size_of(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as image:
        return ImageOps.exif_transpose(image).size

def fit(data: bytes, mime_type: str, limit: int = BLUESKY_LIMIT) -> tuple[bytes, str]:
    """
    The image unchanged if it's small enough and a type Bluesky takes, otherwise a JPEG
    under the limit: at most 2000 px a side, lowering quality, then size, until it fits.
    """
    if len(data) <= limit and mime_type in KEEP_AS_IS:
        return data, mime_type
    image = _open(data)
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    quality = 85
    while True:
        out = _jpeg(image, quality)
        if len(out) <= limit:
            return out, "image/jpeg"
        if quality > 55:
            quality -= 10
        else:
            image = image.resize((int(image.width * 0.8), int(image.height * 0.8)))

def thumbnail(data: bytes, side: int = 480) -> bytes:
    image = _open(data)
    image.thumbnail((side, side))
    return _jpeg(image, 80)
