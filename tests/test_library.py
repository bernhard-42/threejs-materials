"""Tests for threejs_materials.library — offline, no GPU."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from threejs_materials.library import PbrProperties
from threejs_materials.sources import CACHE_DIR, _cache_path


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
# PbrProperties construction and serialization
# ---------------------------------------------------------------------------


def _sample_data(**overrides):
    base = {
        "id": "test_mat",
        "name": "Test Material",
        "source": "ambientcg",
        "url": "https://example.com",
        "license": "CC0",
        "values": {
            "color": [1.0, 0.0, 0.0],
            "roughness": 0.5,
        },
        "textures": {},
    }
    base.update(overrides)
    return base


class TestPbrProperties:
    def test_init(self):
        mat = PbrProperties.from_dict(_sample_data())
        assert mat.id == "test_mat"
        assert mat.name == "Test Material"
        assert mat.source == "ambientcg"
        assert mat.url == "https://example.com"
        assert mat.license == "CC0"
        assert mat.values.color is not None

    def test_to_dict(self):
        mat = PbrProperties.from_dict(_sample_data())
        d = mat.to_dict()
        assert d["id"] == "test_mat"
        assert d["values"]["roughness"] == 0.5
        assert "colorOverride" not in d
        assert "textureRepeat" not in d

    def test_to_json(self):
        mat = PbrProperties.from_dict(_sample_data())
        j = mat.to_json()
        parsed = json.loads(j)
        assert parsed["name"] == "Test Material"

    def test_to_json_kwargs(self):
        mat = PbrProperties.from_dict(_sample_data())
        j = mat.to_json(indent=None)
        assert "\n" not in j

    def test_repr(self):
        mat = PbrProperties.from_dict(_sample_data())
        r = repr(mat)
        assert "Test Material" in r
        assert "ambientcg" in r
        assert "PbrValues(" in r
        assert "color=" in r

    def test_repr_with_texture(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64," + "A" * 100
        mat = PbrProperties.from_dict(data)
        r = repr(mat)
        assert "data:...;base64,..." in r

    def test_dump_gltf(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64," + "A" * 100
        data["values"]["metalness"] = 0.9
        data["values"]["clearcoat"] = 0.8
        mat = PbrProperties.from_dict(data)
        r = mat.dump(gltf=True)
        assert "Test Material" in r
        assert "materials:" in r
        assert "'data:...;base64,...'" in r
        assert "metallicFactor:" in r

    def test_dump_json_threejs(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64," + "A" * 100
        mat = PbrProperties.from_dict(data)
        r = mat.dump(json_format=True)
        parsed = json.loads(r)
        assert parsed["name"] == "Test Material"
        assert parsed["textures"]["color"] == "data:image/png;base64,..."
        assert parsed["values"]["color"] == [1.0, 0.0, 0.0]

    def test_dump_json_gltf(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64," + "A" * 100
        data["values"]["metalness"] = 0.9
        mat = PbrProperties.from_dict(data)
        r = mat.dump(gltf=True, json_format=True)
        parsed = json.loads(r)
        assert "materials" in parsed
        m = parsed["materials"][0]
        assert m["pbrMetallicRoughness"]["metallicFactor"] == 0.9
        assert parsed["images"][0]["uri"] == "data:image/png;base64,..."

    def test_attribute_access(self):
        mat = PbrProperties.from_dict(_sample_data())
        assert mat.name == "Test Material"
        assert mat.source == "ambientcg"

    def test_has_attributes(self):
        mat = PbrProperties.from_dict(_sample_data())
        assert hasattr(mat, "name")
        assert hasattr(mat, "values")
        assert hasattr(mat, "maps")
        assert not hasattr(mat, "nonexistent")

    def test_source_loaders_exist(self):
        from threejs_materials.sources import (
            ambientcg_loader, gpuopen_loader, polyhaven_loader, physicallybased_loader,
        )
        assert repr(ambientcg_loader) == "_SourceLoader('ambientcg')"
        assert repr(gpuopen_loader) == "_SourceLoader('gpuopen')"
        assert repr(polyhaven_loader) == "_SourceLoader('polyhaven')"
        assert repr(physicallybased_loader) == "_SourceLoader('physicallybased')"


# ---------------------------------------------------------------------------
# PbrProperties.override
# ---------------------------------------------------------------------------


class TestOverride:
    def test_color_override(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.1, 0.2, 0.3))
        assert new.values.color == [0.1, 0.2, 0.3]
        # original unchanged
        assert mat.values.color == [1.0, 0.0, 0.0]

    def test_scale(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.scale(2, 2)
        assert new.texture_repeat == (0.5, 0.5)

    def test_scale_asymmetric(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.scale(4, 2)
        assert new.texture_repeat == (0.25, 0.5)

    def test_any_property(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(roughness=0.9)
        assert new.values.roughness == 0.9
        assert mat.values.roughness == 0.5  # original unchanged

    def test_new_property(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(metalness=1.0)
        assert new.values.metalness == 1.0
        assert mat.values.metalness is None  # original unchanged

    def test_multiple_properties(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.5, 0.5, 0.5), roughness=0.2).scale(2, 4)
        assert new.values.color == [0.5, 0.5, 0.5]
        assert new.values.roughness == 0.2
        assert new.texture_repeat == (0.5, 0.25)

    def test_fluent_chaining(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.1, 0.2, 0.3)).scale(5, 5)
        assert new.values.color == [0.1, 0.2, 0.3]
        assert new.texture_repeat == (0.2, 0.2)

    def test_fluent_chaining_properties(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.1, 0.2, 0.3)).override(roughness=0.1)
        assert new.values.color == [0.1, 0.2, 0.3]
        assert new.values.roughness == 0.1

    def test_preserves_textures(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64,abc"
        mat = PbrProperties.from_dict(data)
        new = mat.override(roughness=0.1)
        assert new.maps.color == "data:image/png;base64,abc"

    def test_color_override_removes_texture_and_warns(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64,abc"
        mat = PbrProperties.from_dict(data)

        with pytest.warns(UserWarning, match="color texture removed"):
            red = mat.override(color=(0.5, 0.0, 0.0))
        assert red.values.color == [0.5, 0.0, 0.0]
        assert red.maps.color is None

    def test_color_override_without_texture_sets_value(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.5, 0.0, 0.0))
        assert new.values.color == [0.5, 0.0, 0.0]
        assert new.maps.color is None

    def test_override_all_pbr_value_fields(self):
        """Every field in PbrValues should be overridable via override().
        Guards against drift between the dataclass and the method signature."""
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(
            color=(0.2, 0.3, 0.4),
            roughness=0.7,
            metalness=0.1,
            ior=1.45,
            transmission=0.8,
            opacity=0.9,
            transparent=True,
            alpha_test=0.5,
            clearcoat=0.5,
            clearcoat_roughness=0.2,
            sheen=1.0,
            sheen_color=(0.9, 0.8, 0.7),
            sheen_roughness=0.3,
            anisotropy=0.4,
            anisotropy_rotation=1.2,
            specular_intensity=0.6,
            specular_color=(1.0, 0.9, 0.8),
            emissive=(0.1, 0.1, 0.1),
            emissive_intensity=2.0,
            attenuation_color=(1.0, 0.8, 0.6),
            attenuation_distance=0.4,
            thickness=0.3,
            iridescence=1.0,
            iridescence_ior=1.3,
            iridescence_thickness_range=(100.0, 400.0),
            dispersion=0.5,
            normal_scale=(1.5, 1.5),
            displacement_scale=0.2,
            side=2,
        )
        assert new.values.color == [0.2, 0.3, 0.4]
        assert new.values.roughness == 0.7
        assert new.values.metalness == 0.1
        assert new.values.ior == 1.45
        assert new.values.transmission == 0.8
        assert new.values.opacity == 0.9
        assert new.values.transparent is True
        assert new.values.alpha_test == 0.5
        assert new.values.clearcoat == 0.5
        assert new.values.clearcoat_roughness == 0.2
        assert new.values.sheen == 1.0
        assert new.values.sheen_color == [0.9, 0.8, 0.7]
        assert new.values.sheen_roughness == 0.3
        assert new.values.anisotropy == 0.4
        assert new.values.anisotropy_rotation == 1.2
        assert new.values.specular_intensity == 0.6
        assert new.values.specular_color == [1.0, 0.9, 0.8]
        assert new.values.emissive == [0.1, 0.1, 0.1]
        assert new.values.emissive_intensity == 2.0
        assert new.values.attenuation_color == [1.0, 0.8, 0.6]
        assert new.values.attenuation_distance == 0.4
        assert new.values.thickness == 0.3
        assert new.values.iridescence == 1.0
        assert new.values.iridescence_ior == 1.3
        assert new.values.iridescence_thickness_range == [100.0, 400.0]
        assert new.values.dispersion == 0.5
        assert new.values.normal_scale == [1.5, 1.5]
        assert new.values.displacement_scale == 0.2
        assert new.values.side == 2

    def test_override_id_gets_variant_hash(self):
        """Overriding produces a distinct id so variants don't collapse
        when keyed by id (e.g., in a `{m.id: m}` dict for collect_gltf)."""
        mat = PbrProperties.from_dict(_sample_data())
        red = mat.override(color=(1.0, 0.0, 0.0))
        assert red.id != mat.id
        assert red.id.startswith(f"{mat.name}_")
        assert red.name == mat.name  # display name unchanged

    def test_override_id_is_deterministic(self):
        """Same override on same parent → identical id. Stable across runs."""
        mat = PbrProperties.from_dict(_sample_data())
        a = mat.override(color=(1.0, 0.0, 0.0))
        b = mat.override(color=(1.0, 0.0, 0.0))
        assert a.id == b.id

    def test_override_different_kwargs_different_ids(self):
        mat = PbrProperties.from_dict(_sample_data())
        red = mat.override(color=(1.0, 0.0, 0.0))
        green = mat.override(color=(0.0, 1.0, 0.0))
        rough = mat.override(roughness=0.9)
        # All four are distinguishable
        assert len({mat.id, red.id, green.id, rough.id}) == 4

    def test_override_chain_cascades(self):
        """Hash input includes parent id, so chained overrides produce
        fresh hashes that encode the full chain history."""
        mat = PbrProperties.from_dict(_sample_data())
        red = mat.override(color=(1.0, 0.0, 0.0))
        red_rough = red.override(roughness=0.1)
        # Chained override has a distinct id from the parent variant
        assert red_rough.id != red.id
        # Base name stem preserved — no suffix accumulation
        assert red_rough.id.startswith(f"{mat.name}_")
        # Structurally: "{name}_{8 hex}" (one suffix, not two)
        parts = red_rough.id.split("_")
        assert len(parts[-1]) == 8 and all(c in "0123456789abcdef" for c in parts[-1])

    def test_override_chain_vs_combined_differ(self):
        """A.override(color).override(rough) and A.override(color, rough)
        produce different ids because the chain hashes the intermediate id.
        User accepted this trade-off: 'ignore reverting properties'."""
        mat = PbrProperties.from_dict(_sample_data())
        chained = mat.override(color=(1, 0, 0)).override(roughness=0.1)
        combined = mat.override(color=(1, 0, 0), roughness=0.1)
        # Values are identical
        assert chained.values.color == combined.values.color
        assert chained.values.roughness == combined.values.roughness
        # But ids differ because the chain passes through an intermediate id
        assert chained.id != combined.id

    def test_override_noop_preserves_id(self):
        """override() with no kwargs returns a copy with the id unchanged."""
        mat = PbrProperties.from_dict(_sample_data())
        same = mat.override()
        assert same.id == mat.id

    def test_override_ids_collide_free_in_dict(self):
        """The whole point: keying a dict by `m.id` must keep variants
        distinct so collect_gltf_textures produces one material per variant."""
        mat = PbrProperties.from_dict(_sample_data())
        variants = [mat] + [
            mat.override(color=c) for c in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
        ]
        d = {m.id: m for m in variants}
        assert len(d) == len(variants)

    def test_scale_id_gets_variant_hash(self):
        """scale() changes how the material renders (UV tiling), so it
        must produce a distinct id — otherwise dict-keyed-by-id variants
        collide with the base material."""
        mat = PbrProperties.from_dict(_sample_data())
        s = mat.scale(2, 2)
        assert s.id != mat.id
        assert s.id.startswith(f"{mat.name}_")
        assert s.name == mat.name

    def test_scale_id_is_deterministic(self):
        mat = PbrProperties.from_dict(_sample_data())
        a = mat.scale(2, 2)
        b = mat.scale(2, 2)
        assert a.id == b.id

    def test_scale_different_factors_different_ids(self):
        mat = PbrProperties.from_dict(_sample_data())
        s22 = mat.scale(2, 2)
        s44 = mat.scale(4, 4)
        s24 = mat.scale(2, 4)
        assert len({mat.id, s22.id, s44.id, s24.id}) == 4

    def test_scale_fixed_flag_affects_id(self):
        """fixed=True vs fixed=False are different rendering modes and
        should produce different ids even with the same u/v."""
        mat = PbrProperties.from_dict(_sample_data())
        a = mat.scale(2, 2, fixed=True)
        b = mat.scale(2, 2, fixed=False)
        assert a.id != b.id

    def test_scale_after_override_cascades(self):
        """Chained override().scale() — chain passes through intermediate
        id, so the scale's hash encodes the chain history."""
        mat = PbrProperties.from_dict(_sample_data())
        red_scaled = mat.override(color=(1, 0, 0)).scale(2, 2)
        plain_scaled = mat.scale(2, 2)
        assert red_scaled.id != plain_scaled.id
        assert red_scaled.id.startswith(f"{mat.name}_")

    def test_mixed_override_scale_variants_collide_free(self):
        """End-to-end: override + scale mix still yields unique ids."""
        mat = PbrProperties.from_dict(_sample_data())
        variants = [
            mat,
            mat.override(color=(1, 0, 0)),
            mat.scale(2, 2),
            mat.scale(4, 4),
            mat.override(color=(1, 0, 0)).scale(2, 2),
            mat.scale(2, 2).override(color=(1, 0, 0)),
        ]
        d = {m.id: m for m in variants}
        assert len(d) == len(variants)

    def test_two_materials_with_duplicated_variants_dedupe_to_six(self):
        """Two base materials, three variants each (overrides + scale),
        every variant instantiated twice from the same overrides.
        12 total list entries → 6 unique ids after dedup by id."""
        mat_a = PbrProperties.from_dict(
            dict(_sample_data(), id="A", name="A")
        )
        mat_b = PbrProperties.from_dict(
            dict(_sample_data(), id="B", name="B")
        )

        def three_variants(m):
            return [
                m.override(color=(1, 0, 0)),
                m.override(roughness=0.1),
                m.scale(2, 2),
            ]

        # Build each variant twice — distinct object instances, same
        # kwargs, so determinism requires their ids to match.
        materials = (
            three_variants(mat_a) + three_variants(mat_a)
            + three_variants(mat_b) + three_variants(mat_b)
        )
        assert len(materials) == 12
        # Distinct instances (two-by-two)
        assert materials[0] is not materials[3]

        d = {m.id: m for m in materials}
        assert len(d) == 6

        # Each base produces a disjoint subset — no cross-contamination
        a_ids = {m.id for m in materials[:3]}
        b_ids = {m.id for m in materials[6:9]}
        assert a_ids.isdisjoint(b_ids)
        # All A-variant ids are prefixed by "A_", B-variant ids by "B_"
        assert all(i.startswith("A_") for i in a_ids)
        assert all(i.startswith("B_") for i in b_ids)

        # End-to-end: collect_gltf_textures on the deduped dict emits
        # exactly 6 glTF materials, not 12.
        from threejs_materials.gltf import collect_gltf_textures
        gltf = collect_gltf_textures(d)
        assert len(gltf.materials) == 6

    def test_color_override_preserves_original_texture(self):
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64,abc"
        mat = PbrProperties.from_dict(data)

        with pytest.warns(UserWarning):
            mat.override(color=(0.5, 0.0, 0.0))
        # Original must be unchanged
        assert mat.maps.color == "data:image/png;base64,abc"

    def test_to_dict_includes_repeat(self):
        mat = PbrProperties.from_dict(_sample_data()).scale(2, 2)
        d = mat.to_dict()
        assert d["textureRepeat"] == [0.5, 0.5]

    def test_to_dict_reflects_property_override(self):
        mat = PbrProperties.from_dict(_sample_data()).override(color=(1, 0, 0))
        d = mat.to_dict()
        assert d["values"]["color"] == [1, 0, 0]


