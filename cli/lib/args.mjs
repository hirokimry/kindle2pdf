// 対話ウィザードの回答を Python コア（python -m kindle2pdf run ...）の引数に変換する。
// @clack など UI 依存を持たない純粋ロジックなので node --test で単体検証できる（Issue #34）。

// ページ構成の選択肢。見開きの左右分割は #29/#30 で廃止され「1撮影=1ページ」。片ページ/
// 見開きは Kindle のウィンドウ幅で物理的に選ぶため、コア側で効くのは見開き時の読み順
// (--reading-order) だけ。よって single は読み順不問、spread は rtl/ltr に対応させる（#32/#34）。
export const PAGE_LAYOUTS = [
  {
    value: "single",
    label: "片ページ（横書き・文章系）",
    hint: "Kindle ウィンドウを半分幅に",
    readingOrder: null,
  },
  {
    value: "spread-rtl",
    label: "見開き（縦書き漫画・右→左）",
    hint: "Kindle ウィンドウを全画面幅に",
    readingOrder: "rtl",
  },
  {
    value: "spread-ltr",
    label: "見開き（横書き・左→右）",
    hint: "Kindle ウィンドウを全画面幅に",
    readingOrder: "ltr",
  },
];

// 本タイトルの早期検証。コア側 Config.validate() と同じ規則（パス区切り / 相対参照 /
// 空文字を弾く）にして、ウィザードで早めに気づけるようにする（Issue #32 の検証と一致・#34）。
// 問題なければ undefined、あればエラーメッセージ文字列を返す（clack の validate 規約）。
export function validateTitle(value) {
  const v = (value ?? "").trim();
  if (v === "") return "タイトルを入力してください";
  if (/[\\/]/.test(v)) return "タイトルに / や \\ は使えません";
  if (v === "." || v === "..") return "タイトルに . や .. は使えません";
  // null byte はコア Config.validate() も弾く。パス操作を壊す不正文字として拒否する。
  if (v.includes("\x00")) return "タイトルに不正な文字が含まれます";
  return undefined;
}

// レイアウト選択値 → コアの --reading-order 値（single は null=フラグ不要）。
export function readingOrderFor(layoutValue) {
  const found = PAGE_LAYOUTS.find((l) => l.value === layoutValue);
  return found ? found.readingOrder : null;
}

// ウィザードの回答からコア `run` の argv を組み立てる。
// title 未指定/空はフラグを付けない（コアが config 値を尊重する）。progress は既定 json
// （フロントがスピナー描画のため機械可読で受け取る）。
export function buildCoreArgs({
  title,
  layout,
  open = true,
  progress = "json",
  configPath,
  resume,
} = {}) {
  const args = ["run"];
  if (configPath) args.push("--config", configPath);
  // 検証（validateTitle）と一致させて trim 済みの値を渡す。前後空白付き（"  猫  "）で
  // work/  猫  / のようなフォルダ名にならないようにする（#34）。
  // 値は --title=<値> の単一トークンで渡す。"-1" のような - 始まりの書名でも click が
  // 次トークンを別オプションと誤認しない（別トークンだと "requires an argument" になる）。
  const trimmedTitle = title == null ? "" : String(title).trim();
  if (trimmedTitle !== "") args.push(`--title=${trimmedTitle}`);
  const ro = readingOrderFor(layout);
  if (ro) args.push("--reading-order", ro);
  args.push("--progress", progress);
  args.push(open ? "--open" : "--no-open");
  // 未完了 run の再開可否。true=再開 / false=新規強制。undefined はフラグを付けず
  // コア既定（再開）に委ねる（未完了 run が無い通常経路では新規になる）。#35
  if (resume === true) args.push("--resume");
  else if (resume === false) args.push("--no-resume");
  return args;
}
