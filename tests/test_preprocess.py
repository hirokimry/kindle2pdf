"""preprocess 段（P4）の単体テスト（macOS依存なし・合成画像で検証）。

見開きの左右分割は廃止した（Issue #29）。撮影された各画像がそのまま 1 ページになる
（1 撮影 = 1 ページ）。トリミングで UI・柱・余白を除去し、UI無しの確定ページを
pages/ に書き出す挙動を検証する。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from kindle2pdf import naming, preprocess
from kindle2pdf.config import Config, PreprocessConfig
from kindle2pdf.state import State


def _make_page(path: Path, size=(200, 100), color=(180, 180, 180)) -> Path:
    """撮影1枚相当の明るい合成画像を作る。"""
    im = Image.new("RGB", size, color)
    im.save(path)
    return path


def _setup_raw(tmp_path: Path, book: str, n: int, **make_kw) -> Path:
    """work/<book>/raw/ に n 枚の合成撮影画像を配置する。"""
    raw_dir = tmp_path / "work" / book / "raw"
    raw_dir.mkdir(parents=True)
    for i in range(1, n + 1):
        _make_page(raw_dir / naming.page_filename(i), **make_kw)
    return raw_dir


def _cfg(book: str, **preprocess_kw) -> Config:
    return Config(book_title=book, preprocess=PreprocessConfig(**preprocess_kw))


def test_one_capture_becomes_one_page(tmp_path, monkeypatch):
    """撮影N枚 → Nページになる（1 撮影 = 1 ページ・分割しない）。"""
    monkeypatch.chdir(tmp_path)
    book = "sample"
    _setup_raw(tmp_path, book, n=3)

    cfg = _cfg(book)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    assert len(pages) == 3  # 撮影3枚 → 3ページ（分割しない）
    assert state.pages_total == 3
    # 連番が page_000001..page_000003 で欠けなく揃う
    assert [p.name for p in pages] == [naming.page_filename(i) for i in range(1, 4)]


def test_pages_keep_full_width_and_are_trimmed(tmp_path, monkeypatch):
    """出力ページは元幅のまま（分割しない）かつトリミングでUI域が削られる。"""
    monkeypatch.chdir(tmp_path)
    book = "trim"
    raw_w, raw_h = 200, 100
    _setup_raw(tmp_path, book, n=1, size=(raw_w, raw_h))

    trim_ratios = {"top": 0.1, "bottom": 0.1, "left": 0.0, "right": 0.0}
    cfg = _cfg(book, trim=trim_ratios)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    assert len(pages) == 1
    with Image.open(pages[0]) as im:
        w, h = im.size
    # 分割しないので幅は元のまま
    assert w == raw_w
    # 上下10%ずつトリミングされ高さが縮む
    assert h == int(raw_h * 0.8)


def test_trim_disabled_by_empty_ratios(tmp_path, monkeypatch):
    """trim: {} でトリミング無効化（元画像と同一サイズ）。"""
    monkeypatch.chdir(tmp_path)
    book = "notrim"
    raw_w, raw_h = 200, 100
    _setup_raw(tmp_path, book, n=1, size=(raw_w, raw_h))

    cfg = _cfg(book, trim={})
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    with Image.open(pages[0]) as im:
        w, h = im.size
    assert w == raw_w  # トリミングされず元の幅
    assert h == raw_h  # トリミングされず元の高さ


def test_black_frame_is_excluded(tmp_path, monkeypatch):
    """min_brightness 未満の黒画面異常フレームは除外される。"""
    monkeypatch.chdir(tmp_path)
    book = "black"
    raw_dir = _setup_raw(tmp_path, book, n=2)  # 明るい正常2枚
    # 黒画面異常フレームを1枚追加（連番の末尾）
    Image.new("RGB", (200, 100), (0, 0, 0)).save(raw_dir / "page_0003.png")

    cfg = _cfg(book, min_brightness=20)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    # 正常2枚のみページ化 → 2ページ。黒画面1枚は除外
    assert len(pages) == 2
    assert state.pages_total == 2


def test_empty_raw_dir_yields_zero_pages(tmp_path, monkeypatch):
    """raw/ が空でもエラーにならず0ページ確定になる。"""
    monkeypatch.chdir(tmp_path)
    book = "empty"
    (tmp_path / "work" / book / "raw").mkdir(parents=True)

    cfg = _cfg(book)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = list((tmp_path / "work" / book / "pages").glob("*.png"))
    assert pages == []
    assert state.pages_total == 0


def test_config_change_clears_stale_pages(tmp_path, monkeypatch):
    """config 変更後の再実行で古い page_*.png が残留しない（冪等クリア）。"""
    monkeypatch.chdir(tmp_path)
    book = "restale"
    _setup_raw(tmp_path, book, n=3, size=(200, 100))
    pages_dir = tmp_path / "work" / book / "pages"

    # 1回目: トリミング無効で 3 ページ生成（元サイズ）
    state = State(book_title=book)
    preprocess.process_all(_cfg(book, trim={}), state)
    assert sorted(p.name for p in pages_dir.glob("*.png")) == [
        naming.page_filename(i) for i in range(1, 4)
    ]
    with Image.open(pages_dir / naming.page_filename(1)) as im:
        assert im.size == (200, 100)

    # 2回目: 同じ state で trim を変更 → 署名不一致で全再生成（高さが縮む）
    preprocess.process_all(_cfg(book, trim={"top": 0.2, "bottom": 0.0}), state)
    remaining = sorted(p.name for p in pages_dir.glob("*.png"))
    assert remaining == [naming.page_filename(i) for i in range(1, 4)]
    assert state.pages_total == 3
    with Image.open(pages_dir / naming.page_filename(1)) as im:
        assert im.size == (200, 80)  # top 20% トリミング後


def test_signature_changes_with_trim(tmp_path):
    """入力署名が trim 設定の変更で変わる（設定変更→pages/ 全再生成の担保）。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for i in range(1, 3):
        _make_page(raw_dir / naming.page_filename(i))
    raw_paths = sorted(raw_dir.glob("*.png"))

    sig_a = preprocess._input_signature(PreprocessConfig(trim={"top": 0.1}), raw_paths)
    sig_b = preprocess._input_signature(PreprocessConfig(trim={"top": 0.2}), raw_paths)
    assert sig_a != sig_b


