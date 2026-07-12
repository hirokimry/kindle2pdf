"""ocr_all のバッチ処理・レジューム・失敗継続の単体テスト。

ocrmac は macOS 専用 extra。CI(ubuntu) でも通るよう、実 OCR を行う ocr_page を
monkeypatch で差し替え、ocrmac の import を一切発生させずに検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from kindle2pdf import ocr
from kindle2pdf.config import Config
from kindle2pdf.state import State


def _make_pages(work_dir: Path, names: list[str]) -> Path:
    """work_dir/pages に小さなダミー画像を作り pages ディレクトリを返す。"""
    pages_dir = work_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (8, 8), (255, 255, 255)).save(pages_dir / name)
    return pages_dir


def _fake_items(page_path) -> list[ocr.OcrItem]:
    """(text, confidence, [x, y, w, h]) 形式・座標は正規化(0..1)のダミー結果。"""
    return [("見出し", 0.99, [0.1, 0.8, 0.5, 0.05])]


def test_module_imports_without_ocrmac():
    """非mac環境でも ocr モジュールの import 自体は成功する（遅延importの担保）。"""
    import importlib

    importlib.import_module("kindle2pdf.ocr")


# --- 見開き2カラムの読み順（order_reading_items）---------------------------------
# bbox=[x, y, w, h] 正規化(0..1)・原点左下（y 大 = 上）。見開き分割廃止（Issue #29）で
# 1 ページが 2 カラムになり得るため、列認識で読み順が破綻しないことを検証する。


def _texts(items: list[ocr.OcrItem]) -> list[str]:
    return [t for t, _c, _b in items]


# わざと交互（左下→右上→左上→右下）に並べ、ソートで読み順が正されることを示す入力
_SPREAD_SCRAMBLED: list[ocr.OcrItem] = [
    ("左下", 0.9, [0.1, 0.3, 0.3, 0.05]),
    ("右上", 0.9, [0.6, 0.8, 0.3, 0.05]),
    ("左上", 0.9, [0.1, 0.8, 0.3, 0.05]),
    ("右下", 0.9, [0.6, 0.3, 0.3, 0.05]),
]


def test_spread_rtl_reads_right_column_first():
    """2カラム見開きは rtl で 右列(上→下)→左列(上→下) の読み順になる（漫画・縦書き）。"""
    ordered = ocr.order_reading_items(_SPREAD_SCRAMBLED, "rtl")
    assert _texts(ordered) == ["右上", "右下", "左上", "左下"]


def test_spread_ltr_reads_left_column_first():
    """2カラム見開きは ltr で 左列(上→下)→右列(上→下) の読み順になる（横書き）。"""
    ordered = ocr.order_reading_items(_SPREAD_SCRAMBLED, "ltr")
    assert _texts(ordered) == ["左上", "左下", "右上", "右下"]


def test_single_column_full_width_lines_keep_top_to_bottom():
    """中心線をまたぐ全幅行がある片ページは左右に割らず 上→下ソートのみになる。

    全幅行を誤って左右カラムに割ると読み順が壊れるため、単一カラム退避を検証する。
    """
    scrambled: list[ocr.OcrItem] = [
        ("2行目", 0.9, [0.1, 0.5, 0.8, 0.05]),
        ("3行目", 0.9, [0.1, 0.2, 0.8, 0.05]),
        ("1行目", 0.9, [0.1, 0.8, 0.8, 0.05]),  # 0.1..0.9 が中心 0.5 をまたぐ
    ]
    # rtl でも「右列→左列」に割らず上→下のまま（全幅行があるので単一カラム扱い）
    ordered = ocr.order_reading_items(scrambled, "rtl")
    assert _texts(ordered) == ["1行目", "2行目", "3行目"]


def test_single_item_is_returned_as_is():
    """要素が1つ以下ならそのまま返す（並べ替え不要）。"""
    one: list[ocr.OcrItem] = [("only", 0.9, [0.1, 0.5, 0.2, 0.05])]
    assert ocr.order_reading_items(one, "rtl") == one
    assert ocr.order_reading_items([], "rtl") == []


def test_ocr_all_writes_json_per_page(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_page", lambda path, cfg: _fake_items(path))
    _make_pages(tmp_path, ["page_0001.png", "page_0002.png"])
    state = State()

    ocr.ocr_all(Config(), state, work_dir=tmp_path)

    ocr_dir = tmp_path / "ocr"
    assert (ocr_dir / "page_0001.json").exists()
    assert (ocr_dir / "page_0002.json").exists()
    assert state.ocr_done == 2

    data = json.loads((ocr_dir / "page_0001.json").read_text(encoding="utf-8"))
    assert data["page"] == "page_0001"
    assert data["items"][0]["text"] == "見出し"
    assert data["items"][0]["confidence"] == 0.99
    assert data["items"][0]["bbox"] == [0.1, 0.8, 0.5, 0.05]


def test_ocr_all_resumes_from_unprocessed_pages(tmp_path, monkeypatch):
    """既に ocr/<stem>.json があるページは再OCRせずスキップする。"""
    _make_pages(tmp_path, ["page_0001.png", "page_0002.png"])
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    # page_0001 は処理済みとして既存 JSON を置く
    (ocr_dir / "page_0001.json").write_text(
        json.dumps({"page": "page_0001", "source": "x", "items": []}),
        encoding="utf-8",
    )

    called: list[str] = []

    def _spy(path, cfg):
        called.append(Path(path).name)
        return _fake_items(path)

    monkeypatch.setattr(ocr, "ocr_page", _spy)
    state = State()

    ocr.ocr_all(Config(), state, work_dir=tmp_path)

    # 未処理の page_0002 のみ OCR される
    assert called == ["page_0002.png"]
    assert state.ocr_done == 2


def test_ocr_all_continues_on_failure(tmp_path, monkeypatch):
    """1ページの OCR 失敗で全体を止めず、失敗ページは JSON を残さない。"""
    _make_pages(tmp_path, ["page_0001.png", "page_0002.png"])

    def _flaky(path, cfg):
        if Path(path).name == "page_0001.png":
            raise RuntimeError("Vision 認識に失敗")
        return _fake_items(path)

    monkeypatch.setattr(ocr, "ocr_page", _flaky)
    state = State()

    ocr.ocr_all(Config(), state, work_dir=tmp_path)

    ocr_dir = tmp_path / "ocr"
    # 失敗ページは JSON 未作成 → 次回レジュームで再試行される
    assert not (ocr_dir / "page_0001.json").exists()
    assert (ocr_dir / "page_0002.json").exists()
    # 成功ページのみ完了数に数える
    assert state.ocr_done == 1


def test_ocr_all_persists_state_incrementally(tmp_path, monkeypatch):
    """state_path 指定時はページ毎に state.json へ ocr_done を永続化する。"""
    monkeypatch.setattr(ocr, "ocr_page", lambda path, cfg: _fake_items(path))
    _make_pages(tmp_path, ["page_0001.png"])
    state_path = tmp_path / "state.json"
    state = State()

    ocr.ocr_all(Config(), state, work_dir=tmp_path, state_path=state_path)

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["ocr_done"] == 1


def test_ocr_all_missing_pages_dir(tmp_path, monkeypatch):
    """pages ディレクトリが無くても例外を出さず ocr_done=0 とする。"""
    monkeypatch.setattr(ocr, "ocr_page", lambda path, cfg: _fake_items(path))
    state = State()
    state.ocr_done = 99  # 前回値が残っていても 0 にリセットされる

    ocr.ocr_all(Config(), state, work_dir=tmp_path)

    assert state.ocr_done == 0


def test_ocr_all_defaults_work_dir_and_state(tmp_path, monkeypatch):
    """work_dir / state 省略時（pipeline の2引数呼び出し経路）の既定分岐を検証する。

    work_dir 未指定 → work/<book_title>、state 未指定 → 内部で State() を生成する。
    """
    monkeypatch.setattr(ocr, "ocr_page", lambda path, cfg: _fake_items(path))
    monkeypatch.chdir(tmp_path)  # 相対パス work/ を tmp_path 配下に閉じ込める
    cfg = Config()
    cfg.book_title = "my-book"
    # book_title から導出される work/my-book/pages にダミーページを置く
    pages_dir = tmp_path / "work" / "my-book" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (255, 255, 255)).save(pages_dir / "page_0001.png")

    # state も work_dir も渡さない（既定分岐）→ 例外なく完了する
    ocr.ocr_all(cfg)

    assert (tmp_path / "work" / "my-book" / "ocr" / "page_0001.json").exists()


def test_load_page_items_roundtrip(tmp_path, monkeypatch):
    """保存した JSON を load_page_items で OcrItem タプルへ復元できる。"""
    monkeypatch.setattr(ocr, "ocr_page", lambda path, cfg: _fake_items(path))
    _make_pages(tmp_path, ["page_0001.png"])
    state = State()

    ocr.ocr_all(Config(), state, work_dir=tmp_path)

    items = ocr.load_page_items(tmp_path / "ocr" / "page_0001.json")
    assert items == [("見出し", 0.99, [0.1, 0.8, 0.5, 0.05])]
    # build_pdf が期待する (text, conf, [x, y, w, h]) 形状
    text, conf, bbox = items[0]
    assert isinstance(bbox, list) and len(bbox) == 4
