"""ocr 段 — Apple Vision（既定）/ Google Cloud Vision（オプション）OCR とバッチ処理。

PoC根拠: Visionは余分な空白がほぼ無く（0.01/字）、日本語の語句検索が壊れない。

engine で backend を切り替える（Issue #56）:
- apple: Apple Vision（ocrmac）。端末内完結・無料・macOS 専用。既定。
- google: Google Cloud Vision REST。クラウド送信・従量課金だが、手描き/崩し字/極小文字に強い。
  鍵は環境変数 GOOGLE_VISION_API_KEY から取得する（コード・設定にはコミットしない）。

見開きの左右分割を廃止した（Issue #29）ため、1 ページが 2 カラム（見開き）になり得る。
どちらの engine も生の返り順は読み順が破綻し得るので、`order_reading_items()` で列認識して
から読み順に並べ替える（engine 非依存で dispatch 側が一度だけ適用する）。

ocrmac は macOS 専用（extra: macos）。import は関数内で遅延させ、
非mac環境（CI ubuntu 等）でモジュール import 自体は失敗しないようにする。google backend は
標準ライブラリ（urllib）のみで動くため、Apple Vision が使えない非 mac 環境の逃げ道にもなる。

実装チケット: P5(Vision OCR) / Issue #56(Google backend)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import progress
from .config import Config
from .state import State

logger = logging.getLogger(__name__)

# 返り値要素: (text, confidence, [x, y, w, h])  座標は正規化(0..1)・原点左下
OcrItem = tuple[str, float, list[float]]

# Google Cloud Vision REST。鍵は X-Goog-Api-Key ヘッダで渡す（URL に乗せないことで
# http.client のデバッグログやプロキシ経由での鍵漏洩リスクを避ける）。
GOOGLE_VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
GOOGLE_API_KEY_ENV = "GOOGLE_VISION_API_KEY"
# 1 ページの HTTP タイムアウト（秒）。クラウド OCR の応答遅延を見込みつつ無限待ちを防ぐ。
_GOOGLE_TIMEOUT_SEC = 60

# pages/ から拾う画像拡張子（preprocess の出力形式に追従）
PAGE_IMAGE_EXTS = (".png", ".jpg", ".jpeg")

# 進捗ログを出す間隔（ページ数）。数百ページでも冗長すぎず追跡できる粒度。
_PROGRESS_EVERY = 25


def order_reading_items(items: list[OcrItem], reading_order: str = "rtl") -> list[OcrItem]:
    """OCR結果を読み順に並べ替える（見開き2カラムでも読み順が破綻しないように）。

    座標は正規化(0..1)・原点左下（bbox=[x, y, w, h]）。

    Why: 見開き分割廃止（Issue #29）で 1 ページが 2 カラムになり得る。中央に縦の谷間
    （どの bbox も中心線 x=0.5 をまたがない）があれば 2 カラムと判定し、左右の列に分けて
    各列を上→下に並べ、列同士を reading_order の向きで連結する。中心線をまたぐ全幅行が
    1 つでもあれば片ページ（単一カラム）とみなし、全体を上→下ソートするだけにする
    （片ページを誤って左右に割ると読み順が壊れるため）。

    reading_order: rtl=右列→左列（漫画・縦書き見開き） / ltr=左列→右列（横書き）。
    """
    if len(items) <= 1:
        return list(items)

    def _top_key(it: OcrItem) -> tuple[float, float]:
        # 上から下（原点左下なので y 大 = 上）。同 y は左優先で安定化する。
        _text, _conf, (x, y, _w, _h) = it
        return (-y, x)

    # どれかの bbox が中心線をまたぐ＝全幅行あり＝単一カラムとみなす
    spans_center = any(
        bbox[0] < 0.5 < bbox[0] + bbox[2] for _text, _conf, bbox in items
    )
    if spans_center:
        return sorted(items, key=_top_key)

    left = sorted(
        (it for it in items if it[2][0] + it[2][2] / 2 < 0.5), key=_top_key
    )
    right = sorted(
        (it for it in items if it[2][0] + it[2][2] / 2 >= 0.5), key=_top_key
    )
    if not left or not right:
        # 実質1カラム（全アイテムが片側）なら向きは無関係、上→下のみ
        return sorted(items, key=_top_key)
    return right + left if reading_order == "rtl" else left + right


def ocr_page(path: str | Path, cfg: Config) -> list[OcrItem]:
    """1ページを OCR して (text, confidence, bbox) のリストを読み順で返す。

    engine で backend を選ぶ。読み順の並べ替え（`order_reading_items`）は engine 非依存なので
    ここで一度だけ適用し、各 backend は生の items（正規化 bbox・原点左下）を返す責務に絞る。
    """
    if cfg.ocr.engine == "google":
        items = _ocr_page_google(path)
    else:
        items = _ocr_page_apple(path, cfg)
    return order_reading_items(items, cfg.ocr.reading_order)


def _ocr_page_apple(path: str | Path, cfg: Config) -> list[OcrItem]:
    """Apple Vision（ocrmac）で 1 ページを OCR する。返り値は正規化 bbox・原点左下。"""
    from ocrmac import ocrmac  # 遅延import（macOS専用）

    result = ocrmac.OCR(
        str(path),
        language_preference=cfg.ocr.languages,
        recognition_level=cfg.ocr.recognition_level,
    ).recognize()
    return [(text, conf, list(bbox)) for text, conf, bbox in result]


def _ocr_page_google(path: str | Path) -> list[OcrItem]:
    """Google Cloud Vision REST で 1 ページを OCR する。返り値は Apple 同形（正規化 bbox・原点左下）。

    鍵は GOOGLE_VISION_API_KEY から取得する。DOCUMENT_TEXT_DETECTION（密テキスト向け）を使い、
    応答パースは `_google_items_from_response`（純関数・テスト可能）に委譲する。
    """
    api_key = os.environ.get(GOOGLE_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"ocr.engine=google には環境変数 {GOOGLE_API_KEY_ENV} が必要です"
            "（Google Cloud Vision の API キーを設定してください）。"
        )
    content = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    body = json.dumps(
        {
            "requests": [
                {
                    "image": {"content": content},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["ja", "en"]},
                }
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_VISION_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Goog-Api-Key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_GOOGLE_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 鍵無効・課金未有効・quota 超過等はここに来る。本文にエラー詳細が入るので拾って上げる。
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Vision API エラー（HTTP {exc.code}）: {detail}") from exc
    except urllib.error.URLError as exc:
        # DNS 失敗・接続拒否・タイムアウト等のネットワーク障害。HTTPError の後に置く
        # （HTTPError は URLError のサブクラスなので順序が逆だと拾えない）。
        raise RuntimeError(
            f"Google Vision API 接続エラー（ネットワーク障害またはタイムアウト）: {exc.reason}"
        ) from exc
    # 応答に per-request の error が入る形式もあるため純関数側で検出させる
    return _google_items_from_response(data)


def _google_items_from_response(data: dict) -> list[OcrItem]:
    """Google Vision の annotate 応答を Apple 同形の OcrItem 列に変換する（純関数・ネット非依存）。

    paragraph 単位を 1 item とし、text は symbol を連結、bbox は paragraph の外接矩形を
    正規化(0..1)・原点左下 [x, y, w, h] に変換する（Apple/ocrmac と同形式）。

    Why 純関数化: HTTP を切り離すことで CI がネット非依存でパース仕様（座標変換・0 省略頂点・
    detectedBreak の空白補完）を検証できるようにする。
    """
    responses = data.get("responses") or [{}]
    resp = responses[0]
    if resp.get("error"):
        raise RuntimeError(f"Google Vision API エラー: {resp['error']}")
    fta = resp.get("fullTextAnnotation")
    if not fta:
        return []
    items: list[OcrItem] = []
    for page in fta.get("pages", []):
        width = page.get("width") or 0
        height = page.get("height") or 0
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                text = _google_paragraph_text(para)
                if not text:
                    continue
                bbox = _google_norm_bbox(para.get("boundingBox", {}), width, height)
                if bbox is None:
                    continue
                conf = float(para.get("confidence", 1.0))
                items.append((text, conf, bbox))
    return items


def _google_paragraph_text(paragraph: dict) -> str:
    """paragraph 内の symbol を連結してテキスト化する。detectedBreak で空白を補完する。

    日本語は語間に空白を挟まないため、SPACE / SURE_SPACE のみ半角空白を足し、
    行末（LINE_BREAK / EOL_SURE_SPACE）や折返しでは空白を足さない（本文が不自然に割れないため）。
    """
    parts: list[str] = []
    for word in paragraph.get("words", []):
        for symbol in word.get("symbols", []):
            parts.append(symbol.get("text", ""))
            brk = (
                symbol.get("property", {})
                .get("detectedBreak", {})
                .get("type", "")
            )
            if brk in ("SPACE", "SURE_SPACE"):
                parts.append(" ")
    return "".join(parts).strip()


def _google_norm_bbox(
    bounding_box: dict, width: int, height: int
) -> list[float] | None:
    """Vision の boundingBox を正規化(0..1)・原点左下 [x, y, w, h] に変換する。

    Vision は原点左上・y 下向き。normalizedVertices があれば優先し、無ければ pixel の
    vertices を width/height で正規化する。Vision は値が 0 の座標フィールドを省略するため、
    x/y の欠損は 0 とみなす。矩形が作れない（頂点なし・サイズ 0）場合は None を返す。
    """
    nverts = bounding_box.get("normalizedVertices")
    if nverts:
        xs = [v.get("x", 0.0) for v in nverts]
        ys = [v.get("y", 0.0) for v in nverts]
    else:
        verts = bounding_box.get("vertices")
        if not verts or not width or not height:
            return None
        xs = [v.get("x", 0) / width for v in verts]
        ys = [v.get("y", 0) / height for v in verts]
    if not xs or not ys:
        return None
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max_x - min_x
    h = max_y - min_y
    if w <= 0 or h <= 0:
        return None
    # 原点左上 → 原点左下へ Y 反転: 下辺 max_y が、左下原点では 1 - max_y の高さになる
    return [min_x, 1.0 - max_y, w, h]


def _page_images(pages_dir: Path) -> list[Path]:
    """pages/ 配下の画像ファイルを読み順（ファイル名昇順）で列挙する。"""
    return sorted(
        p for p in pages_dir.iterdir() if p.suffix.lower() in PAGE_IMAGE_EXTS
    )


def _write_page_json(out_path: Path, page_path: Path, items: list[OcrItem]) -> None:
    """1ページ分の OCR 結果を text/confidence/bbox として原子的に保存する。

    一時ファイルへ書いてから os.replace で置換し、途中Kill時の破損JSONを防ぐ。
    破損JSONを残すとレジュームが「OCR済み」と誤認してしまうため。
    """
    payload = {
        "page": page_path.stem,
        "source": str(page_path),
        "items": [
            {"text": text, "confidence": conf, "bbox": list(bbox)}
            for text, conf, bbox in items
        ],
    }
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, out_path)


def load_page_items(json_path: str | Path) -> list[OcrItem]:
    """保存済み ocr/page_XXXX.json を (text, confidence, bbox) タプル列に復元する。

    build 段（P6/P7）が OcrItem を直接扱えるようにするための読み取りヘルパ。
    """
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return [
        (item["text"], item["confidence"], list(item["bbox"]))
        for item in data["items"]
    ]


def ocr_all(
    cfg: Config,
    state: State | None = None,
    work_dir: str | Path | None = None,
    state_path: str | Path | None = None,
) -> None:
    """pages/ の全ページをOCRし ocr/page_XXXX.json に保存する。

    - 既に ocr/<stem>.json があるページはスキップし、未OCRページから続行する（レジューム）。
    - OCR完了数を state.ocr_done に記録する（state_path 指定時はページ毎に永続化）。
    - OCR失敗ページはログに記録して処理を継続する（JSON未作成→再開時に再試行される）。

    work_dir 未指定時は work/<book_title> を用いる（pipeline.book_dir と同一規約）。
    """
    if state is None:
        state = State()
    wd = Path(work_dir) if work_dir is not None else Path("work") / cfg.book_title
    pages_dir = wd / "pages"
    ocr_dir = wd / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    # google backend は鍵不在だと全ページが失敗し「OCR 済み 0 ページ」の空 PDF になってしまう。
    # per-page の握り潰し（下のループの except）に落ちる前に、開始時点で明確に止める。
    if cfg.ocr.engine == "google" and not os.environ.get(GOOGLE_API_KEY_ENV):
        raise RuntimeError(
            f"ocr.engine=google には環境変数 {GOOGLE_API_KEY_ENV} が必要です"
            "（Google Cloud Vision の API キーを設定してください）。"
        )

    if not pages_dir.exists():
        logger.warning("pages ディレクトリが存在しません: %s（OCR対象なし）", pages_dir)
        state.ocr_done = 0
        if state_path is not None:
            state.save(state_path)
        return

    pages = _page_images(pages_dir)
    logger.info("OCR開始: %d ページ", len(pages))
    done = 0
    failed = 0
    for page_path in pages:
        out_path = ocr_dir / f"{page_path.stem}.json"
        if out_path.exists():
            # 既にOCR済み → スキップして未OCRページへ（レジューム）
            done += 1
            continue
        try:
            items = ocr_page(page_path, cfg)
        except Exception as exc:  # noqa: BLE001 — 1ページの失敗で全体を止めない
            failed += 1
            logger.warning("OCR失敗のためスキップ: %s（%s）", page_path.name, exc)
            continue
        _write_page_json(out_path, page_path, items)
        done += 1
        state.ocr_done = done
        if state_path is not None:
            state.save(state_path)
        progress.emit("page", stage="ocr", page=done, total=len(pages))
        # 数百ページでも追跡できるよう一定間隔で進捗を出す。
        if done % _PROGRESS_EVERY == 0:
            logger.info("OCR進捗: %d/%d ページ", done, len(pages))

    state.ocr_done = done
    if state_path is not None:
        state.save(state_path)
    logger.info(
        "OCR完了: %d/%d ページ（失敗 %d ページ）", done, len(pages), failed
    )
