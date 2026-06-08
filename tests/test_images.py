"""Tests for profile-pic image processing."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from gamatrix.constants import PROFILE_PIC_SIZE
from gamatrix.images import process_profile_pic


def _png_bytes(size: tuple[int, int], color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_process_resizes_large_image_within_box():
    out = process_profile_pic(_png_bytes((1000, 500)))
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert max(img.size) == PROFILE_PIC_SIZE  # longest side fits the box
    assert img.size == (PROFILE_PIC_SIZE, PROFILE_PIC_SIZE // 2)  # aspect kept


def test_process_leaves_small_image_dimensions_alone():
    out = process_profile_pic(_png_bytes((32, 32)))
    img = Image.open(io.BytesIO(out))
    assert img.size == (32, 32)


def test_process_rejects_non_image():
    with pytest.raises(ValueError, match="readable image"):
        process_profile_pic(b"this is not an image")
