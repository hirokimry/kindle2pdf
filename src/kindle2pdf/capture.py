"""capture 段 — Kindle制御・撮影・最終ページ検出。

責務分離の要: システム内で **Kindleに触れるのはこのモジュールのみ**。
preprocess/ocr/build は撮影済み画像だけを入力とするバッチで、
Kindle操作なしに何度でも再実行できる。

実装チケット: P1(region実測) / P2(撮影ループ) / P3(最終ページ検出)
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from . import imaging, naming
from .config import Config, validate_region
from .state import State

try:  # macOS のみ（pyobjc-framework-Quartz）。非 macOS では auto_region 不可。
    import Quartz
except ImportError:  # pragma: no cover - 非 macOS 環境
    Quartz = None

try:  # macOS のみ（pyobjc-framework-ApplicationServices）。AX でタイトルバー高さを実測する。
    import ApplicationServices as _AX
except ImportError:  # pragma: no cover - 非 macOS 環境
    _AX = None

# 信号機ボタン（閉じる/最小化/全画面）の AX subrole。タイトルバー内に上下中央で並ぶ。
_TRAFFIC_LIGHT_SUBROLES = ("AXCloseButton", "AXMinimizeButton", "AXFullScreenButton")

logger = logging.getLogger(__name__)

# 矢印キーの macOS key code（osascript / System Events）。
_KEY_CODE = {"right": 124, "left": 123}
# cliclick のキー名。
_CLICLICK_KEY = {"right": "arrow-right", "left": "arrow-left"}

# 黒画面（明度異常）が続いた場合に諦める上限リトライ回数。
_MAX_BLACK_RETRIES = 10
# 1フレーム確定までに許す総撮影回数（描画が安定しない場合の暴走防止）。
_MAX_STABLE_ATTEMPTS = 30


def run_calibrate(cfg: Config, work_dir: Path) -> tuple[Path, tuple[int, int, int, int]]:
    """region を 1 枚だけ撮影し、(保存先パス, 正規化済み region) を返す。[P1]

    未設定・不正な region は validate_region が明確な ValueError で弾く。
    撮影後の画像を開けば UI・柱・余白が入らず本文だけが写るかを目視確認できる。
    正規化済み region を併せて返すことで、呼び出し側が「実際に撮影に使った値」を
    表示でき、config の生の値（float 等）との齟齬を防ぐ。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / "calibrate.png"
    if cfg.capture.auto_region:
        # ウィンドウ ID を検出し `-l` で直接撮る（前面化不要・別 Space 可）。撮影像に含まれる
        # タイトルバー帯だけを AX 実測の高さで上端クロップし、本文余白は残す。
        window_id, region, pid = detect_window_id(cfg.capture.app_name)
        grab(None, out_path, window_id=window_id)
        titlebar_pt = detect_titlebar_pt(pid, region)
        win_h = region[3]
        if win_h:
            imaging.crop_top_fraction(out_path, titlebar_pt / win_h)
            # 返す region は保存画像と一致させる（上端クロップぶん y を下げ h を縮める）。
            # calibrate は「実際に撮影に使った領域」を数値表示するため、クロップ前の
            # ウィンドウ矩形をそのまま返すと表示高さが実画像より大きくなり誤解を招く。
            x, y, w, h = region
            cut = round(titlebar_pt)
            region = (x, y + cut, w, h - cut)
    else:
        # 静的 region 運用: Kindle を前面化してから領域を撮る（未実測は ValueError で弾く）。
        activate_kindle(cfg.capture.app_name)
        time.sleep(cfg.capture.page_turn_wait)
        region = validate_region(cfg.capture.region)
        grab(list(region), out_path)
    return out_path, region


def activate_kindle(app_name: str = "Kindle") -> None:
    """Kindleを前面化する。app_name は環境により "Amazon Kindle" 等。"""
    subprocess.run(
        ["osascript", "-e", f'tell application "{app_name}" to activate'], check=False
    )


