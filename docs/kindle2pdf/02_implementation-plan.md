# 🛠 Kindle本 PDF化システム 実装計画書（Claude Code着手用）

> **対象**: 仕様・設計書 v0.1（実機PoC反映版）を実装に落とす詳細計画
> **前提**: macOS / Homebrew Python 3.12 + venv / OCR=Apple Vision(`ocrmac`) / 撮影=`screencapture` CLI
> **作成日**: 2026-07-09

---

## 📁 1. リポジトリ構成

```
kindle2pdf/
├─ pyproject.toml            # 依存とエントリポイント
├─ README.md                 # セットアップ・権限・使い方
├─ config.example.yaml       # 設定サンプル
├─ src/kindle2pdf/
│  ├─ __init__.py
│  ├─ cli.py                 # click製CLI（capture/preprocess/ocr/build/run/calibrate）
│  ├─ config.py              # YAML読込・検証（dataclass）
│  ├─ state.py               # state.json 読み書き・レジューム
│  ├─ capture.py             # Kindle制御・撮影・最終ページ検出
│  ├─ preprocess.py          # 見開き分割・トリミング・正規化
│  ├─ ocr.py                 # Vision OCRラッパ（ocrmac）
│  ├─ build_pdf.py           # 画像＋透明テキスト層→検索可能PDF
│  ├─ imaging.py             # pHash・明度チェック等の共通画像ユーティリティ
│  └─ pipeline.py            # 4段オーケストレーション
└─ tests/
   ├─ fixtures/              # 本日のPoCスクショ4枚をゴールデンとして格納
   ├─ test_imaging.py        # pHash 同一/別ページ・分割
   ├─ test_ocr.py            # Vision結果の型・座標範囲
   ├─ test_build_pdf.py      # 座標変換・検索可能性
   └─ test_pipeline.py       # レジューム
```

**責務の分離（重要）**: Kindleに触れるのは `capture.py` のみ。`preprocess/ocr/build` は撮影済み画像だけを入力とするバッチで、Kindle操作なしに何度でも再実行できる。

---

## 📦 2. 依存とセットアップ

`pyproject.toml`（主要依存）:

```toml
[project]
name = "kindle2pdf"
requires-python = ">=3.11"
dependencies = [
  "ocrmac>=1.0",        # Apple Vision OCR
  "pillow>=10",         # 画像処理
  "imagehash>=4.3",     # pHash
  "reportlab>=4",       # PDFの画像＋透明テキスト層
  "pypdf>=4",           # PDF結合・メタデータ
  "pyyaml>=6",          # 設定
  "click>=8",           # CLI
]
[project.scripts]
kindle2pdf = "kindle2pdf.cli:main"
```

セットアップ（PEP 668回避のためvenv必須）:

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**権限（macOS）**: OCR・撮影・PDF化に特別な権限は不要。**キー送出（ページ送り）を行うプロセス（＝このvenvを起動したTerminal）にのみ「アクセシビリティ」権限**が要る。画面収録権限は`screencapture`の対象取得に必要な場合がある。→ 権限は「ユーザー自身のTerminal」に、実行中だけ付与し、いつでも取り消せる形になる（Claudeアプリに恒久付与する必要はない）。

---

## ⚙️ 3. 設定スキーマ `config.yaml`

```yaml
book_title: "sample-book"

capture:
  region: [x, y, width, height]   # screencapture -R の値。calibrateで実測
  spread_mode: true               # 見開き2ページ表示か（true=左右分割する）
  page_turn_key: "right"          # right(key code 124) / left(123)
  page_turn_method: "osascript"   # osascript | cliclick
  page_turn_wait: 1.0             # ページ送り後の描画待ち秒
  stable_wait: 0.3                # 安定確認の追加待ち
  stable_required: 2              # 連続安定フレーム数（撮影確定）
  end_detect_repeats: 3           # 同一ハッシュ連続で終了
  same_threshold: 2               # pHash距離 <=2 を同一とみなす（サムネ除去前提）
  max_pages: 3000                 # 安全上限
  prevent_sleep: true             # caffeinate併用

preprocess:
  trim: {top: 0.11, bottom: 0.035, left: 0.015, right: 0.015}  # 比率トリミング
  split_spread: true              # 中央でL/R分割
  min_brightness: 20              # これ未満は黒画面異常として除外

ocr:
  languages: ["ja-JP", "en-US"]
  recognition_level: "accurate"   # accurate | fast
  reading_order: "split"          # split(分割前提で単純Yソート) | column(未分割で列認識)

build:
  image_format: "jpeg"
  jpeg_quality: 88
  font: "HeiseiMin-W3"            # reportlab内蔵CIDフォント（日本語テキスト層用）
```

