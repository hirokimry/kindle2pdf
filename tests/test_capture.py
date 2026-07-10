"""capture の撮影ループ・最終ページ検出の単体テスト（macOS非依存）。

grab / phash / mean_brightness / turn_page を monkeypatch し、
実機のキー送出・screencapture 無しでループ論理だけを検証する。
"""

from __future__ import annotations

from pathlib import Path

import imagehash
import pytest

from kindle2pdf import capture, imaging
from kindle2pdf.config import CaptureConfig, Config, PreprocessConfig
from kindle2pdf.state import State

# 距離が互いに2を大きく超える別ページ用ハッシュ（16進16桁 = 64bit）。
HASH_A = "0000000000000000"
HASH_B = "ffffffffffffffff"
HASH_C = "00000000ffffffff"
HASH_D = "ff00ff00ff00ff00"


def _make_cfg(**cap_over) -> Config:
    """テスト用 Config（待機0秒・安定確認1回）。"""
    defaults = dict(
        region=[0, 0, 10, 10],
        stable_required=1,
        end_detect_repeats=3,
        same_threshold=2,
        max_pages=100,
        page_turn_wait=0,
        stable_wait=0,
    )
    defaults.update(cap_over)
    return Config(
        capture=CaptureConfig(**defaults),
        preprocess=PreprocessConfig(min_brightness=20),
    )


class FakeScreen:
    """撮影フレーム列を再生する疑似スクリーン。

    frames: [(hash16進, 明度), ...]。grab のたびに次のフレームへ進む。
    grab は tmp ファイルを実際に作る（後段の pending.replace(dest) のため）。
    """

    def __init__(self, frames):
        self.frames = list(frames)
        self.gi = 0
        self.current = None
        self.turns = 0

    def grab(self, region, out_path):
        idx = min(self.gi, len(self.frames) - 1)
        self.current = self.frames[idx]
        self.gi += 1
        Path(out_path).write_bytes(b"fake-png")
        return str(out_path)

    def mean_brightness(self, path):
        return self.current[1]

    def phash(self, path):
        return imagehash.hex_to_hash(self.current[0])

    def turn_page(self, cfg):
        self.turns += 1


def _install(monkeypatch, fake: FakeScreen):
    monkeypatch.setattr(capture, "grab", fake.grab)
    monkeypatch.setattr(capture, "turn_page", fake.turn_page)
    monkeypatch.setattr(imaging, "mean_brightness", fake.mean_brightness)
    monkeypatch.setattr(imaging, "phash", fake.phash)


# --- turn_page ---


def test_turn_page_osascript_right(monkeypatch):
    called = {}

    def fake_run(cmd, check):
        called["cmd"] = cmd
        called["check"] = check

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    capture.turn_page(_make_cfg(page_turn_method="osascript", page_turn_key="right"))
    assert called["check"] is True
    assert "key code 124" in " ".join(called["cmd"])


def test_turn_page_osascript_left(monkeypatch):
    called = {}
    monkeypatch.setattr(
        capture.subprocess, "run", lambda cmd, check: called.setdefault("cmd", cmd)
    )
    capture.turn_page(_make_cfg(page_turn_method="osascript", page_turn_key="left"))
    assert "key code 123" in " ".join(called["cmd"])


def test_turn_page_cliclick(monkeypatch):
    called = {}
    monkeypatch.setattr(
        capture.subprocess, "run", lambda cmd, check: called.setdefault("cmd", cmd)
    )
    capture.turn_page(_make_cfg(page_turn_method="cliclick", page_turn_key="right"))
    assert called["cmd"] == ["cliclick", "kp:arrow-right"]


def test_turn_page_invalid_method(monkeypatch):
    monkeypatch.setattr(capture.subprocess, "run", lambda cmd, check: None)
    with pytest.raises(ValueError):
        capture.turn_page(_make_cfg(page_turn_method="xdotool"))


def test_turn_page_invalid_key(monkeypatch):
    monkeypatch.setattr(capture.subprocess, "run", lambda cmd, check: None)
    with pytest.raises(ValueError):
        capture.turn_page(_make_cfg(page_turn_key="up"))


# --- run_capture ---