def test_resume_skips_already_processed_raw(tmp_path, monkeypatch):
    """途中まで消化済みの state から再開すると残りの raw だけを処理する（F-8 レジューム）。"""
    monkeypatch.chdir(tmp_path)
    book = "resume"
    raw_dir = _setup_raw(tmp_path, book, n=3)
    pages_dir = tmp_path / "work" / book / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pcfg = PreprocessConfig()
    raw_paths = sorted(raw_dir.glob("*.png"))

    # 中断状態を再現: 先頭2枚を消化済み（2ページ書き出し済み）とする state を用意
    for i in range(1, 3):
        (pages_dir / naming.page_filename(i)).write_bytes(b"stub")
    state = State(
        book_title=book,
        preprocess_sig=preprocess._input_signature(pcfg, raw_paths),
        preprocess_raw_done=2,
        pages_total=2,
    )
    stub_mtime = (pages_dir / naming.page_filename(1)).stat().st_mtime

    cfg = _cfg(book)
    preprocess.process_all(cfg, state)

    pages = sorted(p.name for p in pages_dir.glob("*.png"))
    # 残り1枚（3枚目）がページ化され page_000003 が追加されて計3ページ
    assert pages == [naming.page_filename(i) for i in range(1, 4)]
    assert state.pages_total == 3
    assert state.preprocess_raw_done == 3
    # 消化済みの page_000001 は再処理されず stub のまま（レジュームでスキップ）
    assert (pages_dir / naming.page_filename(1)).stat().st_mtime == stub_mtime
    assert (pages_dir / naming.page_filename(1)).read_bytes() == b"stub"