---

## 🗂 4. `state.json` スキーマ（レジューム用）

```json
{
  "book_title": "sample-book",
  "stage": "capture",              // capture|preprocess|ocr|build|done
  "captured": 42,                  // raw/ に確定済みの撮影枚数
  "last_hash": "e1c3...",          // 直近確定フレームのpHash
  "repeat_count": 0,               // 同一ハッシュ連続回数
  "pages_total": 84,               // 分割後の確定ページ数（preprocess後に確定）
  "ocr_done": 30,                  // OCR完了ページ数
  "updated_at": "..."              // 実行後にスタンプ
}
```

各段は「処理→state更新」を1ページ単位で逐次コミットし、再実行時は未完了ページから続行する。

---

## 🔩 5. モジュール設計（関数シグネチャ＋要点）

### 5.1 `imaging.py`

```python
def phash(path: str) -> imagehash.ImageHash        # imagehash.phash(Image.open(path))
def hamming(a, b) -> int                            # a - b
def is_same(a, b, threshold: int) -> bool           # hamming(a,b) <= threshold
def mean_brightness(path: str) -> float             # 黒画面異常検知
```
**PoC実測の根拠**: 連続別ページ=距離16〜26、同一=0、サムネ有無のみ差=6。→ サムネを出さない`screencapture`前提で`threshold=2`が安全。

### 5.2 `capture.py`

```python
def activate_kindle() -> None
def turn_page(cfg) -> None            # osascript or cliclick で右/左矢印
def grab(region, out_path) -> str     # screencapture -x -R"x,y,w,h" out_path
def run_capture(cfg, state) -> None   # 送り→待機→撮影→安定確認→pHash→終了判定ループ
```

主要コマンド（そのまま使える）:

```bash
# 領域撮影（サムネイルを出さない）
screencapture -x -R"{x},{y},{w},{h}" raw/page_{n:04d}.png

# ページ送り（osascript / key code 124=右, 123=左）
osascript -e 'tell application "Kindle" to activate' \
          -e 'delay 0.15' \
          -e 'tell application "System Events" to key code 124'

# 代替: cliclick（brew install cliclick, アクセシビリティ権限）
cliclick kp:arrow-right

# スリープ抑止（キャプチャ中）
caffeinate -dimsu -w $$   # 実行プロセス終了まで抑止
```

終了判定ロジック（擬似コード）:

```python
prev = None; repeat = 0
for n in range(cfg.max_pages):
    p = grab(...); 
    if mean_brightness(p) < cfg.min_brightness: retry; continue
    h = phash(p)
    if prev and is_same(h, prev, cfg.same_threshold):
        repeat += 1
        if repeat >= cfg.end_detect_repeats: break   # 最終ページ
    else:
        repeat = 0; save(p); state.commit(n, h)
    prev = h
    turn_page(cfg); sleep(cfg.page_turn_wait)
```

### 5.3 `preprocess.py`

```python
def split_spread(img: Image) -> list[Image]   # 中央で左右2分割（spread_mode時）
def trim(img: Image, ratios) -> Image         # 比率トリミングでUI除去
def process_all(cfg, state) -> None           # raw/ -> pages/（確定ページ）
```

### 5.4 `ocr.py`

```python
def ocr_page(path, cfg) -> list[tuple[str, float, list[float]]]:
    """ocrmacでVision OCR。返り値: (text, confidence, [x,y,w,h]) 正規化・原点左下。"""
    from ocrmac import ocrmac
    return ocrmac.OCR(path,
        language_preference=cfg.ocr.languages,
        recognition_level=cfg.ocr.recognition_level).recognize()
```
**PoC根拠**: Visionは余分な空白がほぼ無く（0.01/字）、日本語の語句検索が壊れない。分割済みページは単一カラムなので読み順は「yの大きい順（＝上から）」で単純ソート。

### 5.5 `build_pdf.py`（システムの肝：透明テキスト層）

**座標変換はY反転不要**（Vision・reportlabとも原点左下）。72dpi基準でピクセル=ポイント換算。

```python
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from PIL import Image

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))  # 日本語CIDフォント

def render_page(c, image_path, items, dpi):
    iw, ih = Image.open(image_path).size
    pw, ph = iw * 72.0/dpi, ih * 72.0/dpi          # ポイント換算
    c.setPageSize((pw, ph))
    c.drawImage(ImageReader(image_path), 0, 0, width=pw, height=ph)
    c.setFont("HeiseiMin-W3", 1)
    for text, conf, (x, y, w, h) in items:
        if not text.strip(): continue
        c.setFontSize(max(h * ph, 1))              # 箱の高さにフォントを合わせる
        t = c.beginText(x * pw, y * ph)            # 原点左下→そのまま
        t.setTextRenderMode(3)                     # 3 = 不可視（画像の上に検索用テキスト）
        t.textLine(text)
        c.drawText(t)
    c.showPage()

def build(pages: list[tuple[str, list]], out_path, cfg):
    c = canvas.Canvas(out_path)
    for image_path, items in pages: render_page(c, image_path, items, cfg.build... )
    c.save()
```

