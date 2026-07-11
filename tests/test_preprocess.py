"""preprocess 段（P4）の単体テスト（macOS依存なし・合成画像で検証）。

見開き画像を左右分割・トリミングし、単一カラム・UI無しの確定ページを
pages/ に書き出す挙動を、config 切替ごとに検証する。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from kindle2pdf import naming, preprocess
from kindle2pdf.config import Config, PreprocessConfig
from kindle2pdf.state import State


def _make_spread(path: Path, size=(200, 100), color=(180, 180, 180)) -> Path:
    """見開き相当の明るい合成画像を作る（左右に色差をつけて分割を判別可能にする）。"""
    im = Image.new("RGB", size, color)
    px = im.load()
    w, h = size
    for x in range(w):
        for y in range(h):
            # 左半分を明るく、右半分をやや暗くして左右カラムを区別できるようにする
            px[x, y] = (200, 200, 200) if x < w // 2 else (120, 120, 120)
    im.save(path)
    return path


def _setup_raw(tmp_path: Path, book: str, n: int, **make_kw) -> Path:
    """work/<book>/raw/ に n 枚の合成見開き画像を配置する。"""
    raw_dir = tmp_path / "work" / book / "raw"
    raw_dir.mkdir(parents=True)
    for i in range(1, n + 1):
        _make_spread(raw_dir / naming.page_filename(i), **make_kw)
    return raw_dir


def _cfg(book: str, *, spread_mode: bool = True, **preprocess_kw) -> Config:
    # 見開き/片ページの切替は capture.spread_mode（唯一のスイッチ）で決まる。
    cfg = Config(book_title=book, preprocess=PreprocessConfig(**preprocess_kw))
    cfg.capture.spread_mode = spread_mode
    return cfg


def test_split_spread_doubles_page_count(tmp_path, monkeypatch):
    """見開きN枚 → 2Nページになる（spread_mode=True 時）。"""
    monkeypatch.chdir(tmp_path)
    book = "sample"
    _setup_raw(tmp_path, book, n=3)

    cfg = _cfg(book, spread_mode=True)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    assert len(pages) == 6  # 見開き3枚 → 6ページ
    assert state.pages_total == 6
    # 連番が page_000001..page_000006 で欠けなく揃う
    assert [p.name for p in pages] == [naming.page_filename(i) for i in range(1, 7)]


def test_split_disabled_keeps_page_count(tmp_path, monkeypatch):
    """spread_mode=False なら分割せず N枚 → Nページ（config 切替の検証）。"""
    monkeypatch.chdir(tmp_path)
    book = "single"
    _setup_raw(tmp_path, book, n=4)

    cfg = _cfg(book, spread_mode=False)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    assert len(pages) == 4
    assert state.pages_total == 4


def test_pages_are_single_column_and_trimmed(tmp_path, monkeypatch):
    """出力ページは単一カラム（元幅の約半分）かつトリミングでUI域が削られる。"""
    monkeypatch.chdir(tmp_path)
    book = "trim"
    raw_w, raw_h = 200, 100
    _setup_raw(tmp_path, book, n=1, size=(raw_w, raw_h))

    trim_ratios = {"top": 0.1, "bottom": 0.1, "left": 0.0, "right": 0.0}
    cfg = _cfg(book, spread_mode=True, trim=trim_ratios)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    assert len(pages) == 2
    with Image.open(pages[0]) as im:
        w, h = im.size
    # 左右分割で幅は約半分（単一カラム）
    assert w == raw_w // 2
    # 上下10%ずつトリミングされ高さが縮む
    assert h == int(raw_h * 0.8)


def test_trim_disabled_by_empty_ratios(tmp_path, monkeypatch):
    """trim: {} でトリミング無効化（分割のみ、高さは元のまま）。"""
    monkeypatch.chdir(tmp_path)
    book = "notrim"
    raw_w, raw_h = 200, 100
    _setup_raw(tmp_path, book, n=1, size=(raw_w, raw_h))

    cfg = _cfg(book, spread_mode=True, trim={})
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    with Image.open(pages[0]) as im:
        w, h = im.size
    assert w == raw_w // 2
    assert h == raw_h  # トリミングされず元の高さ


def test_black_frame_is_excluded(tmp_path, monkeypatch):
    """min_brightness 未満の黒画面異常フレームは除外される。"""
    monkeypatch.chdir(tmp_path)
    book = "black"
    raw_dir = _setup_raw(tmp_path, book, n=2)  # 明るい正常見開き2枚
    # 黒画面異常フレームを1枚追加（連番の末尾）
    Image.new("RGB", (200, 100), (0, 0, 0)).save(raw_dir / "page_0003.png")

    cfg = _cfg(book, spread_mode=True, min_brightness=20)
    state = State(book_title=book)
    preprocess.process_all(cfg, state)

    pages = sorted((tmp_path / "work" / book / "pages").glob("*.png"))
    # 正常2枚のみ分割 → 4ページ。黒画面1枚は除外
    assert len(pages) == 4
    assert state.pages_total == 4


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
    _setup_raw(tmp_path, book, n=3)
    pages_dir = tmp_path / "work" / book / "pages"

    # 1回目: 見開き分割ありで 6 ページ生成
    state = State(book_title=book)
    preprocess.process_all(_cfg(book, spread_mode=True), state)
    assert sorted(p.name for p in pages_dir.glob("*.png")) == [
        naming.page_filename(i) for i in range(1, 7)
    ]

    # 2回目: 同じ state を使い spread_mode=False に変更 → 3 ページに再生成
    preprocess.process_all(_cfg(book, spread_mode=False), state)
    remaining = sorted(p.name for p in pages_dir.glob("*.png"))
    # 古い page_000004..page_000006 は残留せず、新しい 3 ページのみになる
    assert remaining == [naming.page_filename(i) for i in range(1, 4)]
    assert state.pages_total == 3


def test_signature_changes_with_spread_mode(tmp_path):
    """入力署名が spread_mode の切替で変わる（設定変更→pages/ 全再生成の担保）。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for i in range(1, 3):
        _make_spread(raw_dir / naming.page_filename(i))
    raw_paths = sorted(raw_dir.glob("*.png"))
    pcfg = PreprocessConfig()

    sig_spread = preprocess._input_signature(pcfg, True, raw_paths)
    sig_single = preprocess._input_signature(pcfg, False, raw_paths)
    assert sig_spread != sig_single


