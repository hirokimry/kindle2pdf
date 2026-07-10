"""ocr 段 — Apple Vision OCR ラッパ（ocrmac）とバッチ処理。

PoC根拠: Visionは余分な空白がほぼ無く（0.01/字）、日本語の語句検索が壊れない。
分割済みページは単一カラムなので読み順は「yの大きい順（＝上から）」で単純ソート。

ocrmac は macOS 専用（extra: macos）。import は関数内で遅延させ、
非mac環境（CI ubuntu 等）でモジュール import 自体は失敗しないようにする。

実装チケット: P5(Vision OCR)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .config import Config
from .state import State

logger = logging.getLogger(__name__)

# 返り値要素: (text, confidence, [x, y, w, h])  座標は正規化(0..1)・原点左下
OcrItem = tuple[str, float, list[float]]

# pages/ から拾う画像拡張子（preprocess の出力形式に追従）
PAGE_IMAGE_EXTS = (".png", ".jpg", ".jpeg")

# 進捗ログを出す間隔（ページ数）。数百ページでも冗長すぎず追跡できる粒度。
_PROGRESS_EVERY = 25


def ocr_page(path: str | Path, cfg: Config) -> list[OcrItem]:
    """1ページを Vision OCR して (text, confidence, bbox) のリストを返す。"""
    from ocrmac import ocrmac  # 遅延import（macOS専用）

    result = ocrmac.OCR(
        str(path),
        language_preference=cfg.ocr.languages,
        recognition_level=cfg.ocr.recognition_level,
    ).recognize()
    # ocrmac の返り値 (text, confidence, bbox) をそのまま整形して返す
    return [(text, conf, list(bbox)) for text, conf, bbox in result]


def _page_images(pages_dir: Path) -> list[Path]:
    """pages/ 配下の画像ファイルを読み順（ファイル名昇順）で列挙する。"""
    return sorted(
        p for p in pages_dir.iterdir() if p.suffix.lower() in PAGE_IMAGE_EXTS
    )


def _write_page_json(out_path: Path, page_path: Path, items: list[OcrItem]) -> None:
    """1ページ分の OCR 結果を text/confidence/bbox として原子的に保存する。

    一時ファイルへ書いてから os.replace で置換し、途中Kill時の破損JSONを防ぐ。
    破損JSONを残すとレジュームが「OCR済み」と誤認してしまうため。
    """
    payload = {
        "page": page_path.stem,
        "source": str(page_path),
        "items": [
            {"text": text, "confidence": conf, "bbox": list(bbox)}
            for text, conf, bbox in items
        ],
    }
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, out_path)


def load_page_items(json_path: str | Path) -> list[OcrItem]:
    """保存済み ocr/page_XXXX.json を (text, confidence, bbox) タプル列に復元する。

    build 段（P6/P7）が OcrItem を直接扱えるようにするための読み取りヘルパ。
    """
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return [
        (item["text"], item["confidence"], list(item["bbox"]))
        for item in data["items"]
    ]


def ocr_all(
    cfg: Config,
    state: State | None = None,
    work_dir: str | Path | None = None,
    state_path: str | Path | None = None,
) -> None:
    """pages/ の全ページをOCRし ocr/page_XXXX.json に保存する。

    - 既に ocr/<stem>.json があるページはスキップし、未OCRページから続行する（レジューム）。
    - OCR完了数を state.ocr_done に記録する（state_path 指定時はページ毎に永続化）。
    - OCR失敗ページはログに記録して処理を継続する（JSON未作成→再開時に再試行される）。

    work_dir 未指定時は work/<book_title> を用いる（pipeline.work_dir と同一規約）。
    """
    if state is None:
        state = State()
    wd = Path(work_dir) if work_dir is not None else Path("work") / cfg.book_title
    pages_dir = wd / "pages"
    ocr_dir = wd / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    if not pages_dir.exists():
        logger.warning("pages ディレクトリが存在しません: %s（OCR対象なし）", pages_dir)
        state.ocr_done = 0
        if state_path is not None:
            state.save(state_path)
        return

    pages = _page_images(pages_dir)
    logger.info("OCR開始: %d ページ", len(pages))
    done = 0
    failed = 0
    for page_path in pages:
        out_path = ocr_dir / f"{page_path.stem}.json"
        if out_path.exists():
            # 既にOCR済み → スキップして未OCRページへ（レジューム）
            done += 1
            continue
        try:
            items = ocr_page(page_path, cfg)
        except Exception as exc:  # noqa: BLE001 — 1ページの失敗で全体を止めない
            failed += 1
            logger.warning("OCR失敗のためスキップ: %s（%s）", page_path.name, exc)
            continue
        _write_page_json(out_path, page_path, items)
        done += 1
        state.ocr_done = done
        if state_path is not None:
            state.save(state_path)
        # 数百ページでも追跡できるよう一定間隔で進捗を出す。
        if done % _PROGRESS_EVERY == 0:
            logger.info("OCR進捗: %d/%d ページ", done, len(pages))

    state.ocr_done = done
    if state_path is not None:
        state.save(state_path)
    logger.info(
        "OCR完了: %d/%d ページ（失敗 %d ページ）", done, len(pages), failed
    )
