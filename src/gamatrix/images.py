"""Profile-picture image processing.

User-uploaded images are normalized server-side before they are stored: decoded
(which validates that the bytes really are an image), flattened onto a
transparent canvas, downscaled to a small square-ish thumbnail, and re-encoded
as PNG. Re-encoding also strips any EXIF/metadata the original carried.

Decoding is the dangerous step. A small file can describe an enormous bitmap (a
"decompression bomb"); fully decoding it can exhaust a worker's CPU/RAM. We
guard against that twice: the header's declared dimensions are checked before
any pixels are read, and Pillow's own bomb detector is left armed as a backstop.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from gamatrix.constants import PROFILE_PIC_MAX_INPUT_PIXELS, PROFILE_PIC_SIZE

# Arm Pillow's built-in bomb detector at our own threshold: load() raises
# DecompressionBombError past 2x this, and warns past it, regardless of format.
Image.MAX_IMAGE_PIXELS = PROFILE_PIC_MAX_INPUT_PIXELS


def process_profile_pic(raw: bytes) -> bytes:
    """Validate, resize, and re-encode an uploaded image as a PNG thumbnail.

    Raises ValueError if the bytes aren't a readable image or describe a bitmap
    larger than PROFILE_PIC_MAX_INPUT_PIXELS.
    """
    try:
        opened = Image.open(io.BytesIO(raw))
        # open() only reads the header, so size is known before any pixels are
        # decoded. Reject oversized bitmaps here, before the expensive load().
        width, height = opened.size
        if width * height > PROFILE_PIC_MAX_INPUT_PIXELS:
            raise ValueError("That image is too large to process.")
        opened.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("That file isn't a readable image.") from exc

    img = opened.convert("RGBA")
    # thumbnail() preserves aspect ratio, fitting within the bounding box.
    img.thumbnail((PROFILE_PIC_SIZE, PROFILE_PIC_SIZE))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
