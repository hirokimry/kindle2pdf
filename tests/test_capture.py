"""capture の撮影ループ・最終ページ検出の単体テスト（macOS非依存）。

grab / phash / mean_brightness / turn_page を monkeypatch し、
実機のキー送出・screencapture 無しでループ論理だけを検証する。
"""

from __future__ import annotations

from pathlib import Path

import imagehash
import pytest

from kindle2pdf import capture, imaging, naming
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
        app_name="Kindle",  # 明示指定で resolve_app_name を短絡（実 osascript を呼ばない・CI 安全）
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

    def __init__(self, frames, cycle=False):
        self.frames = list(frames)
        self.cycle = cycle
        self.gi = 0
        self.current = None
        self.turns = 0

    def grab(self, out_path, window_id):
        if self.cycle:
            idx = self.gi % len(self.frames)
        else:
            idx = min(self.gi, len(self.frames) - 1)
        self.current = self.frames[idx]
        self.gi += 1
        Path(out_path).write_bytes(b"fake-png")
        return str(out_path)

    def mean_brightness(self, path):
        return self.current[1]

    def phash(self, path):
        return imagehash.hex_to_hash(self.current[0])

    def turn_page(self, cfg, app_name=None):
        self.turns += 1


def _install(monkeypatch, fake: FakeScreen):
    # auto_region の実測（Quartz/AX）を避け、window_id とクロップ比率を固定注入する。
    # crop_fraction=0.0 なら crop_top_fraction を呼ばず、FakeScreen の擬似 png を PIL で開かない。
    monkeypatch.setattr(
        capture, "_auto_region_params", lambda app_name: (1, (0, 0, 100, 100), 0.0)
    )
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
    assert pngs == [naming.page_filename(i) for i in range(3)]
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
    # 連番は 2 番から続く（0/1 は再撮影しない）。
    assert (raw / naming.page_filename(2)).exists()
    assert not (raw / naming.page_filename(0)).exists()
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


def test_black_screen_persisting_raises(monkeypatch, tmp_path):
    # 黒画面が上限を超えて続く撮影領域ズレ・Kindle応答なし相当の異常系。
    monkeypatch.setattr(capture, "_MAX_BLACK_RETRIES", 3)
    fake = FakeScreen([(HASH_A, 5)])  # 常に黒画面
    _install(monkeypatch, fake)
    state = State()
    with pytest.raises(RuntimeError, match="黒画面"):
        capture.run_capture(_make_cfg(), state, tmp_path, tmp_path / "state.json")


def test_never_stable_frame_raises(monkeypatch, tmp_path):
    # フレームが安定しない（毎回別ハッシュ）異常系。
    monkeypatch.setattr(capture, "_MAX_STABLE_ATTEMPTS", 5)
    fake = FakeScreen([(HASH_A, 255), (HASH_B, 255)], cycle=True)  # A,B 交互で安定しない
    _install(monkeypatch, fake)
    state = State()
    with pytest.raises(RuntimeError, match="安定"):
        capture.run_capture(
            _make_cfg(stable_required=2), state, tmp_path, tmp_path / "state.json"
        )


# --- macOS 実機バグ修正（アプリ名 / pending ドットファイル / calibrate フォーカス） ---


def test_turn_page_uses_configured_app_name(monkeypatch):
    """page_turn の activate は config の app_name を使う（"Amazon Kindle" 等）。"""
    called = {}
    monkeypatch.setattr(
        capture.subprocess, "run", lambda cmd, check: called.setdefault("cmd", cmd)
    )
    capture.turn_page(_make_cfg(page_turn_method="osascript", app_name="Amazon Kindle"))
    assert 'tell application "Amazon Kindle" to activate' in " ".join(called["cmd"])