def test_sequential_save_no_gaps(monkeypatch, tmp_path):
    # A,B,C の3ページ → 末尾で C が3回連続 → 停止。
    fake = FakeScreen(
        [(HASH_A, 255), (HASH_B, 255), (HASH_C, 255), (HASH_C, 255), (HASH_C, 255), (HASH_C, 255)]
    )
    _install(monkeypatch, fake)
    state = State()
    state_path = tmp_path / "state.json"
    capture.run_capture(_make_cfg(), state, tmp_path, state_path)

    raw = tmp_path / "raw"
    pngs = sorted(p.name for p in raw.glob("page_*.png"))
    assert pngs == ["page_0000.png", "page_0001.png", "page_0002.png"]
    assert state.captured == 3
    assert len(state.hash_history) == 3
    assert not (raw / ".pending.png").exists()


def test_end_detection_does_not_save_duplicates(monkeypatch, tmp_path):
    fake = FakeScreen([(HASH_A, 255), (HASH_A, 255), (HASH_A, 255), (HASH_A, 255)])
    _install(monkeypatch, fake)
    state = State()
    capture.run_capture(_make_cfg(), state, tmp_path, tmp_path / "state.json")

    # 1ページのみ保存され、以降の同一フレームは保存されない。
    assert state.captured == 1
    assert state.repeat_count == 3
    # end_detect_repeats=3 回目の duplicate で break → その回は turn_page しない。
    assert fake.turns == 3


def test_black_frame_is_retried(monkeypatch, tmp_path):
    # 最初のフレームが黒画面 → 撮り直して A を確定。以降 duplicate で停止。
    fake = FakeScreen(
        [(HASH_A, 5), (HASH_A, 255), (HASH_A, 255), (HASH_A, 255), (HASH_A, 255)]
    )
    _install(monkeypatch, fake)
    state = State()
    capture.run_capture(_make_cfg(), state, tmp_path, tmp_path / "state.json")

    assert state.captured == 1
    # 黒画面ぶんを含めて 5 回撮影している（リトライで撮り直した証跡）。
    assert fake.gi == 5


def test_state_committed_per_page(monkeypatch, tmp_path):
    fake = FakeScreen(
        [(HASH_A, 255), (HASH_B, 255), (HASH_B, 255), (HASH_B, 255), (HASH_B, 255)]
    )
    _install(monkeypatch, fake)
    state = State()
    state_path = tmp_path / "state.json"
    capture.run_capture(_make_cfg(), state, tmp_path, state_path)

    # state.json がディスクに書かれ、確定枚数が永続化されている。
    reloaded = State.load(state_path)
    assert reloaded.captured == 2
    assert reloaded.last_hash == HASH_B


def test_resume_continues_numbering(monkeypatch, tmp_path):
    # 既に2枚確定済み・直前ハッシュ B の状態から再開する。
    (tmp_path / "raw").mkdir()
    state = State(captured=2, last_hash=HASH_B, hash_history=[HASH_A, HASH_B])
    # 新規ページ D → 以降 D の duplicate で停止。
    fake = FakeScreen([(HASH_D, 255), (HASH_D, 255), (HASH_D, 255), (HASH_D, 255)])
    _install(monkeypatch, fake)
    capture.run_capture(_make_cfg(), state, tmp_path, tmp_path / "state.json")

    raw = tmp_path / "raw"
    # 連番は 0002 から続く（0000/0001 は再撮影しない）。
    assert (raw / "page_0002.png").exists()
    assert not (raw / "page_0000.png").exists()
    assert state.captured == 3


def test_stable_required_needs_consecutive_match(monkeypatch, tmp_path):
    # stable_required=2: ローディング中フレーム(B)→安定(A,A) で A を確定する。
    fake = FakeScreen(
        [
            (HASH_B, 255),  # 1枚目（不安定）
            (HASH_A, 255),  # 2枚目（A 開始 count=1）
            (HASH_A, 255),  # 3枚目（A 連続 count=2 → 確定）
            (HASH_A, 255),  # 以降 duplicate
            (HASH_A, 255),
            (HASH_A, 255),
            (HASH_A, 255),
        ]
    )
    _install(monkeypatch, fake)
    state = State()
    capture.run_capture(
        _make_cfg(stable_required=2), state, tmp_path, tmp_path / "state.json"
    )

    assert state.captured == 1
    assert state.last_hash == HASH_A