# ---------------------------------------------------------------------------
# clear_cache / list_cache (in sources)
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clear_all(self, tmp_path, monkeypatch):
        from threejs_materials.sources import clear_cache
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "gpuopen_wood_2k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", cache)

        count = clear_cache()
        assert count == 2
        assert not cache.exists()

    def test_clear_by_source(self, tmp_path, monkeypatch):
        from threejs_materials.sources import clear_cache
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "gpuopen_wood_2k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", cache)

        count = clear_cache(source="ambientcg")
        assert count == 1
        assert (cache / "gpuopen_wood_2k.json").exists()

    def test_clear_by_name(self, tmp_path, monkeypatch):
        from threejs_materials.sources import clear_cache
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "ambientcg_wood_1k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", cache)

        count = clear_cache(name="brick")
        assert count == 1
        assert (cache / "ambientcg_wood_1k.json").exists()

    def test_clear_by_name_and_source(self, tmp_path, monkeypatch):
        from threejs_materials.sources import clear_cache
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "ambientcg_brick_1k.json").write_text("{}")
        (cache / "gpuopen_brick_2k.json").write_text("{}")
        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", cache)

        count = clear_cache(name="brick", source="ambientcg")
        assert count == 1
        assert (cache / "gpuopen_brick_2k.json").exists()

    def test_clear_nonexistent_cache(self, tmp_path, monkeypatch):
        from threejs_materials.sources import clear_cache
        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", tmp_path / "nope")
        assert clear_cache() == 0


