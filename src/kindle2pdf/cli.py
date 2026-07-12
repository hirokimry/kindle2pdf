"""click 製 CLI エントリポイント。

    kindle2pdf calibrate --config config.yaml   # region実測補助（1枚撮って枠を確認）
    kindle2pdf run       --config config.yaml   # capture→preprocess→ocr→build 全自動
    kindle2pdf capture   --config config.yaml   # 段別実行も可（レジューム対応）
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

import click

from .config import Config
from .state import State


@contextmanager
def _friendly_errors():
    """ドメイン例外を CLI の明確なエラー（exit 1 + メッセージ）に変換する。

    Why: auto_region 経路は Kindle 未起動・アクセシビリティ権限未付与・ウィンドウ不検出
    などを RuntimeError で送出する（初回実行で最も起きやすい失敗）。生の traceback ではなく
    click のエラーメッセージで返し、「誤クロップより明確なエラーで止める」設計を CLI 層でも守る。
    region 未設定などの ValueError も同様に扱う。
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


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
def calibrate(config: str) -> None:
    """撮影領域 region を実測するための補助（1枚撮って枠を確認）。[P1]"""
    from . import capture as capture_mod
    from .pipeline import book_dir

    with _friendly_errors():
        # config 読込(廃止キー等の ValueError)・region 未設定・auto_region の検出失敗
        # (RuntimeError)を、全て明確なエラーで返す（生 traceback にしない）。
        # calibrate は 1 冊分の枠確認なので、個別 run ではなく book_dir 直下に保存する。
        cfg = _load(config)
        out_path, region = capture_mod.run_calibrate(cfg, book_dir(cfg))
    x, y, w, h = region
    # 生の config 値ではなく実際に撮影に使った正規化済み region を表示する。
    click.echo(f"✅ region [{x}, {y}, {w}, {h}] を 1 枚撮影しました。")
    click.echo(f"📄 保存先: {out_path}")
    click.echo("👀 画像を開き、UI・柱・余白が入らず本文だけが写っているか確認してください。")


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
def run(config: str) -> None:
    """capture→preprocess→ocr→build を全自動実行（レジューム対応）。[P7]

    撮影ごとに work/<book_title>/<日時>/ の専用ディレクトリを切り、state もその中に置く。
    未完了の撮影があれば自動で続きから再開し、無ければ新規ディレクトリを作る（Issue #31）。
    """
    from .pipeline import output_path
    from .pipeline import run as run_pipeline

    with _friendly_errors():
        cfg = _load(config)
        run_dir = run_pipeline(cfg)
    click.echo(f"✅ 完了しました: {output_path(cfg, run_dir)}")


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
        capture_mod.run_capture(cfg, st, run_dir, state_path)


if __name__ == "__main__":
    main()
