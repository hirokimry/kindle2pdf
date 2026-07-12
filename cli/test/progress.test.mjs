import assert from "node:assert/strict";
import { test } from "node:test";

import { describeEvent, parseProgressLine } from "../lib/progress.mjs";

test("parseProgressLine: JSON 行をイベントに、非JSON/空行は null", () => {
  assert.deepEqual(parseProgressLine('{"event":"stage_start","stage":"ocr"}'), {
    event: "stage_start",
    stage: "ocr",
  });
  assert.equal(parseProgressLine(""), null);
  assert.equal(parseProgressLine("  "), null);
  assert.equal(parseProgressLine("2026-07-12 INFO 人間向けログ"), null);
  assert.equal(parseProgressLine("{壊れたJSON"), null);
  assert.equal(parseProgressLine('{"no":"event key"}'), null);
});

test("describeEvent: 段名を日本語ラベルに、ページ番号/総数を描画文言に変換", () => {
  assert.equal(describeEvent({ event: "stage_start", stage: "capture" }), "撮影 を開始");
  assert.equal(describeEvent({ event: "stage_complete", stage: "build" }), "PDF生成 が完了");
  assert.equal(
    describeEvent({ event: "page", stage: "ocr", page: 3, total: 10 }),
    "OCR: 3/10 ページ",
  );
  // capture は総数未知（total=null）→ 件数のみ
  assert.equal(
    describeEvent({ event: "page", stage: "capture", page: 5, total: null }),
    "撮影: 5 ページ",
  );
  assert.equal(
    describeEvent({ event: "complete", output: "work/x/output/x.pdf" }),
    "完了: work/x/output/x.pdf",
  );
  assert.equal(
    describeEvent({ event: "error", stage: "capture", message: "Kindle未起動" }),
    "エラー（撮影）: Kindle未起動",
  );
});

test("describeEvent: 不明イベント/不正入力は null", () => {
  assert.equal(describeEvent(null), null);
  assert.equal(describeEvent({ event: "unknown" }), null);
  assert.equal(describeEvent({}), null);
});
