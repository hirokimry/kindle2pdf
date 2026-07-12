"""config / state の単体テスト（macOS依存なし）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from kindle2pdf.config import Config
from kindle2pdf.state import State

REPO = Path(__file__).resolve().parents[1]


def test_load_example_config():
    cfg = Config.load(REPO / "config.example.yaml")
    assert cfg.book_title == "sample-book"
    assert cfg.capture.same_threshold == 2
    assert cfg.ocr.reading_order == "rtl"  # 見開き2カラムの読み順方向
    assert cfg.ocr.languages == ["ja-JP", "en-US"]
    assert cfg.build.font == "HeiseiMin-W3"


def test_load_rejects_retired_spread_mode_key(tmp_path):
    """廃止キー capture.spread_mode は移行を促す明確なエラーで弾く（Issue #29）。"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "book_title: x\ncapture:\n  spread_mode: true\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="spread_mode は廃止"):
        Config.load(cfg_file)


def test_load_rejects_retired_split_spread_key(tmp_path):
    """廃止キー preprocess.split_spread は移行を促す明確なエラーで弾く。"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "book_title: x\npreprocess:\n  split_spread: false\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="split_spread は廃止"):
        Config.load(cfg_file)


def test_validate_rejects_retired_reading_order_value():
    """旧 reading_order 値（split / column）は移行を促す明確なエラーで弾く。"""
    cfg = Config.load(REPO / "config.example.yaml")
    cfg.capture.auto_region = True  # region 検証を回避して reading_order 検証まで通す
    cfg.ocr.reading_order = "split"
    with pytest.raises(ValueError, match="reading_order"):
        cfg.validate()


def test_validate_accepts_ltr_reading_order():
    """reading_order=ltr は横書き見開き向けとして受理される。"""
    cfg = Config.load(REPO / "config.example.yaml")
    cfg.capture.auto_region = True
    cfg.ocr.reading_order = "ltr"
    cfg.validate()  # 例外が出なければ OK


def test_validate_rejects_unmeasured_region():
    cfg = Config.load(REPO / "config.example.yaml")  # region=[0,0,0,0]
    cfg.capture.auto_region = False  # 静的 region 運用では未実測を弾く
    with pytest.raises(ValueError):
        cfg.validate()


def test_validate_skips_region_when_auto():
    """auto_region 時は静的 region 未実測でも validate が通る（実行時に自動算出）。"""
    cfg = Config.load(REPO / "config.example.yaml")  # region=[0,0,0,0]
    cfg.capture.auto_region = True
    cfg.validate()  # 例外が出なければ OK


def test_state_roundtrip(tmp_path):
    st = State(book_title="b", stage="ocr", captured=42, ocr_done=30)
    p = tmp_path / "state.json"
    st.save(p)
    loaded = State.load(p)
    assert loaded.stage == "ocr"
    assert loaded.captured == 42
    loaded.advance_stage()
    assert loaded.stage == "build"
