"""preprocess 段 — 見開き左右分割・トリミング・正規化（バッチ）。

入力: work/<book>/raw/ の撮影生画像
出力: work/<book>/pages/ の確定ページ（単一カラム・UI無し）

実装チケット: P4(見開き分割＋トリミング)
"""

from __future__ import annotations

from PIL import Image

from .config import Config
from .state import State


def split_spread(img: Image.Image) -> list[Image.Image]:
    """見開き画像を中央で左右2分割する（読み順: 左→右）。"""
    w, h = img.size
    mid = w // 2
    left = img.crop((0, 0, mid, h))
    right = img.crop((mid, 0, w, h))
    return [left, right]


def trim(img: Image.Image, ratios: dict) -> Image.Image:
    """比率トリミングでUI・柱・余白を除去する。"""
    w, h = img.size
    box = (
        int(w * ratios.get("left", 0.0)),
        int(h * ratios.get("top", 0.0)),
        int(w * (1 - ratios.get("right", 0.0))),
        int(h * (1 - ratios.get("bottom", 0.0))),
    )
    return img.crop(box)


def process_all(cfg: Config, state: State) -> None:
    """raw/ の全画像を分割・トリミングし pages/ に確定ページとして書き出す。"""
    raise NotImplementedError("P4: raw/→pages/ のバッチ処理を実装する")