def detect_window_id(
    app_name: str = "Kindle",
) -> tuple[int, tuple[int, int, int, int], int]:
    """Kindle 本体ウィンドウの (CGWindowID, 矩形(x,y,w,h)[pt], PID) を返す。

    `screencapture -l <id>` でウィンドウを直接撮るための ID。この方式なら **前面化不要・
    別 Space でも撮れ・ウィンドウ中身をそのまま撮る**（余白を絶対に削らない）。ただし撮影像
    には macOS のタイトルバー帯が含まれるため、PID から AX でその帯の高さを実測し
    （`detect_titlebar_pt`）、上端だけを動的にクロップして落とす。本の白余白・ヘッダー/
    フッターはウィンドウの中身なので忠実に残す。

    全ウィンドウから app 名一致・レイヤ0・最大面積のものを本体とみなし、小さな補助
    ウィンドウを除外する。System Events のプロセス名は app 名と異なることがあるため
    app_name の末尾語で部分一致させる。PID は AX でタイトルバーを実測するために返す。
    """
    if Quartz is None:
        raise RuntimeError(
            "Quartz が利用できません（pip install pyobjc-framework-Quartz が必要）"
        )
    keyword = app_name.split()[-1] if app_name.split() else app_name
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
    )
    # (面積, id, 矩形, pid)
    best: tuple[int, int, tuple[int, int, int, int], int] | None = None
    for w in wins:
        owner = w.get("kCGWindowOwnerName", "") or ""
        if keyword not in owner or w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w.get("kCGWindowBounds", {})
        rect = (
            int(b.get("X", 0)),
            int(b.get("Y", 0)),
            int(b.get("Width", 0)),
            int(b.get("Height", 0)),
        )
        area = rect[2] * rect[3]
        if best is None or area > best[0]:
            best = (area, int(w.get("kCGWindowNumber")), rect, int(w.get("kCGWindowOwnerPID", 0)))
    if best is None:
        raise RuntimeError(f"Kindle ウィンドウが見つかりません（app_name={app_name!r}）")
    return best[1], best[2], best[3]


def _ax_attr(el, name):
    """AX 要素の属性を取得する（失敗時 None）。"""
    err, val = _AX.AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _ax_frame(el) -> tuple[float, float, float, float] | None:
    """AX 要素の (x, y, w, h)[pt] を返す（取得不可なら None）。"""
    pos = _ax_attr(el, "AXPosition")
    size = _ax_attr(el, "AXSize")
    if pos is None or size is None:
        return None
    ok_p, p = _AX.AXValueGetValue(pos, _AX.kAXValueCGPointType, None)
    ok_s, s = _AX.AXValueGetValue(size, _AX.kAXValueCGSizeType, None)
    if not (ok_p and ok_s):
        return None
    return (p.x, p.y, s.width, s.height)


def _ax_find_traffic_lights(win) -> list[tuple[float, float, float, float]]:
    """ウィンドウ配下の信号機ボタンの矩形一覧を返す（浅い探索）。

    ボタンはウィンドウ直下に並ぶが、subrole が入れ子になる環境もあるため深さ2まで見る。
    """
    found: list[tuple[float, float, float, float]] = []

    def walk(el, depth):
        if depth > 2:
            return
        for c in _ax_attr(el, "AXChildren") or []:
            if _ax_attr(c, "AXSubrole") in _TRAFFIC_LIGHT_SUBROLES:
                fr = _ax_frame(c)
                if fr is not None:
                    found.append(fr)
            walk(c, depth + 1)

    walk(win, 0)
    return found


