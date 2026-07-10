"""capture 段 — Kindle制御・撮影・最終ページ検出。

責務分離の要: システム内で **Kindleに触れるのはこのモジュールのみ**。
preprocess/ocr/build は撮影済み画像だけを入力とするバッチで、
Kindle操作なしに何度でも再実行できる。

実装チケット: P1(region実測) / P2(撮影ループ) / P3(最終ページ検出)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config, validate_region
from .state import State


def run_calibrate(cfg: Config, work_dir: Path) -> tuple[Path, tuple[int, int, int, int]]:
    """region を 1 枚だけ撮影し、(保存先パス, 正規化済み region) を返す。[P1]

    未設定・不正な region は validate_region が明確な ValueError で弾く。
    撮影後の画像を開けば UI・柱・余白が入らず本文だけが写るかを目視確認できる。
    正規化済み region を併せて返すことで、呼び出し側が「実際に撮影に使った値」を
    表示でき、config の生の値（float 等）との齟齬を防ぐ。
    """
    region = validate_region(cfg.capture.region)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / "calibrate.png"
    grab(list(region), out_path)
    return out_path, region


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
    raise NotImplementedError("P2: ページ送り（osascript / cliclick）を実装する")


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


def run_capture(cfg: Config, state: State, work_dir: Path) -> None:
    """送り→待機→撮影→安定確認→pHash→終了判定 のループ。

    擬似コード（仕様書 5.2）:
        prev = None; repeat = 0
        for n in range(max_pages):
            p = grab(...)
            if mean_brightness(p) < min_brightness: retry; continue
            h = phash(p)
            if prev and is_same(h, prev, same_threshold):
                repeat += 1
                if repeat >= end_detect_repeats: break   # 最終ページ
            else:
                repeat = 0; save(p); state.commit(n, h)
            prev = h
            turn_page(cfg); sleep(page_turn_wait)
    """
    raise NotImplementedError("P2/P3: 撮影ループと最終ページ検出を実装する")