これ単体で「画像＋不可視テキスト層」の検索可能PDFが完成する（別途の結合・ocrmypdf不要）。

### 5.6 `pipeline.py` / `cli.py`

```bash
kindle2pdf calibrate --config config.yaml   # region実測補助（1枚撮って枠を確認）
kindle2pdf run       --config config.yaml   # capture→preprocess→ocr→build 全自動
kindle2pdf capture   --config config.yaml   # 段別実行も可（レジューム対応）
```

---

## 🚦 6. 実装順序（PoC→統合）と受け入れ基準

| # | チケット | 内容 | 受け入れ基準 |
|---|---------|------|-------------|
| P1 | region実測 | `calibrate`で読書領域だけを1枚撮る | 撮影画像にUI/柱が入らず本文だけが写る |
| P2 | 撮影ループ | 送り→待機→撮影を数十ページ実行、pHashログ | 欠け・重複なく`raw/`に連番保存 |
| P3 | 最終ページ検出 | 実際の巻末で停止 | 末尾で`end_detect_repeats`回一致し停止。誤検出0 |
| P4 | 見開き分割＋トリミング | `raw/`→`pages/` | 各`pages/`が単一カラム・UI無し |
| P5 | Vision OCR | `ocr_page`で座標付き抽出 | 返り値が`(text,conf,[x,y,w,h])`、xy∈[0,1] |
| P6 | 透明テキスト層PDF | 1ページで画像＋不可視テキスト | PDFで既知語（例「デプスインタビュー」）が**検索ヒット**、文字位置が本文と重なる |
| P7 | 統合＋レジューム | `run`／`state.json` | 途中Kill→再実行で続きから完了 |
| P8 | 仕上げ | 圧縮・ログ・エラー処理 | 数百ページで安定動作、失敗ページはログに記録し画像のみで継続 |

**ゴールデンテスト**: 本日のPoCスクショ4枚を`tests/fixtures/`に置き、P4〜P6の期待出力（分割枚数、既知語の検索ヒット、座標が本文域内）を回帰テスト化する。

---

## ⚠️ 7. 実装観点のリスクと対策

- **座標変換（最重要）**: 原点左下で一致するためY反転は不要。検証は「既知語が検索ヒットし、その不可視テキストの矩形が本文文字と重なる」ことをP6で確認（`setTextRenderMode(0)`で一時可視化して目視も可）。
- **見開き読み順**: 既定は「分割してから単純Yソート」（最も確実）。未分割運用にするなら、Vision bboxの`x`で2クラスタに分けて各列をyソートする列認識を実装（PoCでVisionは未分割でも概ね正しく並べたが、確実性のため分割を既定とする）。
- **サムネイル写り込み**: `screencapture`はサムネを出さないため原理的に回避。`⌘⇧4`は使わない。
- **スリープ／通知**: `caffeinate`でスリープ抑止、実行中はFocus（おやすみ）モード推奨。通知バナーが写ると当該ページのみ再撮影。
- **描画遅延**: `page_turn_wait`＋「連続安定フレーム一致で確定」で、ローディング中フレームの確定を防止。
- **縦書き（将来）**: 縦書き書籍はVisionが向き検出。`reading_order`と分割方針を縦書き用に拡張する余地を残す。

---

## 🔐 8. セキュリティ姿勢（設計として明記）

このツールがmacOSに要求する強い権限は**アクセシビリティ（キー送出）だけ**で、それも**ユーザー自身のTerminalに、実行時に限って**付与される。撮影・OCR・PDF化はいずれも特別権限不要。すべてローカル完結で外部送信なし。個人が購入した書籍の私的利用に限定する前提は仕様書0章の通り。

---

## 🧾 9. 引き継ぎメモ（Claude Codeセッションへ）

1. 本計画と仕様書v0.1（プロジェクト内）を読み込む。
2. `tests/fixtures/`に本日のPoC画像を配置し、P1（calibrate）から着手。
3. OCRは`ocrmac`（Vision）で確定。TesseractはmacOS外フォールバックのみ。
4. 迷ったら「責務分離（Kindleに触るのはcaptureだけ）」と「段ごとにstate逐次コミット」を優先原則とする。
