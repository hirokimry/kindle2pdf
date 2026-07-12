"""pipeline の4段結線とレジューム（P7・F-8）、run ディレクトリ化（#31）の回帰テスト。

capture 段（Kindle操作）と ocr_page（Apple Vision, macOS専用）だけを monkeypatch で
差し替え、preprocess / build は実物（Pillow + reportlab）で通す。これにより
CI(Linux) でも「4段が順に通り検索可能PDFが出る」「途中Killから再開できる」を検証する。

検証対象:
- 4段フル実行で <run_dir>/output/<book_title>.pdf が生成され全ページ検索ヒットする
- 撮影ごとに work/<book>/<日時>/ の専用ディレクトリが切られ、2回目も上書きしない（#31）
- 未完了 run は継続、完了済み/無しなら新規ディレクトリを作る（#31）
- state が run ディレクトリ内に置かれ、カレントに state.json を残さない（#31）
- build 段のみからの再開（既存 pages/ + ocr/ を束ねる結線）
- OCR JSON が無いページは画像のみでPDF化する（仕様 4.3・クラッシュしない）
- ocr 段の途中Kill→再実行で未OCRページから続行し全ページ揃う
- stage=="done" 再実行は no-op（冪等）
"""

from __future__ import annotations

from datetime import datetime
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

# run ディレクトリ名を決める固定時刻（テストの決定性のため注入する）。
T0 = datetime(2026, 7, 12, 14, 30, 0)
T1 = datetime(2026, 7, 12, 14, 31, 0)


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
    """4段フル実行で <run_dir>/output/<book_title>.pdf が出て全ページ検索ヒットする。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    _stub_capture_writes_raw(monkeypatch, n_pages=3)
    _stub_ocr_page_by_page(monkeypatch)

    run_dir = pipeline.run(cfg, now=T0)

    # run ディレクトリは work/test-book/<日時>/ 配下に作られる
    assert run_dir.parent == pipeline.book_dir(cfg)
    out = pipeline.output_path(cfg, run_dir)
    assert out == run_dir / "output" / "test-book.pdf"
    assert out.exists()

    texts = _searchable_texts(out)
    assert len(texts) == 3
    for i, text in enumerate(texts):
        assert WORDS[i] in text, f"ページ{i+1}に {WORDS[i]} が検索ヒットしない"

    # 全段完了して done に到達している（state は run ディレクトリ内）
    assert State.load(run_dir / "state.json").stage == "done"


def test_run_emits_progress_events(tmp_path, monkeypatch):
    """run が各段・各ページ・完了の進捗イベントを発火する（#32）。

    シンクを差し込んだときだけイベントが観測でき、既定 no-op では従来挙動のまま。
    """
    from kindle2pdf import progress as progress_mod

    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    _stub_capture_writes_raw(monkeypatch, n_pages=2)
    _stub_ocr_page_by_page(monkeypatch)

    events: list[dict] = []
    progress_mod.set_sink(events.append)
    try:
        pipeline.run(cfg, now=T0)
    finally:
        progress_mod.set_sink(None)

    kinds = [e["event"] for e in events]
    # 4 段すべての開始・完了と最終完了イベントが出る。
    for stage in ("capture", "preprocess", "ocr", "build"):
        assert {"event": "stage_start", "stage": stage} in events
        assert {"event": "stage_complete", "stage": stage} in events
    assert kinds[-1] == "complete"
    assert events[-1]["output"].endswith(f"{cfg.book_title}.pdf")
    # ページ単位イベントが段名・ページ番号・総数を含む（preprocess/ocr は総数既知）。
    ocr_pages = [e for e in events if e["event"] == "page" and e["stage"] == "ocr"]
    assert ocr_pages[-1] == {"event": "page", "stage": "ocr", "page": 2, "total": 2}


def test_run_emits_error_event_on_stage_failure(tmp_path, monkeypatch):
    """段の失敗時に error イベント（段名・メッセージ）を発火してから送出する（#32）。"""
    from kindle2pdf import progress as progress_mod

    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)

    def boom(cfg, state, work_dir, state_path):
        raise RuntimeError("Kindle ウィンドウが見つかりません")

    monkeypatch.setattr(pipeline.capture, "run_capture", boom)

    events: list[dict] = []
    progress_mod.set_sink(events.append)
    try:
        with pytest.raises(RuntimeError):
            pipeline.run(cfg, now=T0)
    finally:
        progress_mod.set_sink(None)

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["stage"] == "capture"
    assert "Kindle ウィンドウ" in error_events[0]["message"]


