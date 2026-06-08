"""Profile-picture image processing.

User-uploaded images are normalized server-side before they reach S3: decoded
(which validates that the bytes really are an image), flattened onto a
transparent canvas, downscaled to a small square-ish thumbnail, and re-encoded
as PNG. Re-encoding also strips any EXIF/metadata the original carried.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from gamatrix.constants import PROFILE_PIC_SIZE


def process_profile_pic(raw: bytes) -> bytes:
    """Validate, resize, and re-encode an uploaded image as a PNG thumbnail.

    Raises ValueError if the bytes aren't a readable image.
    """
    try:
        opened = Image.open(io.BytesIO(raw))
        opened.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file isn't a readable image.") from exc

    img = opened.convert("RGBA")
    # thumbnail() preserves aspect ratio, fitting within the bounding box.
    img.thumbnail((PROFILE_PIC_SIZE, PROFILE_PIC_SIZE))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