def test_spread_mode_toggle_regenerates_pages(tmp_path, monkeypatch):
    """spread_mode を切り替えると署名不一致で pages/ が全再生成される（見開き↔片ページ切替）。"""
    monkeypatch.chdir(tmp_path)
    book = "toggle"
    _setup_raw(tmp_path, book, n=2)
    pages_dir = tmp_path / "work" / book / "pages"
    state = State(book_title=book)

    # 見開き: 2枚 → 4ページ
    preprocess.process_all(_cfg(book, spread_mode=True), state)
    assert state.pages_total == 4

    # 片ページに切替: 同じ state でも署名が変わり 2枚 → 2ページに全再生成
    preprocess.process_all(_cfg(book, spread_mode=False), state)
    assert state.pages_total == 2
    assert sorted(p.name for p in pages_dir.glob("*.png")) == [
        naming.page_filename(i) for i in range(1, 3)
    ]


def test_resume_skips_already_processed_raw(tmp_path, monkeypatch):
    """途中まで消化済みの state から再開すると残りの raw だけを処理する（F-8 レジューム）。"""
    monkeypatch.chdir(tmp_path)
    book = "resume"
    raw_dir = _setup_raw(tmp_path, book, n=3)
    pages_dir = tmp_path / "work" / book / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pcfg = PreprocessConfig()
    raw_paths = sorted(raw_dir.glob("*.png"))

    # 中断状態を再現: 先頭2枚を消化済み（4ページ書き出し済み）とする state を用意
    for i in range(1, 5):
        (pages_dir / naming.page_filename(i)).write_bytes(b"stub")
    state = State(
        book_title=book,
        # 署名は spread_mode=True 前提で生成（下の _cfg も spread_mode=True）
        preprocess_sig=preprocess._input_signature(pcfg, True, raw_paths),
        preprocess_raw_done=2,
        pages_total=4,
    )
    stub_mtime = (pages_dir / naming.page_filename(1)).stat().st_mtime

    cfg = _cfg(book, spread_mode=True)
    preprocess.process_all(cfg, state)

    pages = sorted(p.name for p in pages_dir.glob("*.png"))
    # 残り1枚（3枚目）が分割され page_000005/page_000006 が追加されて計6ページ
    assert pages == [naming.page_filename(i) for i in range(1, 7)]
    assert state.pages_total == 6
    assert state.preprocess_raw_done == 3
    # 消化済みの page_000001 は再処理されず stub のまま（レジュームでスキップ）
    assert (pages_dir / naming.page_filename(1)).stat().st_mtime == stub_mtime
    assert (pages_dir / naming.page_filename(1)).read_bytes() == b"stub"