def detect_titlebar_pt(pid: int, window_bounds: tuple[int, int, int, int]) -> float:
    """AX でウィンドウの macOS タイトルバー帯の高さ[pt]を **動的に** 実測する。

    Why: `screencapture -l` の撮影像に写り込むタイトルバー帯だけを落としたい。帯の高さは
    固定 px で決め打ちすると OS バージョン・retina 倍率で狂い、本文余白を削る事故に直結する
    （CEO 絶対制約: 余白を削らない）。そこで信号機ボタンがタイトルバー内で **上下中央** に
    並ぶ macOS の規約を使い、`帯高 = 2 ×(ボタン中心y − ウィンドウ上端y)` として実測する。
    ページ内容には一切依存しないため表紙ページでも壊れない。

    AX 不可・権限不足・ボタン不検出時は、誤クロップより明確なエラーで停止する
    （アクセシビリティ権限の付与を促す）。
    """
    if _AX is None:
        raise RuntimeError(
            "ApplicationServices が利用できません"
            "（pip install pyobjc-framework-ApplicationServices が必要）"
        )
    app = _AX.AXUIElementCreateApplication(pid)
    wins = _ax_attr(app, "AXWindows")
    if not wins:
        raise RuntimeError(
            "AX でウィンドウを取得できませんでした。実行元 Terminal に "
            "「アクセシビリティ」権限を付与してください（システム設定 > "
            "プライバシーとセキュリティ > アクセシビリティ）。"
        )
    wx, wy, ww, wh = window_bounds
    # Quartz 矩形に最も近い AX ウィンドウを選ぶ（複数ウィンドウ時の取り違え防止）。
    def dist(win) -> float:
        fr = _ax_frame(win)
        if fr is None:
            return float("inf")
        return abs(fr[0] - wx) + abs(fr[1] - wy) + abs(fr[2] - ww) + abs(fr[3] - wh)

    win = min(wins, key=dist)
    lights = _ax_find_traffic_lights(win)
    if not lights:
        raise RuntimeError(
            "AX で信号機ボタンを検出できませんでした（タイトルバー高さを実測できません）。"
            "Kindle が通常ウィンドウ表示か、Terminal のアクセシビリティ権限を確認してください。"
        )
    # ボタンは上下中央に並ぶ。最上段（min top）の中心 y から帯高を導く。
    top_light = min(lights, key=lambda fr: fr[1])
    center_y = top_light[1] + top_light[3] / 2
    titlebar_pt = 2 * (center_y - wy)
    # 実測値は「正でウィンドウ高より小さい」範囲に必ず収まる（帯はウィンドウの一部）。
    # ここを外れる値は AX の異常応答であり、そのまま使うと crop 比率>=1 で帯を消せず
    # フレーム入り画像を黙って量産する / calibrate の region 高さが負になる。CEO 制約
    # 「枠は論外」に従い、黙って劣化させず明確なエラーで止める。
    if not 0 < titlebar_pt < wh:
        raise RuntimeError(
            f"タイトルバー高さの実測値が異常です（{titlebar_pt:.1f}pt / ウィンドウ高 {wh}pt）。"
            "Kindle が通常ウィンドウ表示か、Terminal のアクセシビリティ権限を確認してください。"
        )
    return titlebar_pt


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
                f'tell application "{cfg.capture.app_name}" to activate',
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


def grab(
    region: list[int] | None, out_path: str | Path, window_id: int | None = None
) -> str:
    """ウィンドウ(window_id) or 領域(region)をサムネイルを出さずに撮影する。

    - window_id 指定時: `screencapture -x -o -l <id>`。ウィンドウを直接撮る（前面化不要・
      別 Space 可・タイトルバー無し・中身をそのまま=余白を削らない）。auto_region の既定。
    - region 指定時: `screencapture -x -R{x},{y},{w},{h}`（静的 region 運用）。
    -x でシャッター音・フローティングサムネイルを抑止（PoC 7.2 の必須条件）、-o で影を除く。
    """
    if window_id is not None:
        cmd = ["screencapture", "-x", "-o", "-l", str(window_id), str(out_path)]
    else:
        x, y, w, h = region
        cmd = ["screencapture", "-x", f"-R{x},{y},{w},{h}", str(out_path)]
    subprocess.run(cmd, check=True)
    return str(out_path)