def test_two_runs_create_separate_dirs(tmp_path, monkeypatch):
    """2回連続で実行しても互いに上書きせず別ディレクトリになる（破壊的削除が不要・#31）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    _stub_capture_writes_raw(monkeypatch, n_pages=2)
    _stub_ocr_page_by_page(monkeypatch)

    run_dir1 = pipeline.run(cfg, now=T0)
    # 1回目は完了済み → 2回目は継続されず新規ディレクトリが切られる
    run_dir2 = pipeline.run(cfg, now=T1)

    assert run_dir1 != run_dir2
    assert pipeline.output_path(cfg, run_dir1).exists()
    assert pipeline.output_path(cfg, run_dir2).exists()
    # 両 run が残り、どちらも上書きされていない
    assert len(pipeline._run_dirs(pipeline.book_dir(cfg))) == 2


def test_state_lives_inside_run_dir(tmp_path, monkeypatch):
    """state が run ディレクトリ内に置かれ、カレントに state.json を残さない（#31）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    _stub_capture_writes_raw(monkeypatch, n_pages=1)
    _stub_ocr_page_by_page(monkeypatch)

    run_dir = pipeline.run(cfg, now=T0)

    assert (run_dir / "state.json").exists()
    assert not (tmp_path / "state.json").exists()


def test_resolve_run_dir_resumes_incomplete(tmp_path, monkeypatch):
    """未完了 run があればそれを継続対象として返す（完了済みは無視・#31）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    bdir = pipeline.book_dir(cfg)
    done = bdir / "2026-07-12_100000"
    done.mkdir(parents=True)
    State(book_title=cfg.book_title, stage="done").save(done / "state.json")
    inprog = bdir / "2026-07-12_110000"   # done より新しい未完了 run
    inprog.mkdir(parents=True)
    State(book_title=cfg.book_title, stage="ocr").save(inprog / "state.json")

    assert pipeline.resolve_run_dir(cfg) == inprog


def test_resolve_run_dir_new_when_all_complete(tmp_path, monkeypatch):
    """全 run が完了済みなら日時付きの新規ディレクトリを作る（#31）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    bdir = pipeline.book_dir(cfg)
    done = bdir / "2026-07-12_100000"
    done.mkdir(parents=True)
    State(book_title=cfg.book_title, stage="done").save(done / "state.json")

    resolved = pipeline.resolve_run_dir(cfg, now=T0)

    assert resolved == bdir / "2026-07-12_143000"
    assert resolved.is_dir()


def test_resolve_run_dir_suffixes_on_same_timestamp_collision(tmp_path, monkeypatch):
    """同一秒に衝突したら -02 のゼロ埋め連番で退避する（辞書順=時刻順を保つ・#31）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)

    first = pipeline.resolve_run_dir(cfg, resume=False, now=T0)
    # 同じ now で 2 回目 → 既存ディレクトリと衝突し -02 サフィックスで退避する。
    second = pipeline.resolve_run_dir(cfg, resume=False, now=T0)

    assert second == first.parent / f"{first.name}-02"
    assert first.is_dir() and second.is_dir()


def test_resolve_run_dir_resume_false_forces_new(tmp_path, monkeypatch):
    """resume=False は未完了 run があっても新規ディレクトリを作る（#31）。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    bdir = pipeline.book_dir(cfg)
    inprog = bdir / "2026-07-12_110000"
    inprog.mkdir(parents=True)
    State(book_title=cfg.book_title, stage="ocr").save(inprog / "state.json")

    resolved = pipeline.resolve_run_dir(cfg, resume=False, now=T0)

    assert resolved != inprog
    assert resolved == bdir / "2026-07-12_143000"


def test_failed_capture_leaves_resumable_run_dir(tmp_path, monkeypatch):
    """capture が state 保存前に落ちても run ディレクトリは再開対象として残る（#31）。

    Kindle 未起動などで capture が最初の state.save 前に RuntimeError を投げても、
    初期 state.json が即時永続化されているため、次回実行は同一ディレクトリを再開し、
    空ディレクトリが積み上がらない（破壊的 rm 撤廃の狙いを守る）。
    """
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)

    def boom(cfg, state, work_dir, state_path):  # noqa: ANN001
        # state.save 到達前にウィンドウ検出が失敗する初回撮影を模す。
        raise RuntimeError("Kindle ウィンドウが見つかりません")

    monkeypatch.setattr(pipeline.capture, "run_capture", boom)

    with pytest.raises(RuntimeError):
        pipeline.run(cfg, now=T0)

    bdir = pipeline.book_dir(cfg)
    first_dirs = pipeline._run_dirs(bdir)
    assert len(first_dirs) == 1
    assert (first_dirs[0] / "state.json").exists()  # 初期 state が残っている

    # 2回目（T1）も同じ失敗。resume で同一ディレクトリを再開し新規を作らない。
    with pytest.raises(RuntimeError):
        pipeline.run(cfg, now=T1)

    assert pipeline._run_dirs(bdir) == first_dirs  # ディレクトリが増えていない


