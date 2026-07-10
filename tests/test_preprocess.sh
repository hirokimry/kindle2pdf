#!/usr/bin/env bash
# P4(見開き分割＋トリミング)の検証。
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式。
#
# 追加依存（Pillow/PyYAML/imagehash/pytest）が揃う環境では pytest で
# process_all の挙動（2N ページ・単一カラム・トリミング・黒画面除外・config 切替）を検証する。
# 依存が揃わない環境（依存を入れない CI 等）では py_compile のみ行って SKIP する。
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "SKIP: python3 が無い環境"
  exit 0
fi

echo "=== preprocess.py 構文チェック（py_compile） ==="
python3 -m py_compile src/kindle2pdf/preprocess.py
echo "OK: 構文チェック成功"

if python3 -c "import PIL, yaml, imagehash, pytest" >/dev/null 2>&1; then
  echo "=== pytest による挙動検証 ==="
  PYTHONPATH=src python3 -m pytest tests/test_preprocess.py -q
  echo "OK: 挙動検証成功"
else
  echo "SKIP: 実行依存（Pillow/PyYAML/imagehash/pytest）が未インストールのため挙動検証をスキップ"
fi
