"""capture 段 — Kindle制御・撮影・最終ページ検出。

責務分離の要: システム内で **Kindleに触れるのはこのモジュールのみ**。
preprocess/ocr/build は撮影済み画像だけを入力とするバッチで、
Kindle操作なしに何度でも再実行できる。

実装チケット: P1(region実測) / P2(撮影ループ) / P3(最終ページ検出)
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from . import imaging, naming, progress
from .config import Config
from .state import State

# 未指定時に順に試す Kindle アプリ名。新しめの Mac 版は "Amazon Kindle"、旧版は "Kindle"。
# 見つかった名前をキャッシュして次回以降の探索を省く（#33）。
KINDLE_APP_CANDIDATES = ("Amazon Kindle", "Kindle")

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

    ウィンドウを自動検出し `-l` で撮り、AX 実測のタイトルバー帯を上端クロップする。
    返す region はクロップ後の実撮影領域に揃え、表示の齟齬を防ぐ。Quartz/AX が使えない・
    Kindle 未起動・アクセシビリティ権限未付与・ウィンドウ/信号機ボタン不検出・帯高が異常値の
    ときは RuntimeError を送出する（誤クロップより明確なエラーで止める）。

    撮影後の画像を開けば UI・柱・余白が入らず本文だけが写るかを目視確認できる。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / "calibrate.png"
    # 未指定なら候補試行で app_name を自動決定する（明示指定はそのまま優先）（#33）。
    app_name = resolve_app_name(cfg.capture.app_name)
    # ウィンドウ ID を検出し `-l` で直接撮る（前面化不要・別 Space 可）。撮影像に含まれる
    # タイトルバー帯だけを AX 実測の高さで上端クロップし、本文余白は残す。
    window_id, region, pid = detect_window_id(app_name)
    grab(out_path, window_id)
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
    return out_path, region


def _app_name_cache_path() -> Path:
    """自動検出した app_name のキャッシュ先。ユーザーキャッシュ配下（マシン共通）。"""
    # 特定マシンパスをハードコードせず、環境変数→ホーム配下の順で解決する（public-ready）。
    root = os.environ.get("KINDLE2PDF_CACHE_DIR")
    base = Path(root) if root else Path.home() / ".cache" / "kindle2pdf"
    return base / "app_name"


def _read_cached_app_name(cache_path: Path) -> str | None:
    try:
        name = cache_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def _write_cached_app_name(cache_path: Path, name: str) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(name, encoding="utf-8")
    except OSError:
        # キャッシュは高速化のための任意機能。書けなくても検出自体は成功しているので握り潰す。
        logger.debug("app_name のキャッシュ書き込みに失敗しました（無視して継続）: %s", cache_path)


def _applescript_name_works(name: str) -> bool:
    """AppleScript がこのアプリ名を解決できる（インストール済みの）か確認する。

    `id of application "<name>"` はアプリを起動せずにバンドル ID を返し、名前が無ければ
    -1728 で失敗する。detect_window_id はウィンドウ所有者名の末尾語（"Kindle"）で部分一致
    するため "Amazon Kindle" と "Kindle" を区別できないが、本判定は AppleScript が実際に
    受け付ける名前かを厳密に確かめる。これにより activate / turn_page に渡して -1728 になる
    名前を候補から弾ける（#33）。
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", f'id of application "{name}"'],
            capture_output=True,
            text=True,
        )
    except OSError:  # 非 macOS など osascript 不在
        return False
    return result.returncode == 0


