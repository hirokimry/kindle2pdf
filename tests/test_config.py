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
    cfg.ocr.reading_order = "split"
    with pytest.raises(ValueError, match="reading_order"):
        cfg.validate()


def test_validate_accepts_ltr_reading_order():
    """reading_order=ltr は横書き見開き向けとして受理される。"""
    cfg = Config.load(REPO / "config.example.yaml")
    cfg.ocr.reading_order = "ltr"
    cfg.validate()  # 例外が出なければ OK


def test_load_ignores_retired_region_keys(tmp_path):
    """廃止された静的 region フォールバックのキー（region / auto_region）は無視して読み込む（Issue #47）。

    旧 config.yaml を手書きで残しているユーザーが未知キー TypeError で落ちないことを保証する。
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "book_title: x\ncapture:\n"
        "  auto_region: false\n  region: [1, 2, 300, 400]\n  app_name: \"Kindle\"\n",
        encoding="utf-8",
    )
    cfg = Config.load(cfg_file)  # TypeError を出さずに読み込めること
    assert cfg.capture.app_name == "Kindle"  # 廃止キー以外は通常どおり反映される
    assert not hasattr(cfg.capture, "region")  # 廃止キーはフィールドとして残らない


@pytest.mark.parametrize("bad", ["a/b", "..", ".", "\\x", "", "../escape", "sub/dir"])
def test_validate_rejects_unsafe_book_title(bad):
    """book_title のパス区切り・相対参照・空文字を弾く（work/ 外エスケープ防止・#32）。"""
    cfg = Config()
    cfg.book_title = bad
    with pytest.raises(ValueError):
        cfg.validate()


def test_validate_accepts_safe_book_title():
    """区切り文字を含まない通常の書名は通る。"""
    cfg = Config()
    cfg.book_title = "吾輩は猫である_上巻"
    cfg.validate()  # 例外が出なければ OK


def test_max_pages_default_is_unlimited():
    """max_pages の既定は 0（上限なし）になった（#45）。"""
    assert Config().capture.max_pages == 0


@pytest.mark.parametrize("value", [0, 1, 3000])
def test_validate_accepts_nonnegative_max_pages(value):
    """0（上限なし）と正の安全上限は通る（#45）。"""
    cfg = Config()
    cfg.capture.auto_region = True
    cfg.capture.max_pages = value
    cfg.validate()  # 例外が出なければ OK


def test_validate_rejects_negative_max_pages():
    """負の max_pages は明確なエラーで弾く（#45）。"""
    cfg = Config()
    cfg.capture.auto_region = True
    cfg.capture.max_pages = -1
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
