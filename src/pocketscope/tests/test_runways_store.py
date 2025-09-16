import os
import tempfile

from pocketscope.data.base_map import BaseMap, get_runways_for_airports


def test_build_and_query_runways():
    here = os.path.dirname(__file__)
    geo = os.path.join(here, "..", "assets", "runways.json")
    geo = os.path.normpath(geo)
    assert os.path.exists(geo)
    td = tempfile.TemporaryDirectory()
    sqlite_path = os.path.join(td.name, "runways.sqlite")
    bm = BaseMap(sqlite_path)
    bm.ingest_runways(geo)
    assert os.path.exists(sqlite_path)
    # Query a few idents (may be empty but should return mapping)
    res = get_runways_for_airports(sqlite_path, ["KJFK", "KSFO", "EGLL"])
    assert isinstance(res, dict)
    td.cleanup()