def test_pending_temp_is_not_dotfile_and_outside_raw(monkeypatch, tmp_path):
    """pending は非ドットファイル（screencapture が書ける）かつ raw/ の外に置く。"""
    seen = []
    fake = FakeScreen([(HASH_A, 200), (HASH_A, 200), (HASH_A, 200)])

    def rec_grab(out_path, window_id):
        seen.append(Path(out_path))
        return fake.grab(out_path, window_id)

    monkeypatch.setattr(
        capture, "_auto_region_params", lambda app_name: (1, (0, 0, 100, 100), 0.0)
    )
    monkeypatch.setattr(capture, "grab", rec_grab)
    monkeypatch.setattr(capture, "turn_page", fake.turn_page)
    monkeypatch.setattr(imaging, "mean_brightness", fake.mean_brightness)
    monkeypatch.setattr(imaging, "phash", fake.phash)
    capture.run_capture(_make_cfg(), State(), tmp_path, tmp_path / "state.json")
    assert seen, "grab が呼ばれていない"
    raw_dir = tmp_path / "raw"
    for p in seen:
        assert not p.name.startswith("."), f"pending がドットファイル: {p.name}"
        assert p.parent != raw_dir, f"pending が raw/ 内にある: {p}"


# --- ウィンドウ自動検出（-l ウィンドウID撮影）---


class _FakeQuartz:
    """CGWindowListCopyWindowInfo を模した最小スタブ。"""

    kCGWindowListOptionAll = 0
    kCGNullWindowID = 0

    def __init__(self, windows):
        self._windows = windows

    def CGWindowListCopyWindowInfo(self, opt, wid):  # noqa: N802
        return self._windows


