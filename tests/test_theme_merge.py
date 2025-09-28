import textwrap
from pathlib import Path
import yaml

from pocketscope.theme import _merge_theme_data


def test_theme_merge_palette_patch_and_enum_replace(tmp_path: Path, monkeypatch):
    base = {
        "themes": {
            "example": {
                "palette": {"a": "#000000", "b": "#111111", "c": "#222222"},
                "track_speed_scale": {
                    "low": {"value": 0, "color": "#000000"},
                    "stops": [
                        {"value": 100, "color": "#101010"},
                        {"value": 200, "color": "#202020"},
                    ],
                    "high": {"value": 300, "color": "#303030"},
                },
                "nested": {"x": 1, "y": {"m": 2, "n": 3}},
            }
        }
    }
    override = {
        "themes": {
            "example": {
                # patch palette: add d, override b only
                "palette": {"b": "#BBBBBB", "d": "#DDDDDD"},
                # replace entire stops enumeration with single entry list
                "track_speed_scale": {
                    "stops": [{"value": 150, "color": "#AAAAAA"}],
                    # add new high to ensure other keys remain / merge recurses
                    "high": {"value": 400, "color": "#404040"},
                },
                # deep merge nested (retain x, patch nested.y.n, add nested.y.p)
                "nested": {"y": {"n": 30, "p": 99}},
            }
        }
    }

    merged = _merge_theme_data(base, override)
    theme = merged["themes"]["example"]
    # Palette retains a & c, overrides b, adds d
    assert theme["palette"] == {
        "a": "#000000",
        "b": "#BBBBBB",
        "c": "#222222",
        "d": "#DDDDDD",
    }
    # Enumeration list replaced completely (only one stop now)
    assert theme["track_speed_scale"]["stops"] == [
        {"value": 150, "color": "#AAAAAA"}
    ]
    # Low preserved from base (not overridden); high overridden
    assert theme["track_speed_scale"]["low"] == {"value": 0, "color": "#000000"}
    assert theme["track_speed_scale"]["high"] == {"value": 400, "color": "#404040"}
    # Deep merge of nested mapping
    assert theme["nested"] == {"x": 1, "y": {"m": 2, "n": 30, "p": 99}}
