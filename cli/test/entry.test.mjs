import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { after, test } from "node:test";

// 配布エントリ cli/index.mjs を実際に node で実行し、passthrough 経路が Python コアを
// `-m kindle2pdf <引数>` で起動することを検証する（import typo・結線バグを CI で検知する）。
const HERE = dirname(fileURLToPath(import.meta.url));
const ENTRY = join(HERE, "..", "index.mjs");

// 作成した temp ディレクトリを追跡し、テスト終了後に一括削除する（/tmp への蓄積を防ぐ）。
const tmpDirs = [];
after(() => {
  for (const d of tmpDirs) {
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      /* 後片付け失敗は結果に影響させない */
    }
  }
});

// KINDLE2PDF_PYTHON に差し込むフェイク Python を temp に作って返す。body で挙動を差し替える。
function makeFakePython(body) {
  const dir = mkdtempSync(join(tmpdir(), "k2p-fakepy-"));
  tmpDirs.push(dir);
  const fake = join(dir, "fakepy.mjs");
  writeFileSync(fake, `#!/usr/bin/env node\n${body}\n`, "utf8");
  chmodSync(fake, 0o755);
  return fake;
}

test("passthrough: 引数ありは -m kindle2pdf に引数を素通しでコアを起動する", () => {
  const fake = makeFakePython(
    'process.stdout.write("CORE_ARGV: " + process.argv.slice(2).join(" ") + "\\n");',
  );
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
  const fake = makeFakePython("process.exit(3);");
  const res = spawnSync("node", [ENTRY, "run"], {
    env: { ...process.env, KINDLE2PDF_PYTHON: fake },
    encoding: "utf8",
  });
  assert.equal(res.status, 3);
});

test("passthrough: コアがシグナルで死んだら成功扱いにしない（非ゼロ終了）", () => {
  // 自分自身を SIGTERM で殺す → Node は close(code=null, signal="SIGTERM") を渡す。
  const fake = makeFakePython('process.kill(process.pid, "SIGTERM");');
  const res = spawnSync("node", [ENTRY, "run"], {
    env: { ...process.env, KINDLE2PDF_PYTHON: fake },
    encoding: "utf8",
  });
  assert.equal(res.status, 1); // code=null を 0 にせず失敗として伝える
});
