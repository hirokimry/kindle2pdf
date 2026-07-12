import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";

import { loadPrefs, savePrefs } from "../lib/prefs.mjs";

const tmpDirs = [];
const trackTmp = () => {
  const d = mkdtempSync(join(tmpdir(), "k2p-prefs-"));
  tmpDirs.push(d);
  return d;
};
after(() => {
  for (const d of tmpDirs) {
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
});

test("loadPrefs: ファイルが無ければ空オブジェクト", () => {
  assert.deepEqual(loadPrefs(trackTmp()), {});
});

test("savePrefs → loadPrefs で往復し前回回答を覚える", () => {
  const dir = trackTmp();
  savePrefs({ layout: "spread-rtl" }, dir);
  assert.deepEqual(loadPrefs(dir), { layout: "spread-rtl" });
});

test("loadPrefs: 壊れた JSON は空オブジェクトにフォールバック", () => {
  const dir = trackTmp();
  writeFileSync(join(dir, ".kindle2pdf.json"), "{壊れた", "utf8");
  assert.deepEqual(loadPrefs(dir), {});
});

test("KINDLE2PDF_PREFS でパスを差し替えられる", () => {
  const dir = trackTmp();
  const custom = join(dir, "custom-prefs.json");
  const saved = process.env.KINDLE2PDF_PREFS;
  process.env.KINDLE2PDF_PREFS = custom;
  try {
    savePrefs({ layout: "single" });
    assert.deepEqual(loadPrefs(), { layout: "single" });
  } finally {
    if (saved === undefined) delete process.env.KINDLE2PDF_PREFS;
    else process.env.KINDLE2PDF_PREFS = saved;
  }
});
