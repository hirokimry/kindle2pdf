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


def hex_to_hash(hex_str: str) -> imagehash.ImageHash:
    """16進文字列（state.last_hash 等）から pHash を復元する。"""
    return imagehash.hex_to_hash(hex_str)


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


def crop_top_fraction(path: str | Path, fraction: float) -> None:
    """画像の上端を高さ比率 fraction だけ切り落として上書き保存する。

    Why: `screencapture -l` はウィンドウ全体（macOS タイトルバー帯を含む）を撮る。
    その帯 **だけ** を落とすため、本文側は一切触らない。比率で指定するのは、
    retina 倍率が環境で変わっても pt→px 換算が不要になり同じ結果を保つため
    （fraction はウィンドウ座標でも画像座標でも同一）。fraction は AX 実測の
    タイトルバー高さ ÷ ウィンドウ高さから求める（固定 px を持たない）。
    """
    if fraction <= 0:
        return
    with Image.open(path) as im:
        w, h = im.size
        top = round(h * fraction)
        # 帯が画像全体を覆う異常値では切らない（本文喪失を防ぐ安全弁）。
        if top <= 0 or top >= h:
            return
        cropped = im.crop((0, top, w, h))
    cropped.save(path)
