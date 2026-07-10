"""preprocess 段 — 見開き左右分割・トリミング・正規化（バッチ）。

入力: work/<book>/raw/ の撮影生画像
出力: work/<book>/pages/ の確定ページ（単一カラム・UI無し）

実装チケット: P4(見開き分割＋トリミング)
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from PIL import Image, ImageStat

from . import naming
from .config import Config
from .state import State

logger = logging.getLogger(__name__)


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


def _input_signature(pcfg, raw_paths: list[Path]) -> str:
    """preprocess入力（config + raw集合）の署名を返す。

    分割/トリミング/黒画面閾値の設定変更、または raw の増減・並び変化があると
    署名が変わる。署名が前回と食い違えば pages/ を全再生成する（残留ページ混入を防ぐ）。
    """
    payload = json.dumps(
        {
            "split_spread": pcfg.split_spread,
            "trim": pcfg.trim or {},
            "min_brightness": pcfg.min_brightness,
            "raw": [p.name for p in raw_paths],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clear_pages(pages_dir: Path) -> None:
    """pages/ の既存 page_*.png を全削除して冪等な再生成を保証する。"""
    for p in pages_dir.glob("page_*.png"):
        p.unlink()


def process_all(
    cfg: Config,
    state: State,
    work_dir: str | Path | None = None,
    state_path: str | Path | None = None,
    force: bool = False,
) -> None:
    """raw/ の全画像を分割・トリミングし pages/ に確定ページとして書き出す。

    処理フロー（各 raw 画像ごと）:
        1. 黒画面異常フレーム除外（min_brightness 未満はスキップ）
        2. 見開き左右分割（split_spread が真なら1枚→2カラム、偽なら単ページ）
        3. 比率トリミングで UI・柱・余白を除去
        4. pages/page_NNNN.png に単一カラム・UI無しで連番出力

    見開きN枚を分割すると 2N ページになる。全て cfg.preprocess で切替可能。
    処理後の確定ページ数は state.pages_total に記録する。

    冪等クリアとレジュームを両立する（仕様 F-8）:
        - config か raw集合が前回と変わる／force=True → pages/ を全クリアして全再生成する
          （設定チューニング後の残留 page_*.png が後段 OCR/PDF に混入するのを防ぐ）。
        - 署名が一致し途中まで消化済み（中断→再実行）→ 消化済み raw をスキップして続行する。
    state_path 指定時は raw 1枚ごとに state を永続化し、途中Kill後も続きから再開できる。
    """
    pcfg = cfg.preprocess
    wd = Path(work_dir) if work_dir is not None else Path("work") / cfg.book_title
    raw_dir = wd / "raw"
    pages_dir = wd / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    raw_paths = sorted(raw_dir.glob("*.png"))
    sig = _input_signature(pcfg, raw_paths)

    # 署名一致かつ消化途中なら中断→再実行とみなしてレジューム。それ以外は全再生成。
    resume = (
        not force
        and state.preprocess_sig == sig
        and 0 < state.preprocess_raw_done < len(raw_paths)
    )
    if not resume:
        _clear_pages(pages_dir)
        state.preprocess_raw_done = 0
        state.pages_total = 0
    state.preprocess_sig = sig

    start = state.preprocess_raw_done
    page_no = state.pages_total
    skipped = 0
    logger.info(
        "preprocess 開始: raw %d 枚（%d 枚目から処理）", len(raw_paths), start + 1
    )

    for idx, rp in enumerate(raw_paths):
        if idx < start:
            continue  # 既に消化済み → レジュームで飛ばす

        with Image.open(rp) as im:
            img = im.convert("RGB")

        # 黒画面異常フレームを除外する（config で min_brightness を調整可能）
        if _mean_brightness(img) < pcfg.min_brightness:
            skipped += 1
            logger.warning("黒画面異常のためスキップ: %s", rp.name)
        else:
            # 見開きなら左右分割、単ページ運用なら分割しない
            columns = split_spread(img) if pcfg.split_spread else [img]
            for col in columns:
                trimmed = trim(col, pcfg.trim or {})
                page_no += 1
                out_path = pages_dir / naming.page_filename(page_no)
                trimmed.save(out_path)

        # raw 1枚を処理し終えた時点で進捗をコミット（レジューム単位）
        state.preprocess_raw_done = idx + 1
        state.pages_total = page_no
        if state_path is not None:
            state.save(state_path)

    state.pages_total = page_no
    if state_path is not None:
        state.save(state_path)
    logger.info(
        "preprocess 完了: raw %d 枚 → pages %d ページ（スキップ %d 枚）",
        len(raw_paths), page_no, skipped,
    )
