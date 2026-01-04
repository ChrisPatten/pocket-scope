import os
import pytest

from pocketscope import cli


def test_cli_headless_help():
    # Ensure parse_args handles headless and help generation doesn't raise
    args = cli.parse_args(["--headless", "--playback", "sample_data/demo_adsb.jsonl"])
    assert args.headless is True
    assert args.playback is not None


def test_cli_url_default():
    """Verify --url resolves to hardcoded default when not set."""
    args = cli.parse_args(["--headless", "--playback", "sample_data/demo_adsb.jsonl"])
    # Should fall back to hardcoded default when neither CLI nor env var set
    assert args.url == "http://127.0.0.1:8080/data/aircraft.json"


def test_cli_url_explicit():
    """Verify explicit --url overrides defaults."""
    args = cli.parse_args([
        "--headless",
        "--playback", "sample_data/demo_adsb.jsonl",
        "--url", "http://example.com/data/aircraft.json"
    ])
    assert args.url == "http://example.com/data/aircraft.json"


def test_cli_url_env_var():
    """Verify POCKETSCOPE_URL and ADSB_URL env vars are used when --url not set."""
    old_pocketscope = os.environ.get("POCKETSCOPE_URL")
    old_adsb = os.environ.get("ADSB_URL")
    try:
        # POCKETSCOPE_URL takes precedence
        os.environ["POCKETSCOPE_URL"] = "http://pocketscope.example.com/data/aircraft.json"
        os.environ.pop("ADSB_URL", None)
        args = cli.parse_args(["--headless", "--playback", "sample_data/demo_adsb.jsonl"])
        assert args.url == "http://pocketscope.example.com/data/aircraft.json"
        
        # ADSB_URL used as fallback
        os.environ.pop("POCKETSCOPE_URL", None)
        os.environ["ADSB_URL"] = "http://adsb.example.com/data/aircraft.json"
        args = cli.parse_args(["--headless", "--playback", "sample_data/demo_adsb.jsonl"])
        assert args.url == "http://adsb.example.com/data/aircraft.json"
    finally:
        if old_pocketscope is not None:
            os.environ["POCKETSCOPE_URL"] = old_pocketscope
        else:
            os.environ.pop("POCKETSCOPE_URL", None)
        if old_adsb is not None:
            os.environ["ADSB_URL"] = old_adsb
        else:
            os.environ.pop("ADSB_URL", None)


def test_cli_file_argument_parsed():
    """Ensure --file is accepted and parsed."""
    args = cli.parse_args(["--headless", "--file", "/tmp/aircraft.json"])
    assert args.file == "/tmp/aircraft.json"
    # URL default should still resolve for completeness
    assert args.url == "http://127.0.0.1:8080/data/aircraft.json"


@pytest.mark.asyncio
async def test_source_precedence_playback_over_file(monkeypatch):
    """Playback should win over file when both are provided."""
    from pocketscope.app import live_view

    calls: dict[str, bool] = {}

    class DummyPlayback:
        def __init__(self, *args, **kwargs) -> None:
            calls["playback_init"] = True

        async def run(self) -> None:
            calls["playback_run"] = True

        async def stop(self) -> None:
            calls["playback_stop"] = True

    def _fail_file_source(*args, **kwargs):
        raise AssertionError("LocalJsonFileSource should not be constructed when playback is set")

    monkeypatch.setattr(live_view, "FilePlaybackSource", DummyPlayback)
    monkeypatch.setattr(live_view, "LocalJsonFileSource", _fail_file_source)

    await cli.run_async(["--headless", "--playback", "sample_data/demo_adsb.jsonl", "--file", "/tmp/aircraft.json"])

    assert calls.get("playback_init") is True
    assert calls.get("playback_run") is True
    assert calls.get("playback_stop") is True


@pytest.mark.asyncio
async def test_source_uses_file_when_provided(monkeypatch):
    """File source should be chosen when no playback is provided."""
    from pocketscope.app import live_view

    calls: dict[str, bool] = {}

    class DummyFileSource:
        def __init__(self, path: str, *args, **kwargs) -> None:
            calls["file_init"] = True
            calls["file_path"] = path

        async def run(self) -> None:
            calls["file_run"] = True

        async def stop(self) -> None:
            calls["file_stop"] = True

    def _fail_dump(*args, **kwargs):
        raise AssertionError("Dump1090JsonSource should not be constructed when file is set")

    monkeypatch.setattr(live_view, "LocalJsonFileSource", DummyFileSource)
    monkeypatch.setattr(live_view, "Dump1090JsonSource", _fail_dump)

    await cli.run_async(["--headless", "--file", "/tmp/aircraft.json"])

    assert calls.get("file_init") is True
    assert calls.get("file_path") == "/tmp/aircraft.json"
    assert calls.get("file_run") is True
    assert calls.get("file_stop") is True


@pytest.mark.asyncio
async def test_cli_headless_run(tmp_path):
    # Run the headless main briefly with the sample playback file.
    argv = ["--headless", "--playback", "sample_data/demo_adsb.jsonl"]
    # Await the async runner which avoids nested event loop errors
    await cli.run_async(argv)
    # If no exception, consider it a pass
    assert True
