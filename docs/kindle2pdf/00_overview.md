# 📚 kindle2pdf ドキュメント一覧

購入済み Kindle 本を、Mac 上でスクショ → OCR → **検索可能 PDF** 化するツールの設計資料一式です。

## 📄 ファイル構成

| ファイル | 内容 |
|---------|------|
| [`00_overview.md`](00_overview.md) | この目次 |
| [`01_spec-design.md`](01_spec-design.md) | 要件・技術選定・アーキテクチャ・停止条件・**実機 PoC 検証結果**（v0.1 確定版） |
| [`02_implementation-plan.md`](02_implementation-plan.md) | ファイル構成・モジュール API・主要コマンド・座標変換・PoC マイルストーン |

## ✅ 現在のステータス

4 段パイプライン（P1〜P8）は実機 PoC を経て **実装済み**。撮影から検索可能 PDF 化まで一貫して動作する。

- OCR = **Apple Vision**（`ocrmac`）で確定
- 撮影 = **`screencapture` CLI**（サムネ写り込み回避）
- ページ化 = **1 撮影 = 1 ページ（分割しない）**、停止判定 = **pHash（距離 ≤ 2 ＝同一）**
  - 見開きの左右分割は廃止した。片ページ / 見開きは Kindle のウィンドウ幅で選ぶ（半画面幅 = 片ページ / 全画面幅 = 見開き 1 枚）。
- Vision 座標 → PDF 透明テキスト層は **Y 反転不要** と確定

## ▶️ 使い方

利用手順・macOS 権限・開発者向けセットアップはリポジトリ直下の [`README.md`](../../README.md)（英語）/ [`README.ja.md`](../../README.ja.md)（日本語）を参照。
