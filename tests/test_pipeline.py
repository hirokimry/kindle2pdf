"""pipeline の4段結線とレジューム（P7・F-8）の回帰テスト。

capture 段（Kindle操作）と ocr_page（Apple Vision, macOS専用）だけを monkeypatch で
差し替え、preprocess / build は実物（Pillow + reportlab）で通す。これにより
CI(Linux) でも「4段が順に通り検索可能PDFが出る」「途中Killから再開できる」を検証する。

検証対象:
- 4段フル実行で work/<book>/output/<book_title>.pdf が生成され全ページ検索ヒットする
- build 段のみからの再開（既存 pages/ + ocr/ を束ねる結線）
- OCR JSON が無いページは画像のみでPDF化する（仕様 4.3・クラッシュしない）
- ocr 段の途中Kill→再実行で未OCRページから続行し全ページ揃う
- stage=="done" 再実行は no-op（冪等）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from kindle2pdf import ocr as ocr_mod
from kindle2pdf import pipeline
from kindle2pdf.config import Config
from kindle2pdf.state import State

# 各ページに描く既知語（検索ヒット確認用）。ページ番号ごとに一意にする。
WORDS = ["だいいち", "だいに", "だいさん", "だいよん"]

# 生画像サイズ。1 撮影 = 1 ページ（分割しない）なので生画像1枚がそのまま1ページになる。
IMG_W, IMG_H = 400, 560


def _make_raw_image(path: Path) -> None:
    """本文文字域を表す黒矩形を1つ描いた合成生画像を作る。"""
    im = Image.new("RGB", (IMG_W, IMG_H), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((80, 60, 320, 120), fill=(0, 0, 0))
    im.save(path)


def _single_page_config(tmp_path: Path) -> Config:
    """1生画像→1ページになる Config（region は検証を通す固定値）。"""
    cfg = Config()
    cfg.book_title = "test-book"
    cfg.capture.region = [0, 0, IMG_W, IMG_H]
    cfg.preprocess.trim = {}                   # トリミング無効（黒矩形位置を保つ）
    cfg.preprocess.min_brightness = 0          # 合成画像を黒画面誤判定しない
    return cfg


def _stub_ocr_page_by_page(monkeypatch) -> None:
    """ocr_page を「ファイル名の連番に対応する既知語1件」を返すスタブに差し替える。

    page_0001.png → WORDS[0] のように、ページごとに検索できる語を1つ返す。
    bbox は本文文字域（黒矩形）に重なる正規化・原点左下座標。
    """
    def fake_ocr_page(path, cfg):  # noqa: ANN001
        idx = int(Path(path).stem.split("_")[-1]) - 1
        word = WORDS[idx % len(WORDS)]
        # 黒矩形 (80,60)-(320,120) を正規化・原点左下へ（build_pdf の座標系）
        x = 80 / IMG_W
        w = (320 - 80) / IMG_W
        y = (IMG_H - 120) / IMG_H
        h = (120 - 60) / IMG_H
        return [(word, 0.99, [x, y, w, h])]

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr_page)


def _stub_capture_writes_raw(monkeypatch, n_pages: int) -> None:
    """capture.run_capture を「raw/page_XXXX.png を n 枚書く」スタブに差し替える。"""
    def fake_run_capture(cfg, state, work_dir, state_path):  # noqa: ANN001
        raw_dir = Path(work_dir) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n_pages):
            _make_raw_image(raw_dir / f"page_{i:04d}.png")
        state.captured = n_pages
        state.save(state_path)

    monkeypatch.setattr(pipeline.capture, "run_capture", fake_run_capture)


def _searchable_texts(pdf_path: Path) -> list[str]:
    """PDF 各ページの抽出テキストを返す。"""
    return [page.extract_text() for page in PdfReader(str(pdf_path)).pages]


def test_run_full_pipeline_produces_searchable_pdf(tmp_path, monkeypatch):
    """4段フル実行で output/<book_title>.pdf が出て全ページ検索ヒットする。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    _stub_capture_writes_raw(monkeypatch, n_pages=3)
    _stub_ocr_page_by_page(monkeypatch)
    state_path = tmp_path / "state.json"

    pipeline.run(cfg, state_path)

    wd = pipeline.work_dir(cfg)
    out = pipeline.output_path(cfg, wd)
    assert out == wd / "output" / "test-book.pdf"
    assert out.exists()

    texts = _searchable_texts(out)
    assert len(texts) == 3
    for i, text in enumerate(texts):
        assert WORDS[i] in text, f"ページ{i+1}に {WORDS[i]} が検索ヒットしない"

    # 全段完了して done に到達している
    assert State.load(state_path).stage == "done"


