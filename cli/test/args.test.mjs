import assert from "node:assert/strict";
import { test } from "node:test";

import { buildCoreArgs, PAGE_LAYOUTS, readingOrderFor, validateTitle } from "../lib/args.mjs";

test("validateTitle: コア Config.validate() と同じ規則で弾く（/ \\ . .. 空）", () => {
  assert.equal(validateTitle("吾輩は猫である"), undefined); // OK
  assert.match(validateTitle(""), /入力/);
  assert.match(validateTitle("   "), /入力/);
  assert.match(validateTitle("a/b"), /\/ や/);
  assert.match(validateTitle("a\\b"), /\/ や/);
  assert.match(validateTitle("."), /\. や/);
  assert.match(validateTitle(".."), /\. や/);
});

test("readingOrderFor: single は読み順不問（null）", () => {
  assert.equal(readingOrderFor("single"), null);
  assert.equal(readingOrderFor("spread-rtl"), "rtl");
  assert.equal(readingOrderFor("spread-ltr"), "ltr");
  assert.equal(readingOrderFor("unknown"), null);
});

test("PAGE_LAYOUTS は 3 択（片ページ / 見開き rtl / 見開き ltr）", () => {
  assert.deepEqual(
    PAGE_LAYOUTS.map((l) => l.value),
    ["single", "spread-rtl", "spread-ltr"],
  );
});

test("buildCoreArgs: タイトルと見開き読み順をフラグに反映する", () => {
  const args = buildCoreArgs({ title: "猫", layout: "spread-rtl", configPath: "/t/config.yaml" });
  assert.deepEqual(args, [
    "run",
    "--config",
    "/t/config.yaml",
    "--title",
    "猫",
    "--reading-order",
    "rtl",
    "--progress",
    "json",
    "--open",
  ]);
});

test("buildCoreArgs: single は --reading-order を付けない", () => {
  const args = buildCoreArgs({ title: "本", layout: "single" });
  assert.ok(!args.includes("--reading-order"));
  assert.ok(args.includes("--title"));
});

test("buildCoreArgs: 空タイトルは --title を付けない（config 値を尊重）", () => {
  const args = buildCoreArgs({ title: "   ", layout: "single" });
  assert.ok(!args.includes("--title"));
});

test("buildCoreArgs: open=false で --no-open", () => {
  const args = buildCoreArgs({ title: "本", layout: "single", open: false });
  assert.ok(args.includes("--no-open"));
  assert.ok(!args.includes("--open"));
});
