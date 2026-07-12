import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { ensureConfig, renderMinimalConfig } from "../lib/config.mjs";

test("renderMinimalConfig: auto_region: true を含む最小 config を返す", () => {
  const yaml = renderMinimalConfig();
  assert.match(yaml, /auto_region:\s*true/);
});

test("ensureConfig: cwd に config.yaml があればそれを尊重する", () => {
  const dir = mkdtempSync(join(tmpdir(), "k2p-cfg-"));
  const cfg = join(dir, "config.yaml");
  writeFileSync(cfg, "book_title: mine\n", "utf8");

  const result = ensureConfig(dir);
  assert.equal(result.path, cfg);
  assert.equal(result.generated, false);
  // 既存 config を書き換えない
  assert.equal(readFileSync(cfg, "utf8"), "book_title: mine\n");
});

test("ensureConfig: config.yaml が無ければ最小 config を temp に生成する", () => {
  const dir = mkdtempSync(join(tmpdir(), "k2p-nocfg-"));

  const result = ensureConfig(dir);
  assert.equal(result.generated, true);
  assert.notEqual(result.path, join(dir, "config.yaml")); // cwd を汚さない（temp に生成）
  assert.ok(existsSync(result.path));
  assert.match(readFileSync(result.path, "utf8"), /auto_region:\s*true/);
});