def test_detect_window_id_picks_largest_layer0_kindle(monkeypatch):
    """app 名一致・レイヤ0・最大面積のウィンドウを本体 ID として選ぶ。"""
    windows = [
        {"kCGWindowOwnerName": "Kindle", "kCGWindowLayer": 0, "kCGWindowNumber": 11,
         "kCGWindowOwnerPID": 555,
         "kCGWindowBounds": {"X": 0, "Y": 36, "Width": 1470, "Height": 920}},
        {"kCGWindowOwnerName": "Kindle", "kCGWindowLayer": 0, "kCGWindowNumber": 22,
         "kCGWindowOwnerPID": 555,
         "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1470, "Height": 36}},   # 小さい補助窓
        {"kCGWindowOwnerName": "Kindle", "kCGWindowLayer": 26, "kCGWindowNumber": 33,
         "kCGWindowOwnerPID": 555,
         "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1470, "Height": 36}},   # レイヤ0でない
        {"kCGWindowOwnerName": "Finder", "kCGWindowLayer": 0, "kCGWindowNumber": 44,
         "kCGWindowOwnerPID": 999,
         "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 9999, "Height": 9999}},  # 別アプリ
    ]
    monkeypatch.setattr(capture, "Quartz", _FakeQuartz(windows))
    wid, rect, pid = capture.detect_window_id("Amazon Kindle")
    assert wid == 11
    assert rect == (0, 36, 1470, 920)
    assert pid == 555  # AX でタイトルバーを実測するため本体ウィンドウの PID を返す


def test_detect_window_id_warns_on_multiple_candidates(monkeypatch, caplog):
    """Kindle ウィンドウが複数見つかったら警告を出す（誤ウィンドウのサイレント撮影を防ぐ）。"""
    windows = [
        {"kCGWindowOwnerName": "Kindle", "kCGWindowLayer": 0, "kCGWindowNumber": 11,
         "kCGWindowOwnerPID": 555,
         "kCGWindowBounds": {"X": 0, "Y": 36, "Width": 1470, "Height": 920}},
        {"kCGWindowOwnerName": "Kindle", "kCGWindowLayer": 0, "kCGWindowNumber": 22,
         "kCGWindowOwnerPID": 555,
         "kCGWindowBounds": {"X": 0, "Y": 36, "Width": 800, "Height": 600}},  # 2冊目/パネル
    ]
    monkeypatch.setattr(capture, "Quartz", _FakeQuartz(windows))
    import logging
    with caplog.at_level(logging.WARNING):
        wid, rect, pid = capture.detect_window_id("Amazon Kindle")
    assert wid == 11  # 面積最大を本体に選ぶ
    assert any("面積最大" in r.getMessage() for r in caplog.records)


def test_detect_window_id_raises_when_no_kindle(monkeypatch):
    """Kindle ウィンドウが無ければ明確なエラーを出す。"""
    monkeypatch.setattr(capture, "Quartz", _FakeQuartz([]))
    with pytest.raises(RuntimeError, match="見つかりません"):
        capture.detect_window_id("Amazon Kindle")


def _one_kindle_window(number=900, pid=42, bounds=(0, 37, 1470, 919)):
    x, y, w, h = bounds
    return [{
        "kCGWindowOwnerName": "Kindle", "kCGWindowLayer": 0, "kCGWindowNumber": number,
        "kCGWindowOwnerPID": pid,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h},
    }]


def test_run_capture_auto_region_wires_window_id_and_crop(monkeypatch, tmp_path):
    """window_id と crop 比率が grab / crop まで配線される。"""
    grabbed, cropped = [], []

    def rec_grab(out_path, window_id):
        grabbed.append(window_id)
        Path(out_path).write_bytes(b"x")
        return str(out_path)

    monkeypatch.setattr(capture, "Quartz", _FakeQuartz(_one_kindle_window()))
    monkeypatch.setattr(capture, "detect_titlebar_pt", lambda pid, bounds: 28.0)
    monkeypatch.setattr(capture, "grab", rec_grab)
    monkeypatch.setattr(imaging, "crop_top_fraction", lambda p, f: cropped.append(f))
    monkeypatch.setattr(imaging, "mean_brightness", lambda p: 200)
    monkeypatch.setattr(imaging, "phash", lambda p: imagehash.hex_to_hash(HASH_A))
    monkeypatch.setattr(capture, "turn_page", lambda cfg, app_name=None: None)

    capture.run_capture(
        _make_cfg(app_name="Amazon Kindle"),
        State(), tmp_path, tmp_path / "state.json",
    )
    # 検出した window_id で全フレーム撮影し、毎フレーム 28/919 の比率でクロップした。
    assert grabbed and all(wid == 900 for wid in grabbed)
    assert cropped and all(f == pytest.approx(28 / 919) for f in cropped)


def test_run_capture_auto_region_refollows_resize(monkeypatch, tmp_path):
    """ページごとにウィンドウを再検出し、途中リサイズ後のクロップ比率に追従する。"""
    # _auto_region_params が呼ばれるたびに比率が変わる（3回目以降で縮小相当0.05に）。
    fractions = iter([0.03, 0.03, 0.05, 0.05, 0.05, 0.05])
    monkeypatch.setattr(
        capture, "_auto_region_params",
        lambda app_name: (900, (0, 37, 1470, 919), next(fractions, 0.05)),
    )
    cropped = []
    fake = FakeScreen([(HASH_A, 255), (HASH_B, 255), (HASH_B, 255), (HASH_B, 255), (HASH_B, 255)])
    monkeypatch.setattr(capture, "grab", fake.grab)
    monkeypatch.setattr(capture, "turn_page", fake.turn_page)
    monkeypatch.setattr(imaging, "mean_brightness", fake.mean_brightness)
    monkeypatch.setattr(imaging, "phash", fake.phash)
    monkeypatch.setattr(imaging, "crop_top_fraction", lambda p, f: cropped.append(f))

    capture.run_capture(_make_cfg(), State(), tmp_path, tmp_path / "s.json")

    # リサイズ後の比率0.05が実際にクロップに使われている（古い0.03に固定されていない）。
    assert 0.05 in cropped


def test_run_calibrate_auto_returns_crop_adjusted_region(monkeypatch, tmp_path):
    """calibrate は window_id で撮り、返す region をクロップ後に補正する。"""
    calls = []

    def rec_grab(out_path, window_id):
        calls.append(window_id)
        Path(out_path).write_bytes(b"x")
        return str(out_path)

    monkeypatch.setattr(capture, "Quartz", _FakeQuartz(_one_kindle_window()))
    monkeypatch.setattr(capture, "detect_titlebar_pt", lambda pid, bounds: 28.0)
    monkeypatch.setattr(capture, "grab", rec_grab)
    monkeypatch.setattr(imaging, "crop_top_fraction", lambda p, f: None)

    out_path, region = capture.run_calibrate(
        _make_cfg(app_name="Amazon Kindle"), tmp_path
    )
    assert calls == [900]                          # window_id で直接撮った
    assert region == (0, 37 + 28, 1470, 919 - 28)  # 上端クロップぶん y を下げ h を縮めた


def test_grab_uses_window_id_flag(monkeypatch):
    """window_id 指定時は screencapture -l <id> でウィンドウを直接撮る（-R を使わない）。"""
    calls = {}
    import subprocess as sp

    def fake_run(cmd, check):
        calls["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x")

    monkeypatch.setattr(sp, "run", fake_run)
    capture.grab("/tmp/x.png", 79362)
    assert "-l" in calls["cmd"] and "79362" in calls["cmd"]
    assert not any(str(c).startswith("-R") for c in calls["cmd"])


# --- resolve_app_name（Kindleアプリ名の自動検出＋キャッシュ・#33）-----------------
# verifier は「その名前を AppleScript が受け付けるか（id of application が通るか）」を返す
# 述語。detect_window_id の所有者名部分一致では "Amazon Kindle" と "Kindle" を区別できない
# ため、実際に AppleScript で使える名前かを検証する実体に合わせて bool を返すfakeで検証する。


def test_resolve_app_name_prefers_explicit(tmp_path):
    """明示指定があれば検証せずそのまま採用する（最優先）。"""
    calls = []

    def verifier(name):
        calls.append(name)
        return True

    resolved = capture.resolve_app_name(
        "My Kindle", verifier=verifier, cache_path=tmp_path / "app_name"
    )
    assert resolved == "My Kindle"
    assert calls == []  # 明示指定時は検証を呼ばない


def test_resolve_app_name_tries_candidates_in_order(tmp_path):
    """未指定なら候補を順に検証し、最初に通った名前を採用してキャッシュする。"""
    def verifier(name):
        return name == "Kindle"  # "Amazon Kindle" は通らず "Kindle" だけ通る

    cache = tmp_path / "app_name"
    resolved = capture.resolve_app_name("", verifier=verifier, cache_path=cache)
    assert resolved == "Kindle"
    assert cache.read_text(encoding="utf-8") == "Kindle"


def test_resolve_app_name_uses_cache_first(tmp_path):
    """キャッシュ済みの名前を最初に検証する（再探索を省く）。"""
    cache = tmp_path / "app_name"
    cache.write_text("Amazon Kindle", encoding="utf-8")
    calls = []

    def verifier(name):
        calls.append(name)
        return True

    resolved = capture.resolve_app_name("", verifier=verifier, cache_path=cache)
    assert resolved == "Amazon Kindle"
    assert calls[0] == "Amazon Kindle"  # キャッシュを最初に試す


def test_resolve_app_name_raises_when_none_found(tmp_path):
    """どの候補も通らなければ試した名前を含む明確なエラーで止まる。"""
    def verifier(name):
        return False

    with pytest.raises(RuntimeError) as excinfo:
        capture.resolve_app_name("", verifier=verifier, cache_path=tmp_path / "app_name")
    msg = str(excinfo.value)
    assert "Amazon Kindle" in msg
    assert "Kindle" in msg
