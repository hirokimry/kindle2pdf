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
    assert cfg.ocr.languages == ["ja-JP", "en-US"]
    assert cfg.build.font == "HeiseiMin-W3"


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
