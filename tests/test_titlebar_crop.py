"""動的タイトルバークロップの単体テスト（macOS非依存）。

AX（ApplicationServices）をスタブに差し替え、`detect_titlebar_pt` の実測式と、
`imaging.crop_top_fraction` が **タイトルバー帯だけ落とし本文の白余白を保全する** ことを
検証する。後者は CEO 絶対制約「余白を削らない」の自動回帰テスト。
"""

from __future__ import annotations

import pytest
from PIL import Image

from kindle2pdf import capture, imaging


# --- AX スタブ ---


class _Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Sz:
    def __init__(self, w, h):
        self.width, self.height = w, h


class _AXElem:
    """AX 要素の最小モデル（属性辞書のみ持つ）。"""

    def __init__(self, attrs):
        self.attrs = attrs


class _FakeAX:
    """ApplicationServices の AX 関数群を模した最小スタブ。"""

    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2

    def __init__(self, app):
        self._app = app

    def AXUIElementCreateApplication(self, pid):  # noqa: N802
        return self._app

    def AXUIElementCopyAttributeValue(self, el, name, _):  # noqa: N802
        if name in el.attrs:
            return (0, el.attrs[name])
        return (-1, None)

    def AXValueGetValue(self, val, typ, _):  # noqa: N802
        return (True, val)


def _window_with_traffic_light(win_bounds, close_btn):
    """(ウィンドウ, close ボタン) を持つ AX アプリ要素を組み立てる。

    win_bounds: (x, y, w, h)。close_btn: (x, y, w, h)。
    """
    wx, wy, ww, wh = win_bounds
    bx, by, bw, bh = close_btn
    button = _AXElem({
        "AXSubrole": "AXCloseButton",
        "AXPosition": _Pt(bx, by),
        "AXSize": _Sz(bw, bh),
        "AXChildren": [],
    })
    window = _AXElem({
        "AXPosition": _Pt(wx, wy),
        "AXSize": _Sz(ww, wh),
        "AXChildren": [button],
    })
    return _AXElem({"AXWindows": [window]})


# --- detect_titlebar_pt ---


def test_titlebar_height_from_button_center(monkeypatch):
    """帯高 = 2×(ボタン中心y − ウィンドウ上端y)。実機実測値(28pt)を再現する。"""
    app = _window_with_traffic_light((0, 37, 1470, 919), (6, 43, 16, 16))
    monkeypatch.setattr(capture, "_AX", _FakeAX(app))
    tb = capture.detect_titlebar_pt(71518, (0, 37, 1470, 919))
    assert tb == pytest.approx(28.0)  # center=51, top=37 → 2×14


def test_titlebar_scales_with_button_position(monkeypatch):
    """ボタン位置が変われば帯高も追従する（固定値を持たない証拠）。"""
    app = _window_with_traffic_light((0, 100, 1000, 800), (10, 110, 20, 20))
    monkeypatch.setattr(capture, "_AX", _FakeAX(app))
    tb = capture.detect_titlebar_pt(1, (0, 100, 1000, 800))
    assert tb == pytest.approx(40.0)  # center=120, top=100 → 2×20


def test_titlebar_raises_without_ax(monkeypatch):
    """AX 不可時は誤クロップせず明確なエラーで止まる。"""
    monkeypatch.setattr(capture, "_AX", None)
    with pytest.raises(RuntimeError, match="ApplicationServices"):
        capture.detect_titlebar_pt(1, (0, 37, 1470, 919))


def test_titlebar_raises_without_traffic_lights(monkeypatch):
    """信号機ボタンが無ければ帯高を実測できないため明確なエラーで止まる。"""
    window = _AXElem({
        "AXPosition": _Pt(0, 37),
        "AXSize": _Sz(1470, 919),
        "AXChildren": [],
    })
    app = _AXElem({"AXWindows": [window]})
    monkeypatch.setattr(capture, "_AX", _FakeAX(app))
    with pytest.raises(RuntimeError, match="信号機ボタン"):
        capture.detect_titlebar_pt(1, (0, 37, 1470, 919))


def test_titlebar_raises_without_ax_windows(monkeypatch):
    """AX でウィンドウを取得できない（権限不足相当）と権限付与を促すエラー。"""
    app = _AXElem({})  # AXWindows 属性なし
    monkeypatch.setattr(capture, "_AX", _FakeAX(app))
    with pytest.raises(RuntimeError, match="アクセシビリティ"):
        capture.detect_titlebar_pt(1, (0, 37, 1470, 919))


# --- crop_top_fraction（余白ゼロ喪失の自動回帰）---


def _make_framed_page(path, band_px, margin_px, content_px):
    """タイトルバー帯(灰)＋白余白＋本文(黒)の3層を積んだ合成ページを作る。"""
    w = 200
    h = band_px + margin_px + content_px
    im = Image.new("RGB", (w, h), (254, 254, 254))
    px = im.load()
    for y in range(h):
        for x in range(w):
            if y < band_px:
                px[x, y] = (229, 229, 229)      # タイトルバー帯（灰）
            elif y < band_px + margin_px:
                px[x, y] = (254, 254, 254)       # 本文の白余白
            else:
                px[x, y] = (0, 0, 0)             # 本文（黒）
    im.save(path)
    return w, h


def test_crop_removes_titlebar_and_keeps_margin(tmp_path):
    """帯だけ落とし、本文の白余白は1pxも削らない（CEO 絶対制約の回帰）。"""
    p = tmp_path / "page.png"
    band, margin, content = 56, 60, 84
    w, h = _make_framed_page(p, band, margin, content)

    # 帯高/全高の比率でクロップ（実運用と同じ比率指定）。
    imaging.crop_top_fraction(p, band / h)

    out = Image.open(p).convert("RGB")
    assert out.size == (w, h - band)                 # 上端 band px ちょうど除去
    # クロップ後の最上行が白余白であること（本文余白を保全＝削っていない）。
    assert out.getpixel((0, 0)) == (254, 254, 254)
    assert out.getpixel((w - 1, 0)) == (254, 254, 254)
    # 灰の帯が1行も残っていないこと。
    top_rows = [out.getpixel((0, y)) for y in range(3)]
    assert all(c == (254, 254, 254) for c in top_rows)
    # 白余白の直下に本文（黒）が保全されていること（下端方向を削っていない）。
    assert out.getpixel((0, margin)) == (0, 0, 0)
    assert out.getpixel((0, out.size[1] - 1)) == (0, 0, 0)


def test_crop_noop_for_nonpositive_fraction(tmp_path):
    """fraction<=0 は何もしない（静的 region 運用や誤検出時の安全弁）。"""
    p = tmp_path / "page.png"
    _, h = _make_framed_page(p, 10, 10, 10)
    imaging.crop_top_fraction(p, 0)
    assert Image.open(p).size[1] == h


def test_crop_noop_when_fraction_covers_whole_image(tmp_path):
    """帯比率が画像全体を覆う異常値では切らない（本文喪失の防止）。"""
    p = tmp_path / "page.png"
    _, h = _make_framed_page(p, 10, 10, 10)
    imaging.crop_top_fraction(p, 1.0)
    assert Image.open(p).size[1] == h
