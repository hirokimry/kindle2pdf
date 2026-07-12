// 撮影に使う config のパスを解決する。cwd に config.yaml があればそれを尊重（利用者の
// region キャリブレーション等を活かす）。無ければ最小 config を temp に生成して使う。
// これにより「config.yaml を手編集しなくても npx kindle2pdf が動く」を実現する（Issue #34）。
// コア本体は改修せず、フロント側で config の存在を吸収する（責務分離）。

import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// 最小 config の中身。空でも Config.load は全既定（auto_region=true）になるが、生成物を
// 覗いた利用者が意図を追えるよう最小限のキーとコメントを残す。book_title は --title フラグ
// が上書きするため既定のままでよい。
export function renderMinimalConfig() {
  return [
    "# npx kindle2pdf が自動生成した最小設定（手編集不要・Issue #34）。",
    "# ウィンドウ自動検出で撮るため region 実測は不要。詳細調整は config.yaml を置けば尊重される。",
    "capture:",
    "  auto_region: true",
    "",
  ].join("\n");
}

// config パスを解決する。返り値 { path, generated }。generated=true のとき呼び出し側は
// 使用後に temp を破棄してよい（必須ではない）。
export function ensureConfig(cwd = process.cwd()) {
  const existing = join(cwd, "config.yaml");
  if (existsSync(existing)) return { path: existing, generated: false };
  const dir = mkdtempSync(join(tmpdir(), "kindle2pdf-"));
  try {
    const tmpConfig = join(dir, "config.yaml");
    writeFileSync(tmpConfig, renderMinimalConfig(), "utf8");
    return { path: tmpConfig, generated: true };
  } catch (err) {
    // 書き込み失敗（ディスクフル等）時は作った temp ディレクトリを片付けてから送出する。
    // generated=true を返せず呼び出し側の finally も掃除しないため、ここで leak を防ぐ。
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* 片付け失敗は致命でない（OS の tmp が後で回収する） */
    }
    throw err;
  }
}
