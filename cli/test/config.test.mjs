import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, test } from "node:test";

import { ensureConfig, renderMinimalConfig } from "../lib/config.mjs";

// 作成した temp を追跡してテスト後に一括削除する（/tmp への蓄積を防ぐ）。
const tmpDirs = [];
const trackTmp = (prefix) => {
  const d = mkdtempSync(join(tmpdir(), prefix));
  tmpDirs.push(d);
  return d;
};
after(() => {
  for (const d of tmpDirs) {
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      /* 後片付け失敗は結果に影響させない */
    }
  }
});

test("renderMinimalConfig: auto_region: true を含む最小 config を返す", () => {
  const yaml = renderMinimalConfig();
  assert.match(yaml, /auto_region:\s*true/);
});

test("ensureConfig: cwd に config.yaml があればそれを尊重する", () => {
  const dir = trackTmp("k2p-cfg-");
  const cfg = join(dir, "config.yaml");
  writeFileSync(cfg, "book_title: mine\n", "utf8");

  const result = ensureConfig(dir);
  assert.equal(result.path, cfg);
  assert.equal(result.generated, false);
  // 既存 config を書き換えない
  assert.equal(readFileSync(cfg, "utf8"), "book_title: mine\n");
});

test("ensureConfig: config.yaml が無ければ最小 config を temp に生成する", () => {
  const dir = trackTmp("k2p-nocfg-");

  const result = ensureConfig(dir);
  tmpDirs.push(dirname(result.path)); // ensureConfig が別途作る temp も後片付け対象にする
  assert.equal(result.generated, true);
  assert.notEqual(result.path, join(dir, "config.yaml")); // cwd を汚さない（temp に生成）
  assert.ok(existsSync(result.path));
  assert.match(readFileSync(result.path, "utf8"), /auto_region:\s*true/);
});
