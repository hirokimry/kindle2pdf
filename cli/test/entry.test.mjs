import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

// 配布エントリ cli/index.mjs を実際に node で実行し、passthrough 経路が Python コアを
// `-m kindle2pdf <引数>` で起動することを検証する（import typo・結線バグを CI で検知する）。
const HERE = dirname(fileURLToPath(import.meta.url));
const ENTRY = join(HERE, "..", "index.mjs");

// KINDLE2PDF_PYTHON に差し込むフェイク Python。受け取った argv を STDOUT に出して終了する。
function makeFakePython() {
  const dir = mkdtempSync(join(tmpdir(), "k2p-fakepy-"));
  const fake = join(dir, "fakepy.mjs");
  writeFileSync(
    fake,
    '#!/usr/bin/env node\nprocess.stdout.write("CORE_ARGV: " + process.argv.slice(2).join(" ") + "\\n");\n',
    "utf8",
  );
  chmodSync(fake, 0o755);
  return fake;
}

test("passthrough: 引数ありは -m kindle2pdf に引数を素通しでコアを起動する", () => {
  const fake = makeFakePython();
  const res = spawnSync("node", [ENTRY, "run", "--config", "config.yaml"], {
    env: { ...process.env, KINDLE2PDF_PYTHON: fake },
    encoding: "utf8",
  });
  assert.equal(res.status, 0, res.stderr);
  // passthrough は stdio:inherit なのでフェイクの出力が親までそのまま届く。
  assert.match(res.stdout, /CORE_ARGV: -m kindle2pdf run --config config\.yaml/);
});

test("passthrough: Python コアが起動できないとき明確なエラーを stderr に出す", () => {
  const res = spawnSync("node", [ENTRY, "run"], {
    env: { ...process.env, KINDLE2PDF_PYTHON: "/nonexistent/python-xyz" },
    encoding: "utf8",
  });
  assert.equal(res.status, 1);
  assert.match(res.stderr, /起動できません/); // spawn 失敗が握り潰されず見える
});

test("passthrough: コアの終了コードをそのまま伝播する", () => {
  const dir = mkdtempSync(join(tmpdir(), "k2p-fakepy-"));
  const fake = join(dir, "fakepy.mjs");
  writeFileSync(fake, "#!/usr/bin/env node\nprocess.exit(3);\n", "utf8");
  chmodSync(fake, 0o755);
  const res = spawnSync("node", [ENTRY, "run"], {
    env: { ...process.env, KINDLE2PDF_PYTHON: fake },
    encoding: "utf8",
  });
  assert.equal(res.status, 3);
});
