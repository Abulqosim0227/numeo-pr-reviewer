from enum import Enum

import typer
from dotenv import load_dotenv

from .agent import run

app = typer.Typer(add_completion=False, no_args_is_help=True)


class Mode(str, Enum):
    conservative = "conservative"
    aggressive = "aggressive"


@app.command()
def review(
    pr_url: str = typer.Argument(..., help="Full GitHub PR URL"),
    mode: Mode = typer.Option(Mode.conservative, "--mode", "-m", help="Review bias"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip GitHub writes"),
) -> None:
    load_dotenv()
    code = run(pr_url, mode.value, dry_run=dry_run)
    raise typer.Exit(code)


if __name__ == "__main__":
    app()
