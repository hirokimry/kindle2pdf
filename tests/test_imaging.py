"""imaging の純粋ロジックの単体テスト（macOS依存なし）。

fixtures/ に PoC実画像が置かれた場合は同一/別ページの距離も検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kindle2pdf import imaging

FIXTURES = Path(__file__).parent / "fixtures"


def _make_image(tmp_path: Path, color, size=(64, 64)) -> Path:
    from PIL import Image

    p = tmp_path / f"{color}.png"
    Image.new("RGB", size, color).save(p)
    return p


def test_is_same_identical(tmp_path):
    p = _make_image(tmp_path, (255, 255, 255))
    h = imaging.phash(p)
    assert imaging.hamming(h, h) == 0
    assert imaging.is_same(h, h, threshold=2)


def test_is_same_threshold_rejects_different(tmp_path):
    # 単色 と 複雑なグラデ柄は明確に別物 → 距離は閾値2を大きく超える
    from PIL import Image

    solid = _make_image(tmp_path, (255, 255, 255), size=(128, 128))
    grad_path = tmp_path / "grad.png"
    im = Image.new("RGB", (128, 128))
    px = im.load()
    for x in range(128):
        for y in range(128):
            px[x, y] = ((x * 2) % 256, (y * 2) % 256, (x + y) % 256)
    im.save(grad_path)

    a, b = imaging.phash(solid), imaging.phash(grad_path)
    assert not imaging.is_same(a, b, threshold=2)


def test_mean_brightness(tmp_path):
    black = _make_image(tmp_path, (0, 0, 0))
    white = _make_image(tmp_path, (255, 255, 255))
    assert imaging.mean_brightness(black) < 20
    assert imaging.mean_brightness(white) > 200


@pytest.mark.skipif(not FIXTURES.glob("*.png"), reason="PoC実画像なし")
def test_consecutive_pages_are_distinguishable():
    pngs = sorted(FIXTURES.glob("*.png"))
    if len(pngs) < 2:
        pytest.skip("PoC実画像が2枚未満")
    a, b = imaging.phash(pngs[0]), imaging.phash(pngs[1])
    # PoC実測: 連続する別ページ間 = 距離16〜26
    assert imaging.hamming(a, b) > 2