# ---------------------------------------------------------------------------
# PbrProperties.to_gltf
# ---------------------------------------------------------------------------

# Tiny 1x1 PNG helper (reuse from conftest)
from conftest import _make_1x1_png
import base64


def _b64_png(r=128, g=128, b=128):
    data = _make_1x1_png(r, g, b)
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class TestToGltf:
    """Tests for to_gltf() which now returns a pygltflib.GLTF2 object."""

    @staticmethod
    def _mat(g):
        """Extract the first material from a to_gltf() result."""
        return g.materials[0]

    @staticmethod
    def _tex_uri(g, index):
        """Resolve a texture index to its image URI."""
        src = g.textures[index].source
        return g.images[src].uri

    def test_schema_structure(self):
        mat = PbrProperties.from_dict(_sample_data())
        g = mat.to_gltf()
        assert len(g.materials) == 1

    def test_name(self):
        g = PbrProperties.from_dict(_sample_data()).to_gltf()
        assert self._mat(g).name == "Test Material"

    def test_basic_pbr_values(self):
        data = _sample_data(values={
            "color": [0.8, 0.2, 0.1],
            "metalness": 0.9,
            "roughness": 0.4,
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        pbr = m.pbrMetallicRoughness
        assert pbr.baseColorFactor == [0.8, 0.2, 0.1, 1.0]
        assert pbr.metallicFactor == 0.9
        assert pbr.roughnessFactor == 0.4

    def test_base_color_factor_includes_opacity(self):
        data = _sample_data(values={
            "color": [1.0, 1.0, 1.0],
            "opacity": 0.5,
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.pbrMetallicRoughness.baseColorFactor == [1.0, 1.0, 1.0, 0.5]

    def test_color_texture(self):
        tex = _b64_png(200, 100, 50)
        data = _sample_data(values={"color": [1.0, 1.0, 1.0]}, textures={"color": tex})
        g = PbrProperties.from_dict(data).to_gltf()
        m = self._mat(g)
        idx = m.pbrMetallicRoughness.baseColorTexture.index
        assert self._tex_uri(g, idx) == tex

    def test_opacity_texture_merged_into_base_color(self):
        color_tex = _b64_png(200, 100, 50)
        opacity_tex = _b64_png(128, 128, 128)
        data = _sample_data(
            values={"color": [1.0, 1.0, 1.0]},
            textures={"color": color_tex, "opacity": opacity_tex},
        )
        g = PbrProperties.from_dict(data).to_gltf()
        m = self._mat(g)
        idx = m.pbrMetallicRoughness.baseColorTexture.index
        merged_uri = self._tex_uri(g, idx)
        assert merged_uri.startswith("data:image/png;base64,")
        assert merged_uri != color_tex

    def test_opacity_texture_only_creates_white_rgba(self):
        opacity_tex = _b64_png(128, 128, 128)
        data = _sample_data(textures={"opacity": opacity_tex})
        g = PbrProperties.from_dict(data).to_gltf()
        m = self._mat(g)
        idx = m.pbrMetallicRoughness.baseColorTexture.index
        assert self._tex_uri(g, idx).startswith("data:image/png;base64,")

    def test_opacity_texture_sets_mask_alpha_mode(self):
        opacity_tex = _b64_png(128, 128, 128)
        data = _sample_data(
            values={"color": [1.0, 1.0, 1.0]},
            textures={"color": _b64_png(), "opacity": opacity_tex},
        )
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.alphaMode == "MASK"
        assert m.alphaCutoff == 0.5

    def test_normal_texture(self):
        tex = _b64_png(128, 128, 255)
        data = _sample_data(textures={"normal": tex})
        g = PbrProperties.from_dict(data).to_gltf()
        m = self._mat(g)
        idx = m.normalTexture.index
        assert self._tex_uri(g, idx) == tex

    def test_normal_scale(self):
        tex = _b64_png(128, 128, 255)
        data = _sample_data(
            values={"normalScale": [0.5, 0.5]},
            textures={"normal": tex},
        )
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.normalTexture.scale == 0.5

    def test_occlusion_texture(self):
        tex = _b64_png(200, 200, 200)
        data = _sample_data(textures={"ao": tex})
        g = PbrProperties.from_dict(data).to_gltf()
        m = self._mat(g)
        idx = m.occlusionTexture.index
        assert self._tex_uri(g, idx) == tex

    def test_emissive(self):
        data = _sample_data(values={"emissive": [1.0, 0.5, 0.0]})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.emissiveFactor == [1.0, 0.5, 0.0]

    def test_alpha_mode_blend(self):
        data = _sample_data(values={"opacity": 0.5, "transparent": True})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.alphaMode == "BLEND"

    def test_alpha_mode_mask(self):
        data = _sample_data(values={"alphaTest": 0.3})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.alphaMode == "MASK"
        assert m.alphaCutoff == 0.3

    def test_double_sided(self):
        data = _sample_data(values={"side": 2})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.doubleSided is True

    def test_no_double_sided_by_default(self):
        m = self._mat(PbrProperties.from_dict(_sample_data()).to_gltf())
        assert m.doubleSided is False

    def test_extension_ior(self):
        data = _sample_data(values={"ior": 1.45})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.extensions["KHR_materials_ior"]["ior"] == 1.45

    def test_default_ior_preserved(self):
        data = _sample_data(values={"ior": 1.5})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.extensions["KHR_materials_ior"]["ior"] == 1.5

    def test_extension_transmission(self):
        data = _sample_data(values={"transmission": 0.8})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.extensions["KHR_materials_transmission"]["transmissionFactor"] == 0.8

    def test_extension_volume(self):
        data = _sample_data(values={
            "thickness": 0.5,
            "attenuationColor": [0.9, 0.5, 0.1],
            "attenuationDistance": 0.2,
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        vol = m.extensions["KHR_materials_volume"]
        assert vol["thicknessFactor"] == 0.5
        assert vol["attenuationColor"] == [0.9, 0.5, 0.1]
        assert vol["attenuationDistance"] == 0.2

    def test_extension_clearcoat(self):
        data = _sample_data(values={"clearcoat": 0.8, "clearcoatRoughness": 0.1})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        cc = m.extensions["KHR_materials_clearcoat"]
        assert cc["clearcoatFactor"] == 0.8
        assert cc["clearcoatRoughnessFactor"] == 0.1

    def test_extension_sheen(self):
        data = _sample_data(values={
            "sheen": 1.0, "sheenColor": [0.9, 0.8, 0.7],
            "sheenRoughness": 0.3,
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        sh = m.extensions["KHR_materials_sheen"]
        assert sh["sheenColorFactor"] == [0.9, 0.8, 0.7]
        assert sh["sheenRoughnessFactor"] == 0.3

    def test_extension_iridescence(self):
        data = _sample_data(values={
            "iridescence": 1.0, "iridescenceIOR": 1.3,
            "iridescenceThicknessRange": [100.0, 400.0],
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        iri = m.extensions["KHR_materials_iridescence"]
        assert iri["iridescenceFactor"] == 1.0
        assert iri["iridescenceIor"] == 1.3
        assert iri["iridescenceThicknessMinimum"] == 100.0
        assert iri["iridescenceThicknessMaximum"] == 400.0

    def test_extension_anisotropy(self):
        data = _sample_data(values={"anisotropy": 0.5, "anisotropyRotation": 1.57})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        an = m.extensions["KHR_materials_anisotropy"]
        assert an["anisotropyStrength"] == 0.5
        assert an["anisotropyRotation"] == 1.57

    def test_extension_specular(self):
        data = _sample_data(values={
            "specularIntensity": 0.8,
            "specularColor": [1.0, 0.9, 0.8],
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        sp = m.extensions["KHR_materials_specular"]
        assert sp["specularFactor"] == 0.8
        assert sp["specularColorFactor"] == [1.0, 0.9, 0.8]

    def test_extension_emissive_strength(self):
        data = _sample_data(values={"emissive": [1.0, 1.0, 1.0], "emissiveIntensity": 2.0})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.extensions["KHR_materials_emissive_strength"]["emissiveStrength"] == 2.0

    def test_extension_dispersion(self):
        data = _sample_data(values={"dispersion": 0.5})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert m.extensions["KHR_materials_dispersion"]["dispersion"] == 0.5

    def test_no_extensions_when_empty(self):
        data = _sample_data(values={"color": [0.5, 0.5, 0.5]})
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        assert not m.extensions

    def test_displacement_not_mapped(self):
        tex = _b64_png()
        data = _sample_data(
            values={"displacementScale": 0.1},
            textures={"displacement": tex},
        )
        g = PbrProperties.from_dict(data).to_gltf()
        assert "displacement" not in g.to_json()

    def test_metallic_roughness_packed_texture(self):
        tex = _b64_png()
        data = _sample_data(textures={"metallicRoughness": tex})
        g = PbrProperties.from_dict(data).to_gltf()
        m = self._mat(g)
        idx = m.pbrMetallicRoughness.metallicRoughnessTexture.index
        assert self._tex_uri(g, idx) == tex

    def test_extensions_used(self):
        data = _sample_data(values={"ior": 1.45, "transmission": 0.8})
        g = PbrProperties.from_dict(data).to_gltf()
        assert "KHR_materials_ior" in g.extensionsUsed
        assert "KHR_materials_transmission" in g.extensionsUsed

    def test_samplers_and_textures_arrays(self):
        tex = _b64_png()
        data = _sample_data(textures={"color": tex})
        g = PbrProperties.from_dict(data).to_gltf()
        assert len(g.samplers) == 1
        assert g.textures[0].source == 0
        assert g.textures[0].sampler == 0

    def test_no_images_when_no_textures(self):
        data = _sample_data(values={"color": [0.5, 0.5, 0.5]})
        g = PbrProperties.from_dict(data).to_gltf()
        assert len(g.images) == 0

    def test_texture_repeat_as_khr_texture_transform(self):
        tex = _b64_png()
        data = _sample_data(textures={"color": tex})
        mat = PbrProperties.from_dict(data).scale(2, 2)  # repeat = (0.5, 0.5)
        g = mat.to_gltf()
        m = self._mat(g)
        bc_tex = m.pbrMetallicRoughness.baseColorTexture
        assert bc_tex.extensions["KHR_texture_transform"]["scale"] == [0.5, 0.5]
        assert "KHR_texture_transform" in g.extensionsUsed