def _grab_confirmed(
    cfg: Config,
    tmp_path: Path,
    window_id: int | None = None,
    crop_fraction: float = 0.0,
) -> imaging.imagehash.ImageHash:
    """安定した1フレームを撮り、その pHash を返す。

    - 黒画面（明度 < min_brightness）はリトライで撮り直す。
    - `stable_required` 回連続で同一ハッシュになったフレームを確定する
      （ページ送り直後のローディング中フレームの誤確定を防ぐ）。
    - crop_fraction>0 なら撮影直後に上端のタイトルバー帯を落とす（明度/pHash は
      クロップ後の本文画像で判定する）。
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
        grab(cap.region, tmp_path, window_id)
        if crop_fraction:
            imaging.crop_top_fraction(tmp_path, crop_fraction)

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

    - 新規ページのみ `naming.page_filename()`（`raw/page_{n:06d}.png`）に欠け・重複なく連番保存する。
    - 直前確定フレームと `same_threshold` 以内で一致したら duplicate とみなし、
      `end_detect_repeats` 連続で最終ページと判定して停止する（duplicate は保存しない）。
    - 1反復ごとに state を `state_path` へ逐次コミットする（レジューム対応）。
    """
    cap = cfg.capture
    raw_dir = Path(work_dir) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    # auto_region: セッション開始時に Kindle ウィンドウ ID を検出し、以降 `-l` で直接撮る。
    # ウィンドウは移動・別 Space 化し得るため実行時に毎回検出する（ID はセッション毎に変わる）。
    # タイトルバー帯の高さも開始時に一度だけ AX で実測し、比率クロップに使い回す
    # （セッション中は帯高が変わらないため毎フレーム再検出しない）。
    window_id = None
    crop_fraction = 0.0
    if cap.auto_region:
        window_id, bounds, pid = detect_window_id(cap.app_name)
        titlebar_pt = detect_titlebar_pt(pid, bounds)
        win_h = bounds[3]
        crop_fraction = titlebar_pt / win_h if win_h else 0.0
        logger.info(
            "ウィンドウ自動検出: id=%s 矩形=%s タイトルバー=%.1fpt（上端のみクロップ・本文余白は保全）",
            window_id, bounds, titlebar_pt,
        )
    # 一時ファイルは raw/ の外（work_dir 直下）かつ非ドットファイルにする。
    # macOS の screencapture はドットファイル（.pending.png）に書けず、
    # raw/*.png を glob する preprocess に temp を拾わせないため raw/ の外に置く。
    pending = Path(work_dir) / "pending.tmp.png"

    # レジューム: 既存 state から直前確定フレーム・連続一致数を引き継ぐ。
    prev_hash = imaging.hex_to_hash(state.last_hash) if state.last_hash else None
    repeat = state.repeat_count
    logger.info("撮影開始: %d ページ目から（上限 %d）", state.captured + 1, cap.max_pages)

    try:
        while state.captured < cap.max_pages:
            h = _grab_confirmed(cfg, pending, window_id, crop_fraction)

            if prev_hash is not None and imaging.is_same(h, prev_hash, cap.same_threshold):
                # ページ送りが効いていない = 最終ページに到達している可能性。
                repeat += 1
                state.repeat_count = repeat
                if repeat >= cap.end_detect_repeats:
                    logger.info(
                        "最終ページを検出したため撮影を終了します（確定 %d ページ）",
                        state.captured,
                    )
                    state.save(state_path)
                    break
            else:
                # 新規ページ: 連番で確定保存し、state を逐次コミットする。
                seq = state.captured
                dest = raw_dir / naming.page_filename(seq)
                pending.replace(dest)
                state.captured = seq + 1
                state.last_hash = str(h)
                state.hash_history.append(str(h))
                repeat = 0
                state.repeat_count = 0
                logger.info("撮影確定: %d ページ目", state.captured)

            state.save(state_path)
            prev_hash = h
            turn_page(cfg)
            time.sleep(cap.page_turn_wait)
    finally:
        if pending.exists():
            pending.unlink()
