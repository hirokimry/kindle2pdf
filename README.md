# 📚 kindle2pdf

購入済み Kindle 本を、Mac 上でフル自動でスクショ → OCR → **検索可能 PDF** 化するシステム。

> **起動**: `npx kindle2pdf` で対話ウィザードが立ち上がり、質問に答えると撮影が始まる
> **方式**: スクリーンショット → OCR → 検索可能 PDF（画像＋透明テキスト層）
> **対象環境**: macOS（Apple Silicon 想定）
> **基盤**: [vibecorp](https://github.com/hirokimry/vibecorp)（AIエージェント役職化プラグイン）を導入済み

---

## ⚖️ 法的スタンス（重要）

本システムは **DRM を解除しない**。Kindle アプリに画面表示させた内容をスクリーンショットで取得し、OCR を付与して PDF 化する「自炊」に近い手法を採る。**利用は自分が購入した書籍の個人的利用の範囲に限定**し、再配布・共有は行わない。詳細は [`docs/kindle2pdf/01_spec-design.md`](docs/kindle2pdf/01_spec-design.md) 0章を参照。

---

## 🏗 アーキテクチャ（4段パイプライン）

```
capture   → Kindle制御・撮影・最終ページ検出   （唯一Kindleに触れる段 / リアルタイム）
preprocess → トリミング・正規化（1 撮影 = 1 ページ）        （バッチ / 見開き分割はしない）
ocr        → Apple Vision OCR（座標付き抽出）    （バッチ）
build      → 画像＋透明テキスト層で検索可能PDF   （バッチ）
```

**責務分離**: Kindle に触れるのは `capture.py` のみ。preprocess/ocr/build は撮影済み画像だけを入力とし、Kindle 操作なしに何度でも再実行できる。中断/再開は `state.json` で管理する。

主要な確定事項（実機 PoC で検証済み）:

- **OCR = Apple Vision（`ocrmac`）** — 日本語の余分な空白がほぼ無く、語句検索が壊れない
- **撮影 = `screencapture -x` CLI** — フローティングサムネイル写り込みを回避
- **撮影領域 = ウィンドウ自動検出 + 動的タイトルバークロップ** — 設定不要で撮影ごとに Kindle ウィンドウを毎回検出して `screencapture -l` で撮り、AX（アクセシビリティ）で実測した macOS タイトルバー帯の高さ **だけ** を上端から落とす。信号機ボタンが帯の上下中央に並ぶ規約から `帯高 = 2×(ボタン中心 − ウィンドウ上端)` を都度算出するため、**固定 px を持たず**・retina 倍率が変わっても・表紙ページでも狂わない。本文の白余白・柱は一切削らない。**通常ウィンドウ表示が前提**（全画面はバックグラウンドからの自動ページ送りが OS に阻まれるため使わない）。Kindle 自身の進捗フッター/ヘッダーは Kindle の表示設定（没入表示）で消す
- **ページ化 = 1 撮影 = 1 ページ（分割しない）** — 見開きの左右分割は廃止した。ウィンドウ中身をそのまま撮り、片ページ/見開きは **Kindle のウィンドウ幅** で選ぶ。Kindle ウィンドウを半画面幅にすれば Kindle が単一ページ表示になり片ページ（文章系向け）、通常ウィンドウを画面幅いっぱいまで広げれば見開き表示になり見開き 1 枚がそのまま横長 PDF ページになる（漫画向け・柱や大ゴマが綴じ目で切れない）。いずれも通常ウィンドウ表示のままで、macOS のフルスクリーンは使わない（自動ページ送りが OS に阻まれるため）
- **停止判定 = pHash（距離 ≤ 2 を同一）** — サムネ除去前提で最終ページ検出が安定
- **座標変換 = Y反転不要** — Vision・reportlab とも原点左下

---

## ▶️ 使い方

対話ウィザードを叩いて質問に答えるだけで、撮影から検索可能 PDF まで走る。**本ごとの設定ファイル手編集も、実行のたびの仮想環境の有効化も不要**（Python コアの導入は初回一度きり）。

> [!IMPORTANT]
> **必要なもの**（初回のみ）:
> - **Node.js ≥ 18**（`npx` を使うため）
> - **Python コア**（撮影本体。下記「開発」節の手順で一度 `pip install` しておく）
> - **macOS の 2 権限**（画面収録・アクセシビリティ。下記「事前準備」）
>
> `npx kindle2pdf` は内部で `python -m kindle2pdf` を呼ぶため、Python コア未導入だと起動後に「Python コアを起動できません」で止まる。

```bash
# 1. Kindle で対象書籍を 1 ページ目で開いておく
# 2. ウィザードを起動して質問に答える
npx kindle2pdf
```

聞かれるのは実質 2 つ。

- 📖 **本のタイトル**（出力 PDF のファイル名になる）
- 📐 **ページ構成**（片ページ / 見開き）— 同時に「Kindle ウィンドウを半分幅／全画面幅に」と案内が出る

答えると撮影が始まり、進捗がスピナーで表示され、完了すると PDF が自動で開く。中断しても、同じ本を選び直せば続きから再開できる。

> [!NOTE]
> `npx kindle2pdf` は Node 製の対話フロント。撮影本体は Python コアが担う。
> 上級者は Python コアを直叩きして無対話フル制御もできる（下記「開発」節）。

### 🔑 事前準備（macOS 権限）

撮影・ページ送りを行うプロセス（ウィザードを起動したターミナル）に **2 つの権限**が要る。OCR・PDF 化には追加権限は不要。

| 権限 | 用途 | 無いとどうなる |
|------|------|--------------|
| 🖥️ 画面収録（Screen Recording） | `screencapture` でページを撮影 | 撮影画像がデスクトップ壁紙のみになり本文が写らない |
| ⌨️ アクセシビリティ | ページ送り（キー送出）・Kindle 前面化・AX でタイトルバー高さを実測 | ページ送りが `-1719` で失敗する / タイトルバー高さを実測できず撮影が止まる |

- 付与後はターミナルの**再起動**が要る（特に画面収録）。
- Kindle アプリ名は環境で異なるが、**未設定でも自動検出される**。`"Amazon Kindle"` → `"Kindle"` の順に AppleScript で実在を検証し、通った名前をキャッシュして以後使い回す。
  - 自動検出で解決しない特殊なアプリ名のときだけ、`config.yaml` の `capture.app_name` に手動指定する（トラブルシュート用途）。

---

## 🗂 リポジトリ構成

```
kindle2pdf/
├─ pyproject.toml
├─ config.example.yaml
├─ src/kindle2pdf/         # cli / config / state / imaging / capture / preprocess / ocr / build_pdf / pipeline
├─ tests/                  # test_*.py（pytest, 純粋ロジック）+ test_smoke.sh（vibecorp CI）
│  └─ fixtures/            # PoC実画像（著作物のためコミットしない）
├─ docs/kindle2pdf/        # 仕様・設計書 / 実装計画書
└─ .claude/               # vibecorp（AIエージェント役職・ルール・ナレッジ）
```

---

## 🛠 実装ステータス

4 段パイプライン（P1〜P8）は実機 PoC を経て実装済み。各チケットの受け入れ基準は以下（詳細は [`docs/kindle2pdf/02_implementation-plan.md`](docs/kindle2pdf/02_implementation-plan.md)）。

| # | 内容 | 受け入れ基準 |
|---|------|-------------|
| P1 | region 実測（calibrate） | 撮影画像に UI/柱が入らず本文だけが写る |
| P2 | 撮影ループ | 欠け・重複なく `raw/` に連番保存 |
| P3 | 最終ページ検出 | 末尾で停止・誤検出0 |
| P4 | トリミング（1 撮影 = 1 ページ） | 各 `pages/` が UI無し・ウィンドウ中身のまま（分割しない） |
| P5 | Vision OCR | 返り値が `(text, conf, [x,y,w,h])`、xy∈[0,1] |
| P6 | 透明テキスト層PDF | 既知語が検索ヒット・文字位置が本文と重なる |
| P7 | 統合＋レジューム | 途中Kill→再実行で続きから完了 |
| P8 | 仕上げ | 数百ページで安定動作・失敗ページはログ記録し継続 |

---

## 🧑‍💻 開発（コントリビューター向け）

利用者は `npx kindle2pdf` だけでよい。以下はコアの開発・テスト・上級者の無対話実行向け。

### 🐍 Python コアのセットアップ

macOS 標準 Python はコンパイラ非搭載でビルドに失敗するため、Homebrew Python + venv が必須（PEP 668 回避）。

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[macos,dev]"   # ocrmac は macos extra。非macは省略
```

### ⚙️ 無対話フル制御（上級者・CI）

ウィザードを介さず、`config.yaml` でフル制御できる（二段構え）。

```bash
cp config.example.yaml config.yaml               # 設定を用意
python -m kindle2pdf calibrate --config config.yaml   # 撮影領域 region を実測
python -m kindle2pdf run --config config.yaml         # capture→preprocess→ocr→build 全自動
# 中断後も同じコマンドでレジューム
```

`npx kindle2pdf` フロントもこの経路にフラグ（`--title` / `--reading-order` / `--progress json`）を渡してコアを起動する。

### 🖥️ Node フロントの開発

```bash
node cli/index.mjs           # ウィザードをローカル起動（bin: kindle2pdf）
node --test cli/test/*.test.mjs   # フロントの単体テスト
```

---

## 🤖 vibecorp について

本リポジトリは vibecorp（full プリセット）を基盤導入している。Issue を渡すと担当の専門役が実装から PR まで回す運用を想定。プラグイン初回セットアップ:

```bash
/plugin marketplace add hirokimry/vibecorp
/plugin install vibecorp@vibecorp --scope project
```
