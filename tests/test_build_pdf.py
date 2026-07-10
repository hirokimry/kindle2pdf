"""build_pdf の座標変換・検索可能性のゴールデン回帰テスト（P6・システムの肝）。

著作物（PoC実画像）に依存しない自己完結型テスト。Pillow で「本文文字域」を
表す黒矩形を既知ピクセル位置に描き、その領域を Vision 相当の bbox（正規化・
原点左下）へ変換して build_pdf に渡す。生成 PDF から pypdf でテキストと描画
位置を取り出し、以下を固定する:

- 既知語が検索ヒットする（不可視テキスト層が有効）
- 不可視テキストの矩形が本文文字域と重なる（座標変換の妥当性）
- Y反転が起きていない（本文域を上部に置き、PDF側でも上部に来ることを確認）
- px→pt 換算が固定（ページサイズ・基準点が iw*72/dpi で一致）

macOS 依存なし・追加のバイナリ資産なしで動くため CI(Linux) でも緑になる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from kindle2pdf.build_pdf import build
from kindle2pdf.config import Config

# 検索ヒットを確認する既知語（Vision の日本語 OCR を模す）
WORD_TOP = "デプスインタビュー"
WORD_BOTTOM = "ゴールデン回帰"

IMG_W, IMG_H = 600, 800  # 生成画像のピクセルサイズ
TOL = 0.5               # 座標許容誤差（pt）。換算は理論上厳密なので小さくてよい


def _pixel_boxes() -> dict[str, tuple[int, int, int, int]]:
    """本文文字域のピクセル bbox（PIL 座標＝原点左上, left,top,right,bottom）。

    どちらも上下非対称に配置し、Y反転バグが起きれば必ず検出できるようにする。
    """
    return {
        # 画像の上部（PIL の y が小さい＝PDF では y が大きい側）
        WORD_TOP: (120, 80, 480, 140),
        # 画像の中下部
        WORD_BOTTOM: (150, 520, 450, 580),
    }


def _make_page_image(path: Path) -> None:
    """白地に本文文字域を表す黒矩形を描いた合成ページ画像を生成する。"""
    im = Image.new("RGB", (IMG_W, IMG_H), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    for left, top, right, bottom in _pixel_boxes().values():
        draw.rectangle((left, top, right, bottom), fill=(0, 0, 0))
    im.save(path)


def _vision_item(word: str, px_box: tuple[int, int, int, int]) -> tuple[str, float, list[float]]:
    """ピクセル bbox を Vision 相当の OcrItem（正規化・原点左下）へ変換する。

    PIL は原点左上・Vision は原点左下なので、y のみ反転して底辺を基準にする。
    """
    left, top, right, bottom = px_box
    x = left / IMG_W
    w = (right - left) / IMG_W
    y = (IMG_H - bottom) / IMG_H  # 原点左下: 箱の底辺
    h = (bottom - top) / IMG_H
    return (word, 0.99, [x, y, w, h])


def _expected_pdf_region(px_box: tuple[int, int, int, int], dpi: int) -> tuple[float, float, float, float]:
    """ピクセル bbox を PDF 座標（原点左下, pt）へ射影する: (left, bottom, right, top)。"""
    f = 72.0 / dpi
    left, top, right, bottom = px_box
    pdf_left = left * f
    pdf_right = right * f
    pdf_bottom = (IMG_H - bottom) * f
    pdf_top = (IMG_H - top) * f
    return (pdf_left, pdf_bottom, pdf_right, pdf_top)


def _build_pdf(tmp_path: Path) -> tuple[Path, Config]:
    """合成画像＋既知 bbox から検索可能 PDF を1本生成して返す。"""
    img = tmp_path / "page.png"
    _make_page_image(img)
    boxes = _pixel_boxes()
    items = [_vision_item(w, boxes[w]) for w in (WORD_TOP, WORD_BOTTOM)]
    out = tmp_path / "out.pdf"
    cfg = Config()
    build([(str(img), items)], out, cfg)
    return out, cfg


def _collect_runs(pdf: Path) -> list[tuple[str, float, float, float]]:
    """PDF 1ページ目の各テキスト run を (text, x, y, font_size) で集める。"""
    reader = PdfReader(str(pdf))
    runs: list[tuple[str, float, float, float]] = []

    def visit(text, cm, tm, font_dict, font_size):  # noqa: ANN001
        if text.strip():
            runs.append((text.strip(), tm[4], tm[5], font_size))

    reader.pages[0].extract_text(visitor_text=visit)
    return runs


def test_known_words_are_searchable(tmp_path):
    """生成 PDF から既知語が抽出でき、検索ヒットする。"""
    pdf, _ = _build_pdf(tmp_path)
    text = PdfReader(str(pdf)).pages[0].extract_text()
    assert WORD_TOP in text
    assert WORD_BOTTOM in text


def test_invisible_text_overlaps_body_region(tmp_path):
    """不可視テキストの位置が本文文字域と重なる（座標変換の妥当性・Y反転なし）。"""
    pdf, cfg = _build_pdf(tmp_path)
    runs = _collect_runs(pdf)
    boxes = _pixel_boxes()

    for word in (WORD_TOP, WORD_BOTTOM):
        matched = [r for r in runs if r[0] == word]
        assert matched, f"{word} の描画 run が見つからない"
        _text, x, y, fsize = matched[0]
        left, bottom, right, top = _expected_pdf_region(boxes[word], cfg.build.target_dpi)

        # px→pt 換算が固定: ベースラインが本文域の左下に一致する
        assert abs(x - left) <= TOL
        assert abs(y - bottom) <= TOL
        # フォント高さが箱の高さに一致する
        assert abs(fsize - (top - bottom)) <= TOL
        # ベースライン＋フォント高で本文域内に収まる（矩形が重なる）
        assert left - TOL <= x <= right
        assert bottom - TOL <= y <= top
        assert y + fsize <= top + TOL


def test_no_y_flip_top_region_stays_on_top(tmp_path):
    """本文域を画像上部に置くと PDF でも上半分に来る（Y反転していない証拠）。"""
    pdf, cfg = _build_pdf(tmp_path)
    runs = _collect_runs(pdf)
    ph = IMG_H * 72.0 / cfg.build.target_dpi  # ページ高さ(pt)

    top_run = next(r for r in runs if r[0] == WORD_TOP)
    # 上部の本文域なので PDF ベースライン y はページ中央より上にあるはず
    assert top_run[2] > ph / 2


def test_text_render_mode_is_invisible(tmp_path):
    """テキストが RenderMode 3（不可視）で描画されている。"""
    pdf, _ = _build_pdf(tmp_path)
    data = PdfReader(str(pdf)).pages[0].get_contents().get_data()
    assert b"3 Tr" in data


def test_page_size_matches_pixel_to_point(tmp_path):
    """ページサイズが iw*72/dpi・ih*72/dpi と一致（px→pt 換算の固定）。"""
    pdf, cfg = _build_pdf(tmp_path)
    f = 72.0 / cfg.build.target_dpi
    box = PdfReader(str(pdf)).pages[0].mediabox
    assert abs(float(box.width) - IMG_W * f) <= TOL
    assert abs(float(box.height) - IMG_H * f) <= TOL


def test_multipage_each_page_searchable(tmp_path):
    """複数ページ入力でも各ページで既知語が検索ヒットする（showPage の回帰）。"""
    img = tmp_path / "page.png"
    _make_page_image(img)
    boxes = _pixel_boxes()
    page_items = [_vision_item(w, boxes[w]) for w in (WORD_TOP, WORD_BOTTOM)]
    out = tmp_path / "multi.pdf"
    build([(str(img), page_items), (str(img), page_items)], out, Config())

    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    for page in reader.pages:
        text = page.extract_text()
        assert WORD_TOP in text
        assert WORD_BOTTOM in text


def test_empty_text_items_are_skipped(tmp_path):
    """空文字の bbox は不可視テキストを生まない（build_pdf のスキップ挙動）。"""
    img = tmp_path / "page.png"
    _make_page_image(img)
    items = [("", 0.99, [0.1, 0.5, 0.3, 0.05]), _vision_item(WORD_TOP, _pixel_boxes()[WORD_TOP])]
    out = tmp_path / "skip.pdf"
    build([(str(img), items)], out, Config())

    runs = _collect_runs(out)
    assert [r[0] for r in runs] == [WORD_TOP]
