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
    assert cfg.capture.spread_mode is True  # 見開き/片ページの切替スイッチ
    assert cfg.ocr.languages == ["ja-JP", "en-US"]
    assert cfg.build.font == "HeiseiMin-W3"


def test_load_rejects_retired_split_spread_key(tmp_path):
    """廃止キー preprocess.split_spread は移行を促す明確なエラーで弾く。"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "book_title: x\npreprocess:\n  split_spread: false\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="capture.spread_mode"):
        Config.load(cfg_file)


def test_validate_rejects_unmeasured_region():
    cfg = Config.load(REPO / "config.example.yaml")  # region=[0,0,0,0]
    with pytest.raises(ValueError):
        cfg.validate()


def test_state_roundtrip(tmp_path):
    st = State(book_title="b", stage="ocr", captured=42, ocr_done=30)
    p = tmp_path / "state.json"
    st.save(p)
    loaded = State.load(p)
    assert loaded.stage == "ocr"
    assert loaded.captured == 42
    loaded.advance_stage()
    assert loaded.stage == "build"
