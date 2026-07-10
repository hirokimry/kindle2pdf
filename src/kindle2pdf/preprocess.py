"""preprocess 段 — 見開き左右分割・トリミング・正規化（バッチ）。

入力: work/<book>/raw/ の撮影生画像
出力: work/<book>/pages/ の確定ページ（単一カラム・UI無し）

実装チケット: P4(見開き分割＋トリミング)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat

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
    """比率トリミングでUI・柱・余白を除去する。

    ratios が空 dict（全比率0）の場合は元画像と同一サイズを返すため、
    config で trim: {} と指定すれば実質的にトリミングを無効化できる。
    """
    w, h = img.size
    box = (
        int(w * ratios.get("left", 0.0)),
        int(h * ratios.get("top", 0.0)),
        int(w * (1 - ratios.get("right", 0.0))),
        int(h * (1 - ratios.get("bottom", 0.0))),
    )
    return img.crop(box)


def _mean_brightness(img: Image.Image) -> float:
    """開いた Image の平均輝度（グレースケール）。黒画面異常フレーム検知に使う。"""
    return ImageStat.Stat(img.convert("L")).mean[0]


def process_all(cfg: Config, state: State) -> None:
    """raw/ の全画像を分割・トリミングし pages/ に確定ページとして書き出す。

    処理フロー（各 raw 画像ごと）:
        1. 黒画面異常フレーム除外（min_brightness 未満はスキップ）
        2. 見開き左右分割（split_spread が真なら1枚→2カラム、偽なら単ページ）
        3. 比率トリミングで UI・柱・余白を除去
        4. pages/page_NNNN.png に単一カラム・UI無しで連番出力

    見開きN枚を分割すると 2N ページになる。全て cfg.preprocess で切替可能。
    処理後の確定ページ数は state.pages_total に記録する。
    """
    pcfg = cfg.preprocess
    work_dir = Path("work") / cfg.book_title
    raw_dir = work_dir / "raw"
    pages_dir = work_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    raw_paths = sorted(raw_dir.glob("*.png"))
    page_no = 0
    skipped = 0

    for rp in raw_paths:
        with Image.open(rp) as im:
            img = im.convert("RGB")

        # 黒画面異常フレームを除外する（config で min_brightness を調整可能）
        if _mean_brightness(img) < pcfg.min_brightness:
            skipped += 1
            print(f"[preprocess] 黒画面異常のためスキップ: {rp.name}")
            continue

        # 見開きなら左右分割、単ページ運用なら分割しない
        columns = split_spread(img) if pcfg.split_spread else [img]

        for col in columns:
            trimmed = trim(col, pcfg.trim or {})
            page_no += 1
            out_path = pages_dir / f"page_{page_no:04d}.png"
            trimmed.save(out_path)

    state.pages_total = page_no
    print(
        f"[preprocess] 完了: raw {len(raw_paths)} 枚 → pages {page_no} ページ"
        f"（スキップ {skipped} 枚）"
    )