def test_resume_from_build_stage_only(tmp_path, monkeypatch):
    """既存 pages/ + ocr/ から build 段だけ再開して PDF を生成できる。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    run_dir = pipeline.book_dir(cfg) / "2026-07-12_143000"
    pages_dir = run_dir / "pages"
    ocr_dir = run_dir / "ocr"
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

    State(book_title=cfg.book_title, stage="build").save(run_dir / "state.json")

    pipeline.run(cfg, run_dir=run_dir)

    out = pipeline.output_path(cfg, run_dir)
    texts = _searchable_texts(out)
    assert len(texts) == 2
    assert WORDS[0] in texts[0]
    assert WORDS[1] in texts[1]
    assert State.load(run_dir / "state.json").stage == "done"


def test_build_renders_image_only_page_when_ocr_missing(tmp_path, monkeypatch):
    """OCR JSON が無いページは画像のみでPDF化する（仕様4.3・クラッシュしない）。"""
    cfg = _single_page_config(tmp_path)
    run_dir = tmp_path / "run"
    pages_dir = run_dir / "pages"
    ocr_dir = run_dir / "ocr"
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

    out = pipeline.build_stage(cfg, run_dir)

    texts = _searchable_texts(out)
    assert len(texts) == 2               # 2ページとも描画される
    assert WORDS[0] in texts[0]          # OCR済みページは検索ヒット
    assert texts[1].strip() == ""        # OCR無しページはテキスト層なし


def test_resume_after_kill_in_ocr_stage(tmp_path, monkeypatch):
    """ocr 段の途中Kill→再実行で未OCRページから続行し全ページ揃う。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)

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
        pipeline.run(cfg, now=T0)

    # capture→preprocess は完走、ocr で中断。ocr/ には2ページ分だけ JSON がある
    run_dir = pipeline._run_dirs(pipeline.book_dir(cfg))[0]
    st = State.load(run_dir / "state.json")
    assert st.stage == "ocr"
    done_jsons = list((run_dir / "ocr").glob("page_*.json"))
    assert len(done_jsons) == 2

    # 2回目: 正常な ocr_page で再開 → 未完了 run を継続し build まで完走（新規 dir を作らない）
    _stub_ocr_page_by_page(monkeypatch)
    run_dir2 = pipeline.run(cfg)

    assert run_dir2 == run_dir
    assert len(pipeline._run_dirs(pipeline.book_dir(cfg))) == 1
    out = pipeline.output_path(cfg, run_dir)
    texts = _searchable_texts(out)
    assert len(texts) == 4
    for i, text in enumerate(texts):
        assert WORDS[i] in text
    assert State.load(run_dir / "state.json").stage == "done"


def test_build_stage_raises_when_no_pages(tmp_path, monkeypatch):
    """確定ページ0枚なら build_stage は例外を送出しPDFを作らない（黙って成功しない）。"""
    cfg = _single_page_config(tmp_path)
    run_dir = tmp_path / "run"
    (run_dir / "pages").mkdir(parents=True)
    (run_dir / "ocr").mkdir(parents=True)

    with pytest.raises(RuntimeError):
        pipeline.build_stage(cfg, run_dir)

    assert not pipeline.output_path(cfg, run_dir).exists()


def test_run_does_not_reach_done_when_build_has_no_pages(tmp_path, monkeypatch):
    """pages/ が空のまま build 段に入ると run は例外で止まり done に進まない。"""
    monkeypatch.chdir(tmp_path)
    cfg = _single_page_config(tmp_path)
    run_dir = pipeline.book_dir(cfg) / "2026-07-12_143000"
    run_dir.mkdir(parents=True)
    State(book_title=cfg.book_title, stage="build").save(run_dir / "state.json")

    with pytest.raises(RuntimeError):
        pipeline.run(cfg, run_dir=run_dir)

    # 例外で停止 → stage は build のまま（再開余地を残す）、PDF も未生成
    assert State.load(run_dir / "state.json").stage == "build"
    assert not pipeline.output_path(cfg, run_dir).exists()


def test_run_is_noop_when_done(tmp_path, monkeypatch):
    """stage=='done' の再実行は何も走らせない（冪等）。"""
    cfg = _single_page_config(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    State(book_title=cfg.book_title, stage="done").save(run_dir / "state.json")

    def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("done なのに段が実行された")

    monkeypatch.setattr(pipeline.capture, "run_capture", _boom)
    monkeypatch.setattr(pipeline.preprocess, "process_all", _boom)
    monkeypatch.setattr(pipeline.ocr, "ocr_all", _boom)

    result = pipeline.run(cfg, run_dir=run_dir)  # 例外なく戻れば OK
    assert result == run_dir
    assert State.load(run_dir / "state.json").stage == "done"
