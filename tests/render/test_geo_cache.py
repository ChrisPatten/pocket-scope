from __future__ import annotations

from pocketscope.render.geo_cache import global_cache


def _dummy_builder_factory(seq):  # type: ignore[no-untyped-def]
    def _build():  # type: ignore[no-untyped-def]
        return seq

    return _build


def test_cache_center_shift_threshold():
    gc = global_cache()
    e1 = gc.get_or_build(
        layer="sectors",
        obj_id="A",
        range_nm=10.0,
        rotation_deg=0.0,
        center_lat=10.0,
        center_lon=20.0,
        display_px=(800, 600),
        m_per_px=1000.0,
        build_fn=_dummy_builder_factory([[(-1, -1), (0, 0)]]),
    )
    e2 = gc.get_or_build(
        layer="sectors",
        obj_id="A",
        range_nm=10.0,
        rotation_deg=0.0,
        center_lat=10.0005,
        center_lon=20.0,
        display_px=(800, 600),
        m_per_px=1000.0,
        build_fn=_dummy_builder_factory([[(-1, -1), (0, 0)]]),
    )
    assert e1 is e2
    e3 = gc.get_or_build(
        layer="sectors",
        obj_id="A",
        range_nm=10.0,
        rotation_deg=0.0,
        center_lat=10.03,
        center_lon=20.0,
        display_px=(800, 600),
        m_per_px=1000.0,
        build_fn=_dummy_builder_factory([[(-1, -1), (0, 0)]]),
    )
    assert e3 is not e1


def test_cache_rotation_bucket_invalidation():
    gc = global_cache()
    e0 = gc.get_or_build(
        layer="sectors",
        obj_id="B",
        range_nm=10.0,
        rotation_deg=0.4,
        center_lat=1.0,
        center_lon=2.0,
        display_px=(100, 100),
        m_per_px=500.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    e1 = gc.get_or_build(
        layer="sectors",
        obj_id="B",
        range_nm=10.0,
        rotation_deg=0.6,
        center_lat=1.0,
        center_lon=2.0,
        display_px=(100, 100),
        m_per_px=500.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    assert e0 is not e1


def test_cache_range_bucket_invalidation():
    gc = global_cache()
    e0 = gc.get_or_build(
        layer="states",
        obj_id="C",
        range_nm=10.2,
        rotation_deg=0.0,
        center_lat=0.0,
        center_lon=0.0,
        display_px=(50, 50),
        m_per_px=100.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    e1 = gc.get_or_build(
        layer="states",
        obj_id="C",
        range_nm=11.0,
        rotation_deg=0.0,
        center_lat=0.0,
        center_lon=0.0,
        display_px=(50, 50),
        m_per_px=100.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    assert e0 is not e1


def test_cache_display_resize_invalidation():
    gc = global_cache()
    e0 = gc.get_or_build(
        layer="states",
        obj_id="D",
        range_nm=5.0,
        rotation_deg=0.0,
        center_lat=0.0,
        center_lon=0.0,
        display_px=(100, 100),
        m_per_px=50.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    e1 = gc.get_or_build(
        layer="states",
        obj_id="D",
        range_nm=5.0,
        rotation_deg=0.0,
        center_lat=0.0,
        center_lon=0.0,
        display_px=(120, 100),
        m_per_px=50.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    assert e0 is not e1


def test_cache_disable_env(monkeypatch):
    monkeypatch.setenv("POCKETSCOPE_DISABLE_GEO_CACHE", "1")
    gc = global_cache()
    e0 = gc.get_or_build(
        layer="sectors",
        obj_id="E",
        range_nm=1.0,
        rotation_deg=0.0,
        center_lat=0.0,
        center_lon=0.0,
        display_px=(10, 10),
        m_per_px=10.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    e1 = gc.get_or_build(
        layer="sectors",
        obj_id="E",
        range_nm=1.0,
        rotation_deg=0.0,
        center_lat=0.0,
        center_lon=0.0,
        display_px=(10, 10),
        m_per_px=10.0,
        build_fn=_dummy_builder_factory([[(0, 0)]]),
    )
    assert e0 is not e1 and e0.meta.get("bypass") and e1.meta.get("bypass")
