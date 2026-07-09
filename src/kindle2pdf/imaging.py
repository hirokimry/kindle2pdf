"""pHash・明度チェックなどの共通画像ユーティリティ。

PoC実測の根拠:
    連続する別ページ間 = 距離16〜26 / 完全同一 = 0 / サムネ有無のみ差 = 6
    → サムネを出さない screencapture 前提で threshold=2 が安全。
"""

from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image, ImageStat


def phash(path: str | Path) -> imagehash.ImageHash:
    """perceptual hash を返す。"""
    with Image.open(path) as im:
        return imagehash.phash(im)


def hamming(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    """2つのハッシュのハミング距離。"""
    return a - b


def is_same(a: imagehash.ImageHash, b: imagehash.ImageHash, threshold: int) -> bool:
    """距離 <= threshold なら同一ページとみなす。"""
    return hamming(a, b) <= threshold


def mean_brightness(path: str | Path) -> float:
    """平均輝度（グレースケール）。黒画面異常フレームの検知に使う。"""
    with Image.open(path) as im:
        return ImageStat.Stat(im.convert("L")).mean[0]
