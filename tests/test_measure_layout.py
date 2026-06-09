#!/usr/bin/env python3
"""Unit tests for _measure_layout — objective dashboard-geometry measurement.

Hestia produced repeated false visual PASSes (claiming a dashboard was symmetric
when its right margin was 0 px). The fix is to measure geometry from pixels instead
of judging by eye. These tests pin the measurement on synthetic images so the verdict
is provably correct.
"""

import os
import sys
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import _measure_layout
from PIL import Image

BG = (239, 243, 247)   # pale background (top-left corner sample)
CARD = (20, 80, 90)    # dark teal card


def _img(W, H, boxes):
    im = Image.new("RGB", (W, H), BG)
    px = im.load()
    for (x0, y0, x1, y1) in boxes:
        for y in range(y0, min(y1, H)):
            for x in range(x0, min(x1, W)):
                px[x, y] = CARD
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_centered_is_symmetric():
    boxes = [(40, y, 560, y + 60) for y in range(60, 960, 120)]  # x=40..559 on 600-wide
    r = _measure_layout(_img(600, 1024, boxes))
    assert r.get("symmetric") is True, r
    assert abs(r["h_asymmetry_px"]) <= 8, r
    assert r["right_clip"] is False and r["left_clip"] is False, r


def test_pushed_right_is_asymmetric_and_clipped():
    boxes = [(40, y, 600, y + 60) for y in range(60, 960, 120)]  # glued to right edge
    r = _measure_layout(_img(600, 1024, boxes))
    assert r.get("symmetric") is False, r
    assert r["h_asymmetry_px"] >= 30, r          # left ~40, right ~0
    assert r["right_clip"] is True, r
    assert r["left_clip"] is False, r


def test_bottom_clip_detected():
    boxes = [(40, y, 560, y + 60) for y in range(60, 1024, 120)]  # reaches bottom edge
    r = _measure_layout(_img(600, 1024, boxes))
    assert r["bottom_clip"] is True, r


def test_blank_image_reports_error():
    im = Image.new("RGB", (600, 1024), BG)
    buf = BytesIO()
    im.save(buf, format="PNG")
    r = _measure_layout(buf.getvalue())
    assert "error" in r, r


if __name__ == "__main__":
    test_centered_is_symmetric()
    test_pushed_right_is_asymmetric_and_clipped()
    test_bottom_clip_detected()
    test_blank_image_reports_error()
    print("OK — all measure_layout tests passed")
