"""pipeline — 4段オーケストレーションとレジューム制御。

capture → preprocess → ocr → build を順に実行し、各段完了で state を進める。
途中Kill後も同じコマンドで未完了段/ページから続行できる。

撮影は「1 冊 = 複数 run」とし、run ごとに work/<book_title>/<日時>/ の専用ディレクトリを
切る。state もその run ディレクトリ内に置くため、2 回目以降も互いに上書きせず、破壊的な
削除なしで撮り直せる（Issue #31）。

実装チケット: P7(統合＋レジューム) / #31(run ディレクトリ化)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from . import build_pdf, capture, ocr, preprocess
from .build_pdf import OcrItem
from .config import Config
from .state import State

logger = logging.getLogger(__name__)

# run ディレクトリ名の日時フォーマット。固定幅で辞書順=作成時刻順になるようにし、
# _run_dirs のソートだけで新旧を判定できるようにする（外部ライブラリ不要）。
RUN_DIR_FORMAT = "%Y-%m-%d_%H%M%S"


def book_dir(cfg: Config) -> Path:
    """1 冊分の全 run を束ねる親ディレクトリ work/<book_title>。"""
    return Path("work") / cfg.book_title


def output_path(cfg: Config, run_dir: Path) -> Path:
    """検索可能PDFの出力先 <run_dir>/output/<book_title>.pdf。"""
    return run_dir / "output" / f"{cfg.book_title}.pdf"


def _run_dirs(bdir: Path) -> list[Path]:
    """book_dir 配下の run ディレクトリを名前昇順（=作成時刻昇順）で返す。

    calibrate.png 等のファイルは run ではないためディレクトリのみ拾う。
    """
    if not bdir.is_dir():
        return []
    return sorted((d for d in bdir.iterdir() if d.is_dir()), key=lambda p: p.name)


def _incomplete_run_dir(bdir: Path) -> Path | None:
    """未完了（stage!=done）の最新 run ディレクトリを返す。無ければ None。"""
    for d in reversed(_run_dirs(bdir)):
        state_path = d / "state.json"
        if state_path.exists() and State.load(state_path).stage != "done":
            return d
    return None


def resolve_run_dir(
    cfg: Config, *, resume: bool = True, now: datetime | None = None
) -> Path:
    """今回の撮影に使う run ディレクトリを決めて返す。

    未完了の run があれば継続し（resume=True 時）、無ければ日時付きの新規ディレクトリを
    作る。これにより 2 回目以降も前回結果を上書きせず、破壊的な削除なしで撮り直せる
    （Issue #31）。now はテスト時に時刻を固定するための注入口。
    """
    bdir = book_dir(cfg)
    if resume:
        existing = _incomplete_run_dir(bdir)
        if existing is not None:
            return existing
    stamp = (now or datetime.now()).strftime(RUN_DIR_FORMAT)
    run_dir = bdir / stamp
    # 同一秒に複数 run が始まった場合でも衝突しないよう連番で退避する。連番はゼロ埋めして
    # 「名前昇順 = 作成時刻順」の不変条件を保つ（"-10" が "-9" より前に来る辞書順崩れを防ぐ）。
    suffix = 2
    while run_dir.exists():
        run_dir = bdir / f"{stamp}-{suffix:02d}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _assemble_pages(run_dir: Path) -> list[tuple[str, list[OcrItem]]]:
    """pages/ を読み順に列挙し、対応する ocr/<stem>.json の items を束ねる。

    OCR JSON が無いページは画像のみ（テキスト層なし）で PDF 化する（仕様 4.3:
    OCR失敗ページは画像のみでPDF化）。ページ列挙は ocr._page_images と同一規約
    （ファイル名昇順・PAGE_IMAGE_EXTS）にして OCR と build のページ対応を保証する。
    """
    pages_dir = run_dir / "pages"
    ocr_dir = run_dir / "ocr"
    pages: list[tuple[str, list[OcrItem]]] = []
    for page_path in ocr._page_images(pages_dir):
        json_path = ocr_dir / f"{page_path.stem}.json"
        items = ocr.load_page_items(json_path) if json_path.exists() else []
        pages.append((str(page_path), items))
    return pages


def build_stage(cfg: Config, run_dir: Path) -> Path:
    """pages/ と ocr/ を束ねて検索可能PDFを1本生成し、出力パスを返す。

    途中Kill時に破損PDFを残さないよう、一時ファイルへ書いてから os.replace で
    確定する（ocr._write_page_json と同じ原子的置換）。build 段は単一PDF出力なので
    再実行は全再生成（冪等）となり、これがレジューム挙動を兼ねる。
    """
    out_path = output_path(cfg, run_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pages = _assemble_pages(run_dir)
    if not pages:
        # 確定ページ0枚は上流(capture/preprocess)が1枚も生成できなかったエラー状態。
        # ここで黙って戻ると run() が advance_stage() で stage を done に進め、PDFが
        # 出ていないのに正常終了に見えてしまう。明示的に例外を送出して停止する。
        raise RuntimeError(
            f"確定ページがありません（{run_dir / 'pages'} が空）。"
            "capture/preprocess が1ページも生成していない可能性があります。"
            "run ディレクトリの state.json と中身を確認してください。"
        )
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    build_pdf.build(pages, tmp_path, cfg)
    os.replace(tmp_path, out_path)
    logger.info("PDF生成完了: %s（%d ページ）", out_path, len(pages))
    return out_path


def run(
    cfg: Config,
    *,
    run_dir: Path | None = None,
    resume: bool = True,
    now: datetime | None = None,
) -> Path:
    """全段を順次実行し、使用した run ディレクトリを返す（レジューム対応）。

    run_dir 省略時は resolve_run_dir で「未完了 run の継続 or 新規作成」を自動決定する。
    run_dir を明示指定した場合はそのディレクトリで実行する（テスト・上級用途）。
    """
    cfg.validate()
    if run_dir is None:
        run_dir = resolve_run_dir(cfg, resume=resume, now=now)
    state_path = run_dir / "state.json"
    for sub in ("raw", "pages", "ocr", "output"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    state = State.load(state_path)

    if state.stage == "capture":
        logger.info("=== capture 段を開始します ===")
        capture.run_capture(cfg, state, run_dir, state_path)
        state.advance_stage(); state.save(state_path)
    if state.stage == "preprocess":
        logger.info("=== preprocess 段を開始します ===")
        # run_dir/state_path を渡し raw 1枚ごとに進捗を永続化する（capture 段と同じレジューム粒度）。
        preprocess.process_all(cfg, state, run_dir, state_path)
        state.advance_stage(); state.save(state_path)
    if state.stage == "ocr":
        logger.info("=== ocr 段を開始します ===")
        # run_dir/state_path を渡しページ1枚ごとに進捗を永続化する（未OCRページから再開）。
        ocr.ocr_all(cfg, state, run_dir, state_path)
        state.advance_stage(); state.save(state_path)
    if state.stage == "build":
        logger.info("=== build 段を開始します ===")
        build_stage(cfg, run_dir)
        state.advance_stage(); state.save(state_path)
    logger.info("=== 全段完了（stage=%s）===", state.stage)
    return run_dir
