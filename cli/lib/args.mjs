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
export function buildCoreArgs({ title, layout, open = true, progress = "json", configPath } = {}) {
  const args = ["run"];
  if (configPath) args.push("--config", configPath);
  if (title != null && String(title).trim() !== "") args.push("--title", String(title));
  const ro = readingOrderFor(layout);
  if (ro) args.push("--reading-order", ro);
  args.push("--progress", progress);
  args.push(open ? "--open" : "--no-open");
  return args;
}
