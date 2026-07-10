"""pipeline — 4段オーケストレーションとレジューム制御。

capture → preprocess → ocr → build を順に実行し、各段完了で state を進める。
途中Kill後も同じコマンドで未完了段/ページから続行できる。

実装チケット: P7(統合＋レジューム)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import build_pdf, capture, ocr, preprocess
from .build_pdf import OcrItem
from .config import Config
from .state import State

logger = logging.getLogger(__name__)


def work_dir(cfg: Config) -> Path:
    return Path("work") / cfg.book_title


def output_path(cfg: Config, wd: Path) -> Path:
    """検索可能PDFの出力先 work/<book>/output/<book_title>.pdf。"""
    return wd / "output" / f"{cfg.book_title}.pdf"


def _assemble_pages(wd: Path) -> list[tuple[str, list[OcrItem]]]:
    """pages/ を読み順に列挙し、対応する ocr/<stem>.json の items を束ねる。

    OCR JSON が無いページは画像のみ（テキスト層なし）で PDF 化する（仕様 4.3:
    OCR失敗ページは画像のみでPDF化）。ページ列挙は ocr._page_images と同一規約
    （ファイル名昇順・PAGE_IMAGE_EXTS）にして OCR と build のページ対応を保証する。
    """
    pages_dir = wd / "pages"
    ocr_dir = wd / "ocr"
    pages: list[tuple[str, list[OcrItem]]] = []
    for page_path in ocr._page_images(pages_dir):
        json_path = ocr_dir / f"{page_path.stem}.json"
        items = ocr.load_page_items(json_path) if json_path.exists() else []
        pages.append((str(page_path), items))
    return pages


def build_stage(cfg: Config, wd: Path) -> Path:
    """pages/ と ocr/ を束ねて検索可能PDFを1本生成し、出力パスを返す。

    途中Kill時に破損PDFを残さないよう、一時ファイルへ書いてから os.replace で
    確定する（ocr._write_page_json と同じ原子的置換）。build 段は単一PDF出力なので
    再実行は全再生成（冪等）となり、これがレジューム挙動を兼ねる。
    """
    out_path = output_path(cfg, wd)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pages = _assemble_pages(wd)
    if not pages:
        # 確定ページ0枚は上流(capture/preprocess)が1枚も生成できなかったエラー状態。
        # ここで黙って戻ると run() が advance_stage() で stage を done に進め、PDFが
        # 出ていないのに正常終了に見えてしまう。明示的に例外を送出して停止する。
        raise RuntimeError(
            f"確定ページがありません（{wd / 'pages'} が空）。"
            "capture/preprocess が1ページも生成していない可能性があります。"
            "state.json と work/ の内容を確認してください。"
        )
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    build_pdf.build(pages, tmp_path, cfg)
    os.replace(tmp_path, out_path)
    logger.info("PDF生成完了: %s（%d ページ）", out_path, len(pages))
    return out_path


def run(cfg: Config, state_path: str | Path) -> None:
    """全段を順次実行する（レジューム対応）。"""
    cfg.validate()
    state = State.load(state_path)
    wd = work_dir(cfg)
    (wd / "raw").mkdir(parents=True, exist_ok=True)
    (wd / "pages").mkdir(parents=True, exist_ok=True)
    (wd / "ocr").mkdir(parents=True, exist_ok=True)
    (wd / "output").mkdir(parents=True, exist_ok=True)

    if state.stage == "capture":
        capture.run_capture(cfg, state, wd, state_path)
        state.advance_stage(); state.save(state_path)
    if state.stage == "preprocess":
        # wd/state_path を渡し raw 1枚ごとに進捗を永続化する（capture 段と同じレジューム粒度）。
        preprocess.process_all(cfg, state, wd, state_path)
        state.advance_stage(); state.save(state_path)
    if state.stage == "ocr":
        # wd/state_path を渡しページ1枚ごとに進捗を永続化する（未OCRページから再開）。
        ocr.ocr_all(cfg, state, wd, state_path)
        state.advance_stage(); state.save(state_path)
    if state.stage == "build":
        build_stage(cfg, wd)
        state.advance_stage(); state.save(state_path)
