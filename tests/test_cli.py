import pytest

from pocketscope import cli


def test_cli_headless_help():
    # Ensure parse_args handles headless and help generation doesn't raise
    args = cli.parse_args(["--headless", "--playback", "sample_data/demo_adsb.jsonl"])
    assert args.headless is True
    assert args.playback is not None


@pytest.mark.asyncio
async def test_cli_headless_run(tmp_path):
    # Run the headless main briefly with the sample playback file.
    argv = ["--headless", "--playback", "sample_data/demo_adsb.jsonl"]
    # Await the async runner which avoids nested event loop errors
    await cli.run_async(argv)
    # If no exception, consider it a pass
    assert True
