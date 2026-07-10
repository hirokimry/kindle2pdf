"""pipeline — 4段オーケストレーションとレジューム制御。

capture → preprocess → ocr → build を順に実行し、各段完了で state を進める。
途中Kill後も同じコマンドで未完了段/ページから続行できる。

実装チケット: P7(統合＋レジューム)
"""

from __future__ import annotations

from pathlib import Path

from . import build_pdf, capture, ocr, preprocess
from .config import Config
from .state import State


def work_dir(cfg: Config) -> Path:
    return Path("work") / cfg.book_title


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
        preprocess.process_all(cfg, state)
        state.advance_stage(); state.save(state_path)
    if state.stage == "ocr":
        ocr.ocr_all(cfg, state)
        state.advance_stage(); state.save(state_path)
    if state.stage == "build":
        # pages/ と ocr/ を突き合わせて build_pdf.build(...) を呼ぶ（P7で実装）
        raise NotImplementedError("P7: pages/ と ocr/ を束ねて build を呼ぶ結線を実装する")
