#!/usr/bin/env python3
"""Unit tests for the image mime-type invariant in _process_image.

Home Assistant's image_proxy can mislabel a PNG as image/jpeg. If the MCP
emitted an image block whose declared media_type disagreed with its bytes, the
Anthropic vision API would reject it with a 400 — and because the block is
persisted into the session, it would re-400 on every subsequent turn, poisoning
the persona's session (~5s crash loop).

The guard: _process_image ALWAYS derives the returned mime_type from the magic
bytes of the bytes it actually emits, never from a declared/assumed label. These
tests assert that invariant holds — mime_type(returned) == sniff(emitted bytes).
"""

import base64
import os
import sys
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import _process_image, _detect_mime_type

VISION_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _make_png(color=(10, 20, 30)) -> bytes:
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg(color=(200, 100, 50)) -> bytes:
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_detect_mime_type_from_magic_bytes():
    # Each accepted format must be recognized from its signature alone.
    assert _detect_mime_type(_make_png()) == "image/png"
    assert _detect_mime_type(_make_jpeg()) == "image/jpeg"
    assert _detect_mime_type(b"GIF89a" + b"\x00" * 16) == "image/gif"
    assert _detect_mime_type(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8) == "image/webp"


def test_returned_mime_matches_emitted_bytes_for_png_input():
    # A PNG in → whatever bytes come out, the label must match those bytes.
    b64, mime, _orig = _process_image(_make_png(), {})
    emitted = base64.b64decode(b64)
    assert mime in VISION_MIMES
    assert mime == _detect_mime_type(emitted)


def test_returned_mime_matches_emitted_bytes_for_jpeg_input():
    b64, mime, _orig = _process_image(_make_jpeg(), {})
    emitted = base64.b64decode(b64)
    assert mime in VISION_MIMES
    assert mime == _detect_mime_type(emitted)


def test_ha_mislabeled_png_does_not_produce_incoherent_block():
    # Simulates HA serving real PNG bytes (the image_proxy mislabel scenario).
    # The emitted block must be self-consistent: the declared mime must match
    # the actual emitted bytes, never an arbitrary label that would 400 the API.
    png_bytes = _make_png()
    b64, mime, _orig = _process_image(png_bytes, {})
    emitted = base64.b64decode(b64)
    assert mime == _detect_mime_type(emitted), "declared mime must match emitted bytes"
    # The emitted bytes must be a real, recognizable vision image.
    assert _detect_mime_type(emitted) in VISION_MIMES


if __name__ == "__main__":
    test_detect_mime_type_from_magic_bytes()
    test_returned_mime_matches_emitted_bytes_for_png_input()
    test_returned_mime_matches_emitted_bytes_for_jpeg_input()
    test_ha_mislabeled_png_does_not_produce_incoherent_block()
    print("OK — all image mime invariant tests passed")
