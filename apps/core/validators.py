import os

from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_DIMENSION = 6000  # px, guards against decompression-bomb uploads


def validate_image_file(file):
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(f"Unsupported file extension '{ext}'. Allowed: {sorted(ALLOWED_IMAGE_EXTENSIONS)}")

    if file.size > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError(f"Image too large ({file.size} bytes). Maximum is {MAX_IMAGE_SIZE_BYTES} bytes.")

    content_type = getattr(file, "content_type", None)
    if content_type and content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValidationError(f"Unsupported MIME type '{content_type}'.")

    try:
        image = Image.open(file)
        image.verify()
    except Exception as exc:
        raise ValidationError("Uploaded file is not a valid image.") from exc
    finally:
        file.seek(0)

    with Image.open(file) as image:
        width, height = image.size
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            raise ValidationError(
                f"Image dimensions ({width}x{height}) exceed the maximum of "
                f"{MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION}px."
            )
    file.seek(0)


def validate_video_file(file):
    ext = os.path.splitext(file.name)[1].lower()
    allowed = {".mp4", ".mov", ".webm"}
    if ext not in allowed:
        raise ValidationError(f"Unsupported video extension '{ext}'. Allowed: {sorted(allowed)}")
