"""P8（仕上げ）の回帰テスト — JPEG圧縮・画像のみ継続・各段の進捗ログ。

著作物に依存しない自己完結テスト。合成画像を使い以下を固定する:

- image_format="jpeg" で PDF 画像が DCTDecode（JPEG圧縮）になる
- image_format="png" で PDF 画像が FlateDecode（可逆）になる
- jpeg_quality を下げると出力 PDF が小さくなる（品質が実際に効く）
- JPEG 圧縮でもテキスト層は保持され検索ヒットする
- テキスト層の無いページ（OCR失敗/未実施）は画像のみで積み、枚数をログに記録する
- 各段（preprocess / ocr / build / pipeline）が INFO 進捗ログを出す

macOS 依存なし・追加のバイナリ資産なしで動くため CI(Linux) でも緑になる。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from kindle2pdf import naming
from kindle2pdf import ocr as ocr_mod
from kindle2pdf import pipeline, preprocess
from kindle2pdf.build_pdf import build
from kindle2pdf.config import Config
from kindle2pdf.state import State

IMG_W, IMG_H = 600, 800
WORD = "デプスインタビュー"


def _make_text_page(path: Path) -> None:
    """白地に本文文字域を表す黒矩形を描いた合成ページ画像を生成する。"""
    im = Image.new("RGB", (IMG_W, IMG_H), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((120, 80, 480, 140), fill=(0, 0, 0))
    im.save(path)


def _make_detailed_page(path: Path) -> None:
    """JPEG 品質差が出るよう階調のある合成ページ画像を生成する。"""
    im = Image.new("RGB", (IMG_W, IMG_H))
    px = im.load()
    for x in range(IMG_W):
        for y in range(0, IMG_H, 2):
            v = (x * 3 + y * 5) % 256
            px[x, y] = (v, (255 - v) % 256, (v * 2) % 256)
    im.save(path)


def _vision_item() -> tuple[str, float, list[float]]:
    """本文黒矩形に重なる Vision 相当の OcrItem（正規化・原点左下）。"""
    left, top, right, bottom = 120, 80, 480, 140
    x = left / IMG_W
    w = (right - left) / IMG_W
    y = (IMG_H - bottom) / IMG_H
    h = (bottom - top) / IMG_H
    return (WORD, 0.99, [x, y, w, h])


def _image_filters(pdf_path: Path) -> str:
    """PDF 1ページ目の画像 XObject の /Filter を文字列化して返す。"""
    reader = PdfReader(str(pdf_path))
    xobjects = reader.pages[0]["/Resources"]["/XObject"]
    filters = [str(v.get_object()["/Filter"]) for v in xobjects.values()]
    return " ".join(filters)


def test_jpeg_format_embeds_dctdecode(tmp_path):
    """image_format='jpeg' で埋め込み画像が DCTDecode（JPEG圧縮）になる。"""
    img = tmp_path / "page.png"
    _make_text_page(img)
    out = tmp_path / "jpeg.pdf"
    cfg = Config()  # 既定 image_format='jpeg'
    build([(str(img), [_vision_item()])], out, cfg)
    assert "DCTDecode" in _image_filters(out)


def test_png_format_embeds_flatedecode(tmp_path):
    """image_format='png' で埋め込み画像が FlateDecode（可逆）になる。"""
    img = tmp_path / "page.png"
    _make_text_page(img)
    out = tmp_path / "png.pdf"
    cfg = Config()
    cfg.build.image_format = "png"
    build([(str(img), [_vision_item()])], out, cfg)
    filters = _image_filters(out)
    assert "FlateDecode" in filters
    assert "DCTDecode" not in filters


def test_lower_jpeg_quality_reduces_pdf_size(tmp_path):
    """jpeg_quality を下げると出力 PDF が小さくなる（品質が実際に効く）。"""
    img = tmp_path / "detailed.png"
    _make_detailed_page(img)

    def _size(quality: int) -> int:
        cfg = Config()
        cfg.build.jpeg_quality = quality
        out = tmp_path / f"q{quality}.pdf"
        build([(str(img), [_vision_item()])], out, cfg)
        return out.stat().st_size

    assert _size(20) < _size(95)


def test_jpeg_keeps_text_searchable(tmp_path):
    """JPEG 圧縮でもテキスト層は保持され既知語が検索ヒットする。"""
    img = tmp_path / "page.png"
    _make_text_page(img)
    out = tmp_path / "jpeg.pdf"
    build([(str(img), [_vision_item()])], out, Config())
    text = PdfReader(str(out)).pages[0].extract_text()
    assert WORD in text


def test_image_only_pages_are_logged(tmp_path, caplog):
    """テキスト層の無いページは画像のみで積み、枚数を INFO ログに記録する。"""
    img = tmp_path / "page.png"
    _make_text_page(img)
    out = tmp_path / "mixed.pdf"
    pages = [(str(img), [_vision_item()]), (str(img), [])]  # 2枚目はテキスト層なし
    with caplog.at_level(logging.INFO, logger="kindle2pdf.build_pdf"):
        build(pages, out, Config())

    # 2ページとも描画され、テキスト層なしページ数がログに残る
    assert len(PdfReader(str(out)).pages) == 2
    assert any("画像のみ" in r.message and "1/2" in r.message for r in caplog.records)


def test_build_logs_start_progress(tmp_path, caplog):
    """build 開始ログに総ページ数・画像形式が出る（進捗追跡）。"""
    img = tmp_path / "page.png"
    _make_text_page(img)
    out = tmp_path / "log.pdf"
    with caplog.at_level(logging.INFO, logger="kindle2pdf.build_pdf"):
        build([(str(img), [_vision_item()])], out, Config())
    assert any("PDF生成開始" in r.message for r in caplog.records)


def test_preprocess_logs_progress(tmp_path, caplog):
    """preprocess が print ではなく logger で開始/完了ログを出す。"""
    cfg = Config()
    cfg.book_title = "log-book"
    cfg.preprocess.trim = {}
    cfg.preprocess.min_brightness = 0
    wd = tmp_path / "wd"
    (wd / "raw").mkdir(parents=True)
    _make_text_page(wd / "raw" / "page_0000.png")

    with caplog.at_level(logging.INFO, logger="kindle2pdf.preprocess"):
        preprocess.process_all(cfg, State(book_title=cfg.book_title), wd)
    messages = " ".join(r.message for r in caplog.records)
    assert "preprocess 開始" in messages
    assert "preprocess 完了" in messages


def test_pipeline_logs_stage_banners(tmp_path, monkeypatch, caplog):
    """pipeline.run が各段の開始バナーを INFO で出す。"""
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.book_title = "banner-book"
    cfg.preprocess.trim = {}
    cfg.preprocess.min_brightness = 0

    def fake_run_capture(cfg, state, work_dir, state_path):  # noqa: ANN001
        raw_dir = Path(work_dir) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        _make_text_page(raw_dir / "page_0000.png")
        state.captured = 1
        state.save(state_path)

    def fake_ocr_page(path, cfg):  # noqa: ANN001
        return [_vision_item()]

    monkeypatch.setattr(pipeline.capture, "run_capture", fake_run_capture)
    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr_page)

    with caplog.at_level(logging.INFO, logger="kindle2pdf.pipeline"):
        pipeline.run(cfg)

    messages = " ".join(r.message for r in caplog.records)
    for stage in ("capture", "preprocess", "ocr", "build"):
        assert f"{stage} 段を開始します" in messages
    assert "全段完了" in messages


def test_invalid_image_format_rejected():
    """image_format が jpeg/png 以外は validate で弾く。"""
    cfg = Config()
    cfg.build.image_format = "gif"
    with pytest.raises(ValueError):
        cfg.validate()


def test_invalid_jpeg_quality_rejected():
    """jpeg_quality が 1〜100 外は validate で弾く。"""
    cfg = Config()
    cfg.build.jpeg_quality = 0
    with pytest.raises(ValueError):
        cfg.validate()


def test_page_filename_sorts_lexicographically_across_magnitudes():
    """ゼロ埋め桁が十分広く、桁数の異なるページ番号でも辞書順=番号順になる。

    `:04d` は 9999 を超えると辞書順が破綻するが、共通ヘルパーの広い桁なら
    数百〜数万ページでも sorted() がページ番号順と一致する（P8: 安定動作）。
    """
    numbers = [0, 1, 9, 10, 99, 100, 999, 1000, 6000, 9999, 10000, 99999]
    names = [naming.page_filename(n) for n in numbers]
    # 辞書順ソートした並びが、番号昇順の並びと一致する
    assert sorted(names) == [naming.page_filename(n) for n in sorted(numbers)]


def test_page_filename_uses_shared_width():
    """capture/preprocess が使う採番桁が一元化されている（横断で一貫）。"""
    # 実装と同じフォーマット式で再計算せず、期待値をリテラルで固定する（実装バグを検出できるように）
    assert naming.page_filename(1) == "page_000001.png"
    assert naming.page_filename(42) == "page_000042.png"
    assert naming.PAGE_NUM_WIDTH >= 6  # 上限なし撮影（#45）の大冊でも桁溢れしない 6 桁幅


def test_preprocess_emits_padded_page_names(tmp_path):
    """preprocess が共通ヘルパーの桁で pages/ を採番する（辞書順ソート担保）。"""
    cfg = Config()
    cfg.book_title = "pad-book"
    cfg.preprocess.trim = {}
    cfg.preprocess.min_brightness = 0
    wd = tmp_path / "wd"
    (wd / "raw").mkdir(parents=True)
    _make_text_page(wd / "raw" / "page_000000.png")

    preprocess.process_all(cfg, State(book_title=cfg.book_title), wd)

    produced = [p.name for p in (wd / "pages").glob("page_*.png")]
    assert produced == [naming.page_filename(1)]


def test_png_format_skips_jpeg_quality_check():
    """png 指定時は jpeg_quality の範囲検証を課さない（可逆なので無関係）。"""
    cfg = Config()
    cfg.build.image_format = "png"
    cfg.build.jpeg_quality = 0  # png では無視される
    cfg.validate()  # 例外が出なければ OK
