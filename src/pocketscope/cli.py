"""Command-line interface for PocketScope.

This module adapts the example `examples.live_view` into a proper package
entrypoint. Keeping CLI parsing close to the example ensures behavior
stays consistent when invoked via the console script or ``python -m
pocketscope``.
"""

from __future__ import annotations

import argparse
import asyncio

from pocketscope import __version__
from pocketscope.app import live_view


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Return parsed arguments using the example's parser helper.

    The example defines a `parse_args` function; reuse it so the
    CLI flags stay in sync.
    """
    # Delegate to the app parser (supports argv for testing)
    if argv is None:
        return live_view.parse_args()
    # argparse by default reads from sys.argv; when argv provided, parse manually
    return live_view.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Synchronous entrypoint for the PocketScope CLI.

    This function parses arguments and runs the asynchronous application
    loop from :mod:`pocketscope.examples.live_view`.
    """
    # Get parsed args (example handles defaults and validation)
    args = parse_args(argv)

    # Support a top-level --version
    if hasattr(args, "version") and args.version:
        print(f"PocketScope {__version__}")
        return

    # Run the async runner using asyncio.run when not already in an event loop
    try:
        asyncio.run(run_async(argv))
    except KeyboardInterrupt:
        # Allow graceful cancellation via Ctrl+C
        pass


async def run_async(argv: list[str] | None = None) -> None:
    """Async entrypoint for programmatic usage/testing.

    Tests and programmatic callers can `await run_async(...)` to run the
    application without starting a nested event loop.
    """
    args = parse_args(argv)
    # Support a top-level --version
    if hasattr(args, "version") and args.version:
        print(f"PocketScope {__version__}")
        return

    # Choose headless or regular async entrypoint from the app package
    if getattr(args, "headless", False):
        await live_view._main_headless_async(args)
    else:
        await live_view.main_async(args)


if __name__ == "__main__":
    main()
