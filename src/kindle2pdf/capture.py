"""capture 段 — Kindle制御・撮影・最終ページ検出。

責務分離の要: システム内で **Kindleに触れるのはこのモジュールのみ**。
preprocess/ocr/build は撮影済み画像だけを入力とするバッチで、
Kindle操作なしに何度でも再実行できる。

実装チケット: P1(region実測) / P2(撮影ループ) / P3(最終ページ検出)
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import imaging
from .config import Config
from .state import State

# 矢印キーの macOS key code（osascript / System Events）。
_KEY_CODE = {"right": 124, "left": 123}
# cliclick のキー名。
_CLICLICK_KEY = {"right": "arrow-right", "left": "arrow-left"}

# 黒画面（明度異常）が続いた場合に諦める上限リトライ回数。
_MAX_BLACK_RETRIES = 10
# 1フレーム確定までに許す総撮影回数（描画が安定しない場合の暴走防止）。
_MAX_STABLE_ATTEMPTS = 30


def activate_kindle() -> None:
    """Kindleを前面化する。"""
    subprocess.run(
        ["osascript", "-e", 'tell application "Kindle" to activate'], check=False
    )


def turn_page(cfg: Config) -> None:
    """右/左矢印キーを送出してページ送りする（osascript / cliclick）。

    osascript: key code 124=右, 123=左。cliclick: kp:arrow-right/left。
    キー送出には実行元Terminalに「アクセシビリティ」権限が必要。
    """
    key = cfg.capture.page_turn_key
    method = cfg.capture.page_turn_method
    if key not in _KEY_CODE:
        raise ValueError(f"page_turn_key は right / left のいずれか（受領値: {key}）。")

    if method == "osascript":
        code = _KEY_CODE[key]
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Kindle" to activate',
                "-e",
                "delay 0.15",
                "-e",
                f'tell application "System Events" to key code {code}',
            ],
            check=True,
        )
    elif method == "cliclick":
        subprocess.run(["cliclick", f"kp:{_CLICLICK_KEY[key]}"], check=True)
    else:
        raise ValueError(
            f"page_turn_method は osascript / cliclick のいずれか（受領値: {method}）。"
        )


def grab(region: list[int], out_path: str | Path) -> str:
    """指定領域をサムネイルを出さずに撮影する。

    実コマンド: screencapture -x -R"{x},{y},{w},{h}" out_path
    -x でシャッター音・フローティングサムネイルを抑止（PoC 7.2 の必須条件）。
    """
    x, y, w, h = region
    subprocess.run(
        ["screencapture", "-x", f"-R{x},{y},{w},{h}", str(out_path)], check=True
    )
    return str(out_path)


def _grab_confirmed(cfg: Config, tmp_path: Path) -> imaging.imagehash.ImageHash:
    """安定した1フレームを撮り、その pHash を返す。

    - 黒画面（明度 < min_brightness）はリトライで撮り直す。
    - `stable_required` 回連続で同一ハッシュになったフレームを確定する
      （ページ送り直後のローディング中フレームの誤確定を防ぐ）。
    """
    cap = cfg.capture
    min_brightness = cfg.preprocess.min_brightness
    stable_required = max(1, cap.stable_required)

    stable_hash: imaging.imagehash.ImageHash | None = None
    stable_count = 0
    black_retries = 0  # これまでに撮り直した黒画面の回数
    attempts = 0       # これまでに撮影した総回数

    while True:
        # 総撮影回数が上限（_MAX_STABLE_ATTEMPTS）に達しても安定しなければ諦める。
        if attempts >= _MAX_STABLE_ATTEMPTS:
            raise RuntimeError(
                "撮影フレームが安定しませんでした（page_turn_wait / stable_wait を見直してください）。"
            )
        attempts += 1
        grab(cap.region, tmp_path)

        if imaging.mean_brightness(tmp_path) < min_brightness:
            # 黒画面のリトライ回数が上限（_MAX_BLACK_RETRIES）に達したら諦める。
            if black_retries >= _MAX_BLACK_RETRIES:
                raise RuntimeError(
                    "黒画面が継続しました（Kindle表示・撮影領域 region を確認してください）。"
                )
            black_retries += 1
            # 黒画面は安定判定をリセットして撮り直す。
            stable_hash = None
            stable_count = 0
            time.sleep(cap.stable_wait)
            continue

        h = imaging.phash(tmp_path)
        # 安定確認は専用の stable_threshold を使う（重複判定用 same_threshold とは別較正）。
        if stable_hash is not None and imaging.is_same(h, stable_hash, cap.stable_threshold):
            stable_count += 1
        else:
            stable_hash = h
            stable_count = 1

        if stable_count >= stable_required:
            return h
        time.sleep(cap.stable_wait)


def run_capture(
    cfg: Config, state: State, work_dir: Path, state_path: str | Path
) -> None:
    """送り→待機→撮影→安定確認→pHash→終了判定 のループ（仕様書 5.2）。

    - 新規ページのみ `raw/page_{n:04d}.png` に欠け・重複なく連番保存する。
    - 直前確定フレームと `same_threshold` 以内で一致したら duplicate とみなし、
      `end_detect_repeats` 連続で最終ページと判定して停止する（duplicate は保存しない）。
    - 1反復ごとに state を `state_path` へ逐次コミットする（レジューム対応）。
    """
    cap = cfg.capture
    raw_dir = Path(work_dir) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pending = raw_dir / ".pending.png"

    # レジューム: 既存 state から直前確定フレーム・連続一致数を引き継ぐ。
    prev_hash = imaging.hex_to_hash(state.last_hash) if state.last_hash else None
    repeat = state.repeat_count

    try:
        while state.captured < cap.max_pages:
            h = _grab_confirmed(cfg, pending)

            if prev_hash is not None and imaging.is_same(h, prev_hash, cap.same_threshold):
                # ページ送りが効いていない = 最終ページに到達している可能性。
                repeat += 1
                state.repeat_count = repeat
                if repeat >= cap.end_detect_repeats:
                    state.save(state_path)
                    break
            else:
                # 新規ページ: 連番で確定保存し、state を逐次コミットする。
                seq = state.captured
                dest = raw_dir / f"page_{seq:04d}.png"
                pending.replace(dest)
                state.captured = seq + 1
                state.last_hash = str(h)
                state.hash_history.append(str(h))
                repeat = 0
                state.repeat_count = 0

            state.save(state_path)
            prev_hash = h
            turn_page(cfg)
            time.sleep(cap.page_turn_wait)
    finally:
        if pending.exists():
            pending.unlink()
