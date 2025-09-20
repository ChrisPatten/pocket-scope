import pytest

from pocketscope.settings.schema import Settings


def test_autoscale_range_limits_valid():
    cfg = Settings(autoscale_min_range_nm=5.0, autoscale_max_range_nm=50.0)
    assert cfg.autoscale_min_range_nm == 5.0
    assert cfg.autoscale_max_range_nm == 50.0


def test_autoscale_range_limits_must_be_positive():
    with pytest.raises(ValueError):
        Settings(autoscale_min_range_nm=-1.0)
    with pytest.raises(ValueError):
        Settings(autoscale_max_range_nm=0)


def test_autoscale_range_min_not_above_max():
    with pytest.raises(ValueError):
        Settings(autoscale_min_range_nm=60.0, autoscale_max_range_nm=30.0)