def test_resume_from_build_stage_only(tmp_path, monkeypatch):
    """既存 pages/ + ocr/ から build 段だけ再開して PDF を生成できる。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    wd = pipeline.work_dir(cfg)
    pages_dir = wd / "pages"
    ocr_dir = wd / "ocr"
    pages_dir.mkdir(parents=True)
    ocr_dir.mkdir(parents=True)

    # 2ページ分の pages/ と対応する ocr/ JSON を先に用意する
    _stub_ocr_page_by_page(monkeypatch)
    for i in range(2):
        img = pages_dir / f"page_{i+1:04d}.png"
        _make_raw_image(img)
        items = ocr_mod.ocr_page(img, cfg)
        ocr_mod._write_page_json(ocr_dir / f"{img.stem}.json", img, items)

    # capture/preprocess/ocr が呼ばれたら失敗させる（build だけが走る証明）
    def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("build 段のみ走るべきなのに前段が呼ばれた")

    monkeypatch.setattr(pipeline.capture, "run_capture", _boom)
    monkeypatch.setattr(pipeline.preprocess, "process_all", _boom)
    monkeypatch.setattr(pipeline.ocr, "ocr_all", _boom)

    state_path = tmp_path / "state.json"
    State(book_title=cfg.book_title, stage="build").save(state_path)

    pipeline.run(cfg, state_path)

    out = pipeline.output_path(cfg, wd)
    texts = _searchable_texts(out)
    assert len(texts) == 2
    assert WORDS[0] in texts[0]
    assert WORDS[1] in texts[1]
    assert State.load(state_path).stage == "done"


def test_build_renders_image_only_page_when_ocr_missing(tmp_path, monkeypatch):
    """OCR JSON が無いページは画像のみでPDF化する（仕様4.3・クラッシュしない）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    wd = pipeline.work_dir(cfg)
    pages_dir = wd / "pages"
    ocr_dir = wd / "ocr"
    pages_dir.mkdir(parents=True)
    ocr_dir.mkdir(parents=True)

    _stub_ocr_page_by_page(monkeypatch)
    # 2ページ用意するが OCR JSON は1ページ目だけ作る
    for i in range(2):
        _make_raw_image(pages_dir / f"page_{i+1:04d}.png")
    img1 = pages_dir / "page_0001.png"
    ocr_mod._write_page_json(
        ocr_dir / "page_0001.json", img1, ocr_mod.ocr_page(img1, cfg)
    )

    out = pipeline.build_stage(cfg, wd)

    texts = _searchable_texts(out)
    assert len(texts) == 2               # 2ページとも描画される
    assert WORDS[0] in texts[0]          # OCR済みページは検索ヒット
    assert texts[1].strip() == ""        # OCR無しページはテキスト層なし


def test_resume_after_kill_in_ocr_stage(tmp_path, monkeypatch):
    """ocr 段の途中Kill→再実行で未OCRページから続行し全ページ揃う。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    state_path = tmp_path / "state.json"

    _stub_capture_writes_raw(monkeypatch, n_pages=4)

    # 1回目: 2ページOCRした時点で強制Kill。
    # ocr_all は OCR失敗ページを except Exception でスキップ継続する設計なので、
    # 実際のKill(Ctrl-C/SIGINT)相当の KeyboardInterrupt(BaseException) で伝播させる。
    calls = {"n": 0}

    def killing_ocr_page(path, cfg):  # noqa: ANN001
        if calls["n"] >= 2:
            raise KeyboardInterrupt("疑似Kill: OCR中にプロセス停止")
        calls["n"] += 1
        idx = int(Path(path).stem.split("_")[-1]) - 1
        x = 80 / IMG_W
        w = (320 - 80) / IMG_W
        y = (IMG_H - 120) / IMG_H
        h = (120 - 60) / IMG_H
        return [(WORDS[idx % len(WORDS)], 0.99, [x, y, w, h])]

    monkeypatch.setattr(ocr_mod, "ocr_page", killing_ocr_page)

    with pytest.raises(KeyboardInterrupt):
        pipeline.run(cfg, state_path)

    # capture→preprocess は完走、ocr で中断。ocr/ には2ページ分だけ JSON がある
    wd = pipeline.work_dir(cfg)
    st = State.load(state_path)
    assert st.stage == "ocr"
    done_jsons = list((wd / "ocr").glob("page_*.json"))
    assert len(done_jsons) == 2

    # 2回目: 正常な ocr_page で再開 → 未OCRページのみ処理して build まで完走
    _stub_ocr_page_by_page(monkeypatch)
    pipeline.run(cfg, state_path)

    out = pipeline.output_path(cfg, wd)
    texts = _searchable_texts(out)
    assert len(texts) == 4
    for i, text in enumerate(texts):
        assert WORDS[i] in text
    assert State.load(state_path).stage == "done"


def test_build_stage_raises_when_no_pages(tmp_path, monkeypatch):
    """確定ページ0枚なら build_stage は例外を送出しPDFを作らない（黙って成功しない）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    wd = pipeline.work_dir(cfg)
    (wd / "pages").mkdir(parents=True)
    (wd / "ocr").mkdir(parents=True)

    with pytest.raises(RuntimeError):
        pipeline.build_stage(cfg, wd)

    assert not pipeline.output_path(cfg, wd).exists()


def test_run_does_not_reach_done_when_build_has_no_pages(tmp_path, monkeypatch):
    """pages/ が空のまま build 段に入ると run は例外で止まり done に進まない。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    state_path = tmp_path / "state.json"
    State(book_title=cfg.book_title, stage="build").save(state_path)

    with pytest.raises(RuntimeError):
        pipeline.run(cfg, state_path)

    # 例外で停止 → stage は build のまま（再開余地を残す）、PDF も未生成
    assert State.load(state_path).stage == "build"
    assert not pipeline.output_path(cfg, pipeline.work_dir(cfg)).exists()


def test_run_is_noop_when_done(tmp_path, monkeypatch):
    """stage=='done' の再実行は何も走らせない（冪等）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    state_path = tmp_path / "state.json"
    State(book_title=cfg.book_title, stage="done").save(state_path)

    def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("done なのに段が実行された")

    monkeypatch.setattr(pipeline.capture, "run_capture", _boom)
    monkeypatch.setattr(pipeline.preprocess, "process_all", _boom)
    monkeypatch.setattr(pipeline.ocr, "ocr_all", _boom)

    pipeline.run(cfg, state_path)  # 例外なく戻れば OK
    assert State.load(state_path).stage == "done"
