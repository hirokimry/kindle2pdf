// 前回のウィザード回答を覚えるローカルファイルの読み書き（Issue #35）。
// cwd 直下の .kindle2pdf.json に置き、.gitignore でコミット混入を防ぐ。KINDLE2PDF_PREFS で
// パスを差し替え可能（テスト用）。壊れた/無いファイルは既定にフォールバックする。

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

function prefsPath(cwd = process.cwd()) {
  return process.env.KINDLE2PDF_PREFS || join(cwd, ".kindle2pdf.json");
}

// 前回回答を読む。無い/壊れている場合は空オブジェクト（＝既定提示なし）。
export function loadPrefs(cwd = process.cwd()) {
  const p = prefsPath(cwd);
  if (!existsSync(p)) return {};
  try {
    const obj = JSON.parse(readFileSync(p, "utf8"));
    return obj && typeof obj === "object" ? obj : {};
  } catch {
    return {};
  }
}

// 今回の回答を保存する。保存失敗は致命でない（次回は既定に戻るだけ）ため握り潰す。
export function savePrefs(prefs, cwd = process.cwd()) {
  try {
    writeFileSync(prefsPath(cwd), `${JSON.stringify(prefs, null, 2)}\n`, "utf8");
  } catch {
    /* 保存失敗は無視（記憶が効かないだけで撮影自体は進む） */
  }
}
