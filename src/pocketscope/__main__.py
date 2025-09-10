"""Console entrypoint for the pocketscope application.

This module delegates to :mod:`pocketscope.cli` so that running
``python -m pocketscope`` or the installed ``pocketscope`` console script
executes the same application code.
"""

from __future__ import annotations

from pocketscope.cli import main as cli_main


def main() -> None:
    """Application entrypoint (delegates to :func:`pocketscope.cli.main`)."""
    cli_main()


if __name__ == "__main__":
    main()