def resolve_app_name(
    configured: str | None = None,
    *,
    verifier=None,
    cache_path: Path | None = None,
) -> str:
    """撮影・ページ送りに使う Kindle アプリ名を決めて返す（#33）。

    明示指定（configured 非空）があればそれを最優先する。未指定なら
    「キャッシュ済みの名前 → 既定候補（"Amazon Kindle" → "Kindle"）」の順に
    AppleScript で受け付けられる名前かを検証し、最初に通った名前を採用してキャッシュする。
    どの候補も通らなければ、次のアクションが分かる明確なエラーで止める（サイレントに
    誤検出しない）。verifier / cache_path はテスト用の注入口。
    """
    if configured:
        return configured
    verify = verifier or _applescript_name_works
    cpath = cache_path or _app_name_cache_path()

    candidates: list[str] = []
    cached = _read_cached_app_name(cpath)
    if cached:
        candidates.append(cached)
    for name in KINDLE_APP_CANDIDATES:
        if name not in candidates:
            candidates.append(name)

    tried: list[str] = []
    for name in candidates:
        if verify(name):
            _write_cached_app_name(cpath, name)
            return name
        tried.append(name)
    raise RuntimeError(
        "Kindle アプリが見つかりません（試した名前: "
        + ", ".join(repr(t) for t in tried)
        + "）。Kindle を起動して再実行してください。"
        "アプリ名が特殊な場合は config.yaml の capture.app_name に明示してください。"
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

    全ウィンドウから app 名一致・レイヤ0 を候補とし、Kindle が裏で生成する **名前なしの
    ゴミウィンドウ**（黒い補助窓・細い帯）を除外するため、ウィンドウ名を持つ候補があれば
    それだけに絞ってから最大面積を本体とみなす。System Events のプロセス名は app 名と異なる
    ことがあるため app_name の末尾語で部分一致させる。PID は AX でタイトルバーを実測するために返す。
    """
    if Quartz is None:
        raise RuntimeError(
            "Quartz が利用できません（pip install pyobjc-framework-Quartz が必要）"
        )
    keyword = app_name.split()[-1] if app_name.split() else app_name
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
    )
    # (面積, id, 矩形, pid, ウィンドウ名有無)。候補が複数出る（2冊同時・設定/検索パネル等）
    # ときは面積最大を本体とみなすが、誤ウィンドウをサイレントに撮り続ける事故を避けるため警告する。
    candidates: list[tuple[int, int, tuple[int, int, int, int], int, bool]] = []
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
        name = w.get("kCGWindowName", "") or ""
        candidates.append(
            (rect[2] * rect[3], int(w.get("kCGWindowNumber")), rect,
             int(w.get("kCGWindowOwnerPID", 0)), bool(name))
        )
    if not candidates:
        raise RuntimeError(f"Kindle ウィンドウが見つかりません（app_name={app_name!r}）")
    # Kindle は本文ウィンドウとは別に、名前なしの黒いゴミ窓（500x500 等）や細い帯を裏で作る。
    # 本文ウィンドウだけが非空のウィンドウ名を持つため、名前ありが1つでもあれば名前なしを除外する。
    # これで「面積最大がゴミ窓を掴んで黒画面」になる事故を config/calibrate なしに防ぐ。
    # 全て名前なし（画面収録権限が未付与等）の場合のみ、従来の全候補・面積最大にフォールバックする。
    named = [c for c in candidates if c[4]]
    if named:
        candidates = named
    candidates.sort(key=lambda c: c[0], reverse=True)
    if len(candidates) > 1:
        logger.warning(
            "Kindle ウィンドウが %d 個見つかりました。面積最大の id=%s 矩形=%s を本体として撮影します"
            "（意図と違う場合は不要なウィンドウ／パネルを閉じるか calibrate で確認してください）。",
            len(candidates), candidates[0][1], candidates[0][2],
        )
    best = candidates[0]
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


def _auto_region_params(
    app_name: str,
) -> tuple[int, tuple[int, int, int, int], float]:
    """auto_region 用に (window_id, ウィンドウ矩形, 上端クロップ比率) を実測して返す。

    クロップ比率 = タイトルバー帯高[pt] ÷ ウィンドウ高[pt]。比率で持つことで retina 倍率に
    依存せず、ページごとに呼び直せばウィンドウのリサイズ/移動にも追従できる。
    """
    window_id, bounds, pid = detect_window_id(app_name)
    titlebar_pt = detect_titlebar_pt(pid, bounds)
    win_h = bounds[3]
    crop_fraction = titlebar_pt / win_h if win_h else 0.0
    return window_id, bounds, crop_fraction


def turn_page(cfg: Config, app_name: str | None = None) -> None:
    """右/左矢印キーを送出してページ送りする（osascript / cliclick）。

    osascript: key code 124=右, 123=左。cliclick: kp:arrow-right/left。
    キー送出には実行元Terminalに「アクセシビリティ」権限が必要。
    app_name 未指定時は cfg の値を使う（run_capture は自動検出済みの名前を渡す・#33）。
    """
    key = cfg.capture.page_turn_key
    method = cfg.capture.page_turn_method
    name = app_name if app_name else cfg.capture.app_name
    if key not in _KEY_CODE:
        raise ValueError(f"page_turn_key は right / left のいずれか（受領値: {key}）。")

    if method == "osascript":
        code = _KEY_CODE[key]
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "{name}" to activate',
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


def grab(out_path: str | Path, window_id: int) -> str:
    """Kindle ウィンドウ(window_id)をサムネイルを出さずに撮影する。

    `screencapture -x -o -l <id>` でウィンドウを直接撮る（前面化不要・別 Space 可・
    タイトルバー無し・中身をそのまま=余白を削らない）。-x でシャッター音・フローティング
    サムネイルを抑止（PoC 7.2 の必須条件）、-o で影を除く。
    """
    cmd = ["screencapture", "-x", "-o", "-l", str(window_id), str(out_path)]
    subprocess.run(cmd, check=True)
    return str(out_path)


def _grab_confirmed(
    cfg: Config,
    tmp_path: Path,
    window_id: int,
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
        grab(tmp_path, window_id)
        if crop_fraction:
            imaging.crop_top_fraction(tmp_path, crop_fraction)

        if imaging.mean_brightness(tmp_path) < min_brightness:
            # 黒画面のリトライ回数が上限（_MAX_BLACK_RETRIES）に達したら諦める。
            if black_retries >= _MAX_BLACK_RETRIES:
                raise RuntimeError(
                    "黒画面が継続しました（Kindle 表示・ウィンドウ検出を確認してください）。"
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
    # 実行時に Kindle ウィンドウを検出し `-l` で直接撮る。ウィンドウは移動・別 Space 化・
    # リサイズし得るため **ページごとに再検出** し、クロップ比率(帯高÷ウィンドウ高)を毎回
    # 最新化する。セッション中に一度だけ算出して使い回すと、リサイズ時に比率がずれて枠が
    # 写る／余白を削る事故になるため（帯高は pt 不変でも比率は高さに依存する）。
    # app_name はウィンドウ検出にもページ送りの activate にも要る（未指定なら候補を
    # AppleScript 検証で自動決定・#33）。
    app_name = resolve_app_name(cap.app_name)
    window_id, last_bounds, crop_fraction = _auto_region_params(app_name)
    logger.info(
        "ウィンドウ自動検出: id=%s 矩形=%s（上端タイトルバーのみクロップ・本文余白は保全）",
        window_id, last_bounds,
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
            # リサイズ/移動に追従してクロップ比率を最新化する（枠の写り込み・余白削りを防ぐ）。
            window_id, bounds, crop_fraction = _auto_region_params(app_name)
            if bounds != last_bounds:
                logger.info(
                    "ウィンドウ変化を検出: 矩形=%s に追従しクロップ比率を再算出しました", bounds
                )
                last_bounds = bounds
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
                # 総数は最終ページ検出まで未知のため total=None（フロントは件数のみ更新）。
                progress.emit("page", stage="capture", page=state.captured, total=None)

            state.save(state_path)
            prev_hash = h
            turn_page(cfg, app_name)
            time.sleep(cap.page_turn_wait)
    finally:
        if pending.exists():
            pending.unlink()
