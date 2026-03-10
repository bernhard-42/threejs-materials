"""Tests for threejs_materials.library — offline, no GPU."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from threejs_materials.library import (
    CACHE_DIR,
    Material,
    _cache_path,
)


# ---------------------------------------------------------------------------
# _cache_path
# ---------------------------------------------------------------------------


class TestCachePath:
    def test_with_resolution(self):
        p = _cache_path("ambientcg", "Brick Wall", "1K")
        assert p == CACHE_DIR / "ambientcg_brick_wall_1k.json"

    def test_without_resolution(self):
        p = _cache_path("physicallybased", "Gold", None)
        assert p == CACHE_DIR / "physicallybased_gold.json"

    def test_name_normalization(self):
        p = _cache_path("gpuopen", "Some Material Name", "2K")
        assert "some_material_name" in p.name


# ---------------------------------------------------------------------------
# Material construction and serialization
# ---------------------------------------------------------------------------


def _sample_data(**overrides):
    base = {
        "id": "test_mat",
        "name": "Test Material",
        "source": "ambientcg",
        "url": "https://example.com",
        "license": "CC0",
        "properties": {
            "color": {"value": [1.0, 0.0, 0.0]},
            "roughness": {"value": 0.5},
        },
    }
    base.update(overrides)
    return base


class TestMaterial:
    def test_init(self):
        mat = Material(_sample_data())
        assert mat.id == "test_mat"
        assert mat.name == "Test Material"
        assert mat.source == "ambientcg"
        assert mat.url == "https://example.com"
        assert mat.license == "CC0"
        assert "color" in mat.properties

    def test_to_dict(self):
        mat = Material(_sample_data())
        d = mat.to_dict()
        assert d["id"] == "test_mat"
        assert d["properties"]["roughness"]["value"] == 0.5
        assert "colorOverride" not in d
        assert "textureRepeat" not in d

    def test_to_json(self):
        mat = Material(_sample_data())
        j = mat.to_json()
        parsed = json.loads(j)
        assert parsed["name"] == "Test Material"

    def test_to_json_kwargs(self):
        mat = Material(_sample_data())
        j = mat.to_json(indent=None)
        assert "\n" not in j

    def test_repr(self):
        mat = Material(_sample_data())
        r = repr(mat)
        assert "Test Material" in r
        assert "ambientcg" in r
        assert "color:" in r

    def test_repr_with_texture(self):
        data = _sample_data()
        data["properties"]["color"]["texture"] = "data:image/png;base64," + "A" * 100
        mat = Material(data)
        r = repr(mat)
        assert "texture=" in r
        # Long base64 should be truncated in repr
        assert "..." in r

    def test_getitem(self):
        mat = Material(_sample_data())
        assert mat["name"] == "Test Material"
        assert mat["source"] == "ambientcg"

    def test_contains(self):
        mat = Material(_sample_data())
        assert "name" in mat
        assert "properties" in mat
        assert "nonexistent" not in mat

    def test_source_loaders_exist(self):
        assert repr(Material.ambientcg) == "Material.ambientcg"
        assert repr(Material.gpuopen) == "Material.gpuopen"
        assert repr(Material.polyhaven) == "Material.polyhaven"
        assert repr(Material.physicallybased) == "Material.physicallybased"


# ---------------------------------------------------------------------------
# Material.override
# ---------------------------------------------------------------------------


class TestOverride:
    def test_color_override(self):
        mat = Material(_sample_data())
        new = mat.override(color=(0.1, 0.2, 0.3))
        assert new.properties["color"]["value"] == [0.1, 0.2, 0.3]
        # original unchanged
        assert mat.properties["color"]["value"] == [1.0, 0.0, 0.0]

    def test_repeat_override(self):
        mat = Material(_sample_data())
        new = mat.override(repeat=(3, 3))
        assert new.texture_repeat == (3, 3)

    def test_any_property(self):
        mat = Material(_sample_data())
        new = mat.override(roughness=0.9)
        assert new.properties["roughness"]["value"] == 0.9
        assert mat.properties["roughness"]["value"] == 0.5  # original unchanged

    def test_new_property(self):
        mat = Material(_sample_data())
        new = mat.override(metalness=1.0)
        assert new.properties["metalness"]["value"] == 1.0
        assert "metalness" not in mat.properties  # original unchanged

    def test_multiple_properties(self):
        mat = Material(_sample_data())
        new = mat.override(color=(0.5, 0.5, 0.5), roughness=0.2, repeat=(2, 4))
        assert new.properties["color"]["value"] == [0.5, 0.5, 0.5]
        assert new.properties["roughness"]["value"] == 0.2
        assert new.texture_repeat == (2, 4)

    def test_fluent_chaining(self):
        mat = Material(_sample_data())
        new = mat.override(color=(0.1, 0.2, 0.3)).override(repeat=(5, 5))
        assert new.properties["color"]["value"] == [0.1, 0.2, 0.3]
        assert new.texture_repeat == (5, 5)

    def test_fluent_chaining_properties(self):
        mat = Material(_sample_data())
        new = mat.override(color=(0.1, 0.2, 0.3)).override(roughness=0.1)
        assert new.properties["color"]["value"] == [0.1, 0.2, 0.3]
        assert new.properties["roughness"]["value"] == 0.1

    def test_preserves_textures(self):
        data = _sample_data()
        data["properties"]["color"]["texture"] = "data:image/png;base64,abc"
        mat = Material(data)
        new = mat.override(roughness=0.1)
        assert new.properties["color"]["texture"] == "data:image/png;base64,abc"

    def test_color_override_removes_texture_and_warns(self):
        data = _sample_data()
        data["properties"]["color"]["texture"] = "data:image/png;base64,abc"
        mat = Material(data)

        with pytest.warns(UserWarning, match="color texture removed"):
            red = mat.override(color=(0.5, 0.0, 0.0))
        assert red.properties["color"]["value"] == [0.5, 0.0, 0.0]
        assert "texture" not in red.properties["color"]

    def test_color_override_without_texture_sets_value(self):
        mat = Material(_sample_data())
        new = mat.override(color=(0.5, 0.0, 0.0))
        assert new.properties["color"]["value"] == [0.5, 0.0, 0.0]
        assert "texture" not in new.properties["color"]

    def test_color_override_preserves_original_texture(self):
        data = _sample_data()
        data["properties"]["color"]["texture"] = "data:image/png;base64,abc"
        mat = Material(data)

        with pytest.warns(UserWarning):
            mat.override(color=(0.5, 0.0, 0.0))
        # Original must be unchanged
        assert mat.properties["color"]["texture"] == "data:image/png;base64,abc"

    def test_to_dict_includes_repeat(self):
        mat = Material(_sample_data()).override(repeat=(2, 2))
        d = mat.to_dict()
        assert d["textureRepeat"] == [2, 2]

    def test_to_dict_reflects_property_override(self):
        mat = Material(_sample_data()).override(color=(1, 0, 0))
        d = mat.to_dict()
        assert d["properties"]["color"]["value"] == [1, 0, 0]


# ---------------------------------------------------------------------------
# Material.clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clear_all(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "gpuopen_wood_2k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.library.CACHE_DIR", cache)

        count = Material.clear_cache()
        assert count == 2
        assert not cache.exists()

    def test_clear_by_source(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "gpuopen_wood_2k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.library.CACHE_DIR", cache)

        count = Material.clear_cache(source="ambientcg")
        assert count == 1
        assert (cache / "gpuopen_wood_2k.json").exists()

    def test_clear_by_name(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "ambientcg_wood_1k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.library.CACHE_DIR", cache)

        count = Material.clear_cache(name="brick")
        assert count == 1
        assert (cache / "ambientcg_wood_1k.json").exists()

    def test_clear_by_name_and_source(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "gpuopen_brick_2k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.library.CACHE_DIR", cache)

        count = Material.clear_cache(name="brick", source="ambientcg")
        assert count == 1
        assert (cache / "gpuopen_brick_2k.json").exists()

    def test_clear_nonexistent_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("threejs_materials.library.CACHE_DIR", tmp_path / "nope")
        assert Material.clear_cache() == 0
