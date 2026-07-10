"""click 製 CLI エントリポイント。

    kindle2pdf calibrate --config config.yaml   # region実測補助（1枚撮って枠を確認）
    kindle2pdf run       --config config.yaml   # capture→preprocess→ocr→build 全自動
    kindle2pdf capture   --config config.yaml   # 段別実行も可（レジューム対応）
"""

from __future__ import annotations

import click

from .config import Config
from .state import State


@click.group()
@click.version_option(package_name="kindle2pdf")
def main() -> None:
    """Kindle本を検索可能PDF化するフル自動パイプライン。"""


def _load(config: str) -> Config:
    return Config.load(config)


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
def calibrate(config: str) -> None:
    """撮影領域 region を実測するための補助（1枚撮って枠を確認）。[P1]"""
    from . import capture as capture_mod
    from .pipeline import work_dir

    cfg = _load(config)
    try:
        out_path, region = capture_mod.run_calibrate(cfg, work_dir(cfg))
    except ValueError as e:
        # region 未設定・不正は利用者に明確なエラーとして返す（click が exit 1）。
        raise click.ClickException(str(e)) from e
    x, y, w, h = region
    # 生の config 値ではなく実際に撮影に使った正規化済み region を表示する。
    click.echo(f"✅ region [{x}, {y}, {w}, {h}] を 1 枚撮影しました。")
    click.echo(f"📄 保存先: {out_path}")
    click.echo("👀 画像を開き、UI・柱・余白が入らず本文だけが写っているか確認してください。")


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--state", "state_path", default="state.json", show_default=True)
def run(config: str, state_path: str) -> None:
    """capture→preprocess→ocr→build を全自動実行（レジューム対応）。[P7]"""
    from .pipeline import run as run_pipeline

    run_pipeline(_load(config), state_path)


@main.command()
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--state", "state_path", default="state.json", show_default=True)
def capture(config: str, state_path: str) -> None:
    """capture 段のみ実行（Kindle操作）。[P2/P3]"""
    from . import capture as capture_mod
    from .pipeline import work_dir

    cfg = _load(config)
    cfg.validate()
    st = State.load(state_path)
    capture_mod.run_capture(cfg, st, work_dir(cfg))


if __name__ == "__main__":
    main()
