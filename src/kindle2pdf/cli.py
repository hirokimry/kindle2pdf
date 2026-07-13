"""click 製 CLI エントリポイント。

    kindle2pdf calibrate --config config.yaml   # region実測補助（1枚撮って枠を確認）
    kindle2pdf run       --config config.yaml   # capture→preprocess→ocr→build 全自動
    kindle2pdf capture   --config config.yaml   # 段別実行も可（レジューム対応）
"""

from __future__ import annotations

import logging
import subprocess
from contextlib import contextmanager, nullcontext

import click

from .config import Config
from .state import State


@contextmanager
def _friendly_errors():
    """ドメイン例外を CLI の明確なエラー（exit 1 + メッセージ）に変換する。

    Why: ウィンドウ自動検出は Kindle 未起動・アクセシビリティ権限未付与・ウィンドウ不検出
    などを RuntimeError で送出する（初回実行で最も起きやすい失敗）。生の traceback ではなく
    click のエラーメッセージで返し、「誤クロップより明確なエラーで止める」設計を CLI 層でも守る。
    config 廃止キーなどの ValueError も同様に扱う。
    """
    try:
        yield
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


def _setup_logging() -> None:
    """各段の進捗 INFO ログを標準エラーへ出す（未設定なら INFO で初期化）。

    basicConfig は既存ハンドラがあれば no-op なので、ライブラリ利用時（テスト等）の
    ログ設定を壊さない。CLI 実行時にだけ INFO 進捗が可視化される。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@click.group()
@click.version_option(package_name="kindle2pdf")
def main() -> None:
    """Kindle本を検索可能PDF化するフル自動パイプライン。"""
    _setup_logging()


def _load(config: str) -> Config:
    return Config.load(config)


def _open_file(path: str) -> None:
    """生成した PDF を OS の既定アプリで開く（macOS: `open`）。

    Why: 撮影完了後にフロント／利用者がすぐ結果を確認できるようにする（Issue #32）。
    open が無い環境やエラーは握り潰す。PDF 生成自体は成功済みで、開けないことは致命でない。
    """
    try:
        subprocess.run(["open", path], check=False)
    except OSError:
        pass


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
def calibrate(config: str) -> None:
    """撮影領域を確認するための補助（1枚撮って枠を確認）。[P1]"""
    from . import capture as capture_mod
    from .pipeline import book_dir

    with _friendly_errors():
        # config 読込(廃止キー等の ValueError)・ウィンドウ自動検出の失敗(RuntimeError)を、
        # 全て明確なエラーで返す（生 traceback にしない）。
        cfg = _load(config)
        # book_dir(cfg) は work/<book_title>/ を作る。book_title に / .. が混ざると work/ の
        # 外へ書き込みうるため、run/capture と同じく撮影前に validate で弾く（#32）。
        cfg.validate()
        # calibrate は 1 冊分の枠確認なので、個別 run ではなく book_dir 直下に保存する。
        out_path, region = capture_mod.run_calibrate(cfg, book_dir(cfg))
    x, y, w, h = region
    # 生の config 値ではなく実際に撮影に使った正規化済み region を表示する。
    click.echo(f"✅ region [{x}, {y}, {w}, {h}] を 1 枚撮影しました。")
    click.echo(f"📄 保存先: {out_path}")
    click.echo("👀 画像を開き、UI・柱・余白が入らず本文だけが写っているか確認してください。")


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
@click.option(
    "--title",
    default=None,
    help="本タイトル（config.yaml の book_title を上書き）。フロントから渡す想定。",
)
@click.option(
    "--reading-order",
    type=click.Choice(["rtl", "ltr"]),
    default=None,
    help=(
        "見開き(2カラム)の読み順。rtl=右→左(漫画/縦書き) ltr=左→右(横書き)。"
        "片ページ表示なら結果に影響しない。片ページ/見開きは Kindle ウィンドウ幅で選ぶ。"
    ),
)
@click.option(
    "--open/--no-open",
    "open_pdf",
    default=True,
    show_default=True,
    help="完了時に生成PDFを開く（--no-open で抑制）。",
)
@click.option(
    "--progress",
    "progress_mode",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="進捗出力。text=人間向けログ / json=1行1イベントのJSON Lines（フロント連携用）。",
)
@click.option(
    "--resume/--no-resume",
    default=True,
    show_default=True,
    help="未完了の撮影があれば続きから再開する。--no-resume で未完了を無視し常に新規 run を作る。",
)
def run(
    config: str,
    title: str | None,
    reading_order: str | None,
    open_pdf: bool,
    progress_mode: str,
    resume: bool,
) -> None:
    """capture→preprocess→ocr→build を全自動実行（レジューム対応）。[P7]

    撮影ごとに work/<book_title>/<日時>/ の専用ディレクトリを切り、state もその中に置く。
    未完了の撮影があれば自動で続きから再開し、無ければ新規ディレクトリを作る（Issue #31）。

    フラグ（--title / --reading-order / --no-open / --progress）で対話なしに実行でき、
    対話フロント（npx kindle2pdf）はこの経路に答えを渡す。--config による無対話フル制御は
    従来どおり維持され、フラグ未指定なら config.yaml の値がそのまま使われる（Issue #32）。
    """
    from . import progress as progress_mod
    from .pipeline import output_path
    from .pipeline import run as run_pipeline

    with _friendly_errors():
        cfg = _load(config)
        # フラグは config.yaml への上書き。未指定（None）なら config の値を尊重する。
        if title is not None:
            cfg.book_title = title
        if reading_order is not None:
            cfg.ocr.reading_order = reading_order
        # --progress json のときだけ機械可読シンクを差し込む。text は従来ログ経路のまま。
        sink = progress_mod.json_lines() if progress_mode == "json" else nullcontext()
        with sink:
            # --no-resume は未完了 run を無視して常に新規 run を作る（ウィザードの再開拒否経路）。
            run_dir = run_pipeline(cfg, resume=resume)
        out_path = output_path(cfg, run_dir)
    # 完了PDFを自動で開く（--no-open で抑制）。エラーは _open_file 側で握り潰す。
    if open_pdf:
        _open_file(str(out_path))
    click.echo(f"✅ 完了しました: {out_path}")


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
def capture(config: str) -> None:
    """capture 段のみ実行（Kindle操作）。[P2/P3]"""
    from . import capture as capture_mod
    from .pipeline import resolve_run_dir

    with _friendly_errors():
        # config 読込・検証・撮影の各段が投げるドメイン例外を明確なエラーで返す。
        cfg = _load(config)
        cfg.validate()
        # 未完了 run があれば継続、無ければ新規 run ディレクトリを作る（run と同じ規約）。
        run_dir = resolve_run_dir(cfg)
        state_path = run_dir / "state.json"
        st = State.load(state_path)
        # run 同様、run_capture が最初の state.save に到達する前（Kindle 未起動等）に
        # 落ちても、この run が次回 resolve_run_dir で再開対象になるよう初期 state を
        # 即時永続化する。これがないと capture 単体経由で空 run ディレクトリが積み上がる（#31）。
        if not state_path.exists():
            st.save(state_path)
        capture_mod.run_capture(cfg, st, run_dir, state_path)


if __name__ == "__main__":
    main()
