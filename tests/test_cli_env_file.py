import os
import tempfile
import pytest

from pocketscope import cli

@pytest.mark.asyncio
async def test_cli_env_file_loading():
    fd, path = tempfile.mkstemp(prefix="ps_env_", suffix=".env")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("# comment\nFOO=BAR\nexport BAZ=QUX\nEMPTY=\n")
        assert "FOO" not in os.environ or os.environ.get("FOO") != "BAR"
        argv = ["--headless", "--playback", "sample_data/demo_adsb.jsonl", "--env-file", path]
        await cli.run_async(argv)
        assert os.environ.get("FOO") == "BAR"
        assert os.environ.get("BAZ") == "QUX"
        # EMPTY should be set but empty string
        assert "EMPTY" in os.environ
        assert os.environ.get("EMPTY") == ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
