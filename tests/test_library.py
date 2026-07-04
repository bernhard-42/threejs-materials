"""Tests for threejs_materials.library — offline, no GPU."""

import base64
import json

import pytest

from conftest import _make_1x1_png
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
# PbrProperties.create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_no_args_produces_empty_material(self):
        """create() is a passthrough — with no kwargs, no values are set.
        Three.js/glTF applies its own defaults at render time."""
        mat = PbrProperties.create(id="plain")
        assert mat.values.color is None
        assert mat.values.metalness is None
        assert mat.values.roughness is None
        assert mat.values.ior is None
        assert mat.maps.color is None

    def test_explicit_scalars_respected(self):
        mat = PbrProperties.create(
            id="t", color=(0.2, 0.4, 0.6), metalness=0.9, roughness=0.1,
        )
        assert mat.values.color == [0.2, 0.4, 0.6]
        assert mat.values.metalness == 0.9
        assert mat.values.roughness == 0.1

    def test_map_only_leaves_scalar_unset(self, tmp_path, tiny_png):
        """Passing a map without a scalar leaves the scalar unset (None).
        Three.js will apply its own default at render time."""
        png = tmp_path / "tex.png"
        png.write_bytes(tiny_png.read_bytes())

        mat = PbrProperties.create(
            id="t",
            color_map=str(png),
            metalness_map=str(png),
            roughness_map=str(png),
        )
        assert mat.values.color is None
        assert mat.values.metalness is None
        assert mat.values.roughness is None
        assert mat.maps.color == "tex.png"
        assert mat.maps.metalness == "tex.png"
        assert mat.maps.roughness == "tex.png"

    def test_scalar_plus_map_is_preserved(self, tmp_path, tiny_png):
        """User-explicit scalar + map preserved verbatim — Three.js/glTF
        multiplies them per spec."""
        png = tmp_path / "tex.png"
        png.write_bytes(tiny_png.read_bytes())

        mat = PbrProperties.create(
            id="t",
            color=(0.3, 0.3, 0.3),
            roughness=0.3,
            metalness=0.5,
            color_map=str(png),
            roughness_map=str(png),
            metalness_map=str(png),
        )
        assert mat.values.color == [0.3, 0.3, 0.3]
        assert mat.values.roughness == 0.3
        assert mat.values.metalness == 0.5

    def test_feature_scalars_passthrough(self):
        """Feature scalars are not gated — passing 0 explicitly emits 0,
        passing non-zero emits that, omitting emits nothing."""
        mat = PbrProperties.create(id="t")
        assert mat.values.clearcoat is None
        assert mat.values.sheen is None
        assert mat.values.transmission is None
        assert mat.values.iridescence is None
        assert mat.values.dispersion is None
        assert mat.values.thickness is None
        assert mat.values.anisotropy is None

        mat2 = PbrProperties.create(
            id="t", clearcoat=0.8, clearcoat_roughness=0.2,
            sheen=0.5, sheen_color=(1, 1, 1), sheen_roughness=0.3,
            iridescence=1.0, iridescence_ior=1.3,
            iridescence_thickness_range=(100, 400),
            dispersion=0.4, transmission=0.7, thickness=0.1,
        )
        assert mat2.values.clearcoat == 0.8
        assert mat2.values.clearcoat_roughness == 0.2
        assert mat2.values.sheen == 0.5
        assert mat2.values.sheen_color == [1, 1, 1]
        assert mat2.values.iridescence == 1.0
        assert mat2.values.dispersion == 0.4
        assert mat2.values.transmission == 0.7
        assert mat2.values.thickness == 0.1

    def test_feature_map_without_scalar_is_silent(self, tmp_path, tiny_png):
        """Passing only a feature-map without its enable-scalar is a no-op
        in Three.js (the feature stays off). create() reflects the input
        as-is — it does not auto-enable. Callers must set the scalar."""
        png = tmp_path / "tex.png"
        png.write_bytes(tiny_png.read_bytes())

        mat = PbrProperties.create(id="t", clearcoat_map=str(png))
        assert mat.values.clearcoat is None
        assert mat.maps.clearcoat == "tex.png"

    def test_color_string(self):
        """Color strings are sRGB and stored as sRGB byte ratios.
        three-cad-viewer's setRGB(SRGBColorSpace) handles the linearization."""
        mat = PbrProperties.create(id="t", color="#ff0000")
        assert mat.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)

    def test_color_hex_midgray_stored_as_srgb_byte_ratio(self):
        """sRGB 0x80 → 0.502 byte ratio (no gamma decode at this layer)."""
        mat = PbrProperties.create(id="t", color="#808080")
        assert mat.values.color == pytest.approx([0.5020, 0.5020, 0.5020], abs=1e-3)

    def test_to_dict_color_space_asymmetry(self):
        """to_dict() boundary contract consumed by three-cad-viewer:
        `color` is sRGB byte ratios; `emissive`/`specularColor`/`sheenColor`/
        `attenuationColor` are LINEAR. Same #ff8000 in → color keeps 0.502
        (sRGB), the rest gamma-decode to ~0.216 (linear). Regression guard for
        the orange→yellow bug: a consumer must apply `color` with SRGBColorSpace
        and the other four as bare linear THREE.Color(r,g,b)."""
        from threejs_materials.utils import _srgb_to_linear

        srgb, lin = 0x80 / 255.0, _srgb_to_linear(0x80 / 255.0)
        v = PbrProperties.create(
            id="t",
            color="#ff8000",
            emissive="#ff8000",
            specular_color="#ff8000",
            sheen_color="#ff8000",
            attenuation_color="#ff8000",
        ).to_dict()["values"]
        assert v["color"][1] == pytest.approx(srgb, abs=1e-3)  # sRGB
        for k in ("emissive", "specularColor", "sheenColor", "attenuationColor"):
            assert v[k][1] == pytest.approx(lin, abs=1e-3), k  # linear

    def test_color_4tuple_lifts_opacity(self):
        """A 4-tuple color sets opacity from the alpha component."""
        mat = PbrProperties.create(id="t", color=(0.5, 0.6, 0.7, 0.4))
        assert mat.values.color == [0.5, 0.6, 0.7]
        assert mat.values.opacity == 0.4

    def test_color_hex_with_alpha_lifts_opacity(self):
        mat = PbrProperties.create(id="t", color="#ff000080")
        assert mat.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)
        assert mat.values.opacity == pytest.approx(0.5019, abs=1e-3)

    def test_explicit_opacity_wins_over_color_alpha(self):
        """Passing both ``opacity`` and an alpha-bearing color: explicit wins."""
        mat = PbrProperties.create(
            id="t", color=(0.5, 0.6, 0.7, 0.4), opacity=0.9,
        )
        assert mat.values.color == [0.5, 0.6, 0.7]
        assert mat.values.opacity == 0.9

    def test_from_dict_and_create_converge(self, tmp_path, tiny_png):
        """Same inputs via either factory produce equivalent output."""
        png = tmp_path / "tex.png"
        png.write_bytes(tiny_png.read_bytes())

        via_create = PbrProperties.create(
            id="x",
            color=(0.3, 0.3, 0.3), metalness=0.5, roughness=0.3,
            color_map=str(png),
        )
        via_dict = PbrProperties.from_dict({
            "id": "x", "name": "x", "source": "custom",
            "url": "", "license": "",
            "values": {
                "color": [0.3, 0.3, 0.3],
                "metalness": 0.5, "roughness": 0.3,
            },
            "textures": {"color": "tex.png"},
            "maps_dir": str(tmp_path),
        })
        assert via_create.values.color == via_dict.values.color
        assert via_create.values.metalness == via_dict.values.metalness
        assert via_create.values.roughness == via_dict.values.roughness
        assert via_create.maps.color == via_dict.maps.color

    def test_texture_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="Texture file not found"):
            PbrProperties.create(id="t", color_map="/nonexistent/tex.png")

    def test_mixed_texture_dirs_rejected(self, tmp_path, tiny_png):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "tex.png").write_bytes(tiny_png.read_bytes())
        (d2 / "tex.png").write_bytes(tiny_png.read_bytes())

        with pytest.raises(ValueError, match="same directory"):
            PbrProperties.create(
                id="t",
                color_map=str(d1 / "tex.png"),
                roughness_map=str(d2 / "tex.png"),
            )


# ---------------------------------------------------------------------------
# PbrProperties.from_pymat
# ---------------------------------------------------------------------------


class TestFromPymat:
    def test_hex_color_stored_as_srgb(self):
        """Hex colors are sRGB by definition; values.color stores sRGB byte
        ratios so three-cad-viewer's setRGB(SRGBColorSpace) renders correctly."""
        mat = PbrProperties.from_pymat({"color": "#ff0000"})
        assert mat.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)

    def test_hex_midgray_stored_as_srgb(self):
        """sRGB 0x80 → stored as 0.502 (byte ratio). The viewer's
        setRGB(SRGBColorSpace) gamma-decodes to ~0.216 linear at render."""
        mat = PbrProperties.from_pymat({"color": "#808080"})
        assert mat.values.color == pytest.approx([0.5020, 0.5020, 0.5020], abs=1e-3)

    def test_int_color_treated_as_hex(self):
        mat = PbrProperties.from_pymat({"color": 0xff0000})
        assert mat.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)

    def test_4tuple_color_lifts_opacity(self):
        mat = PbrProperties.from_pymat({"color": (0.5, 0.6, 0.7, 0.4)})
        assert mat.values.color == [0.5, 0.6, 0.7]
        assert mat.values.opacity == 0.4

    def test_explicit_pbr_opacity_wins_over_color_alpha(self):
        mat = PbrProperties.from_pymat(
            {"color": (0.5, 0.6, 0.7, 0.4), "opacity": 0.9}
        )
        assert mat.values.color == [0.5, 0.6, 0.7]
        assert mat.values.opacity == 0.9

    def test_overrides_color_normalized(self):
        mat = PbrProperties.from_pymat(
            {"color": "#000000"},
            overrides={"color": "#ff0000"},
        )
        assert mat.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)

    def test_overrides_color_with_alpha_lifts_opacity(self):
        mat = PbrProperties.from_pymat(
            {"color": "#000000"},
            overrides={"color": (0.2, 0.3, 0.4, 0.5)},
        )
        assert mat.values.color == [0.2, 0.3, 0.4]
        assert mat.values.opacity == 0.5


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

    def test_color_override_preserves_texture_as_tint(self):
        """Per glTF 2.0 §3.9.2 ('If both factors and textures are present,
        the factor value acts as a linear multiplier for the corresponding
        texture values'), `override(color=...)` on a material with a color
        texture must KEEP the texture and update only the scalar — Three.js
        will then render `color × textureSample` per pixel, tinting the
        texture. The previous behavior of deleting the texture and warning
        was the legacy 'color OR texture' interpretation, contradicted by
        the spec."""
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64,abc"
        mat = PbrProperties.from_dict(data)

        red = mat.override(color=(0.5, 0.0, 0.0))
        assert red.values.color == [0.5, 0.0, 0.0]
        assert red.maps.color == "data:image/png;base64,abc"

    def test_color_override_without_texture_sets_value(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.5, 0.0, 0.0))
        assert new.values.color == [0.5, 0.0, 0.0]
        assert new.maps.color is None

    def test_color_override_hex_stored_as_srgb(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color="#ff0000")
        assert new.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)

    def test_color_override_hex_midgray_stored_as_srgb_byte_ratio(self):
        """sRGB 0x80 → 0.502 byte ratio (regression: previously gamma-decoded
        to 0.216 linear, which broke rendering in three-cad-viewer's
        setRGB(SRGBColorSpace) path)."""
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color="#808080")
        assert new.values.color == pytest.approx([0.5020, 0.5020, 0.5020], abs=1e-3)

    def test_color_override_4tuple_lifts_opacity(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.5, 0.6, 0.7, 0.4))
        assert new.values.color == [0.5, 0.6, 0.7]
        assert new.values.opacity == 0.4

    def test_color_override_hex_with_alpha_lifts_opacity(self):
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color="#ff000080")
        assert new.values.color == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)
        assert new.values.opacity == pytest.approx(0.5019, abs=1e-3)

    def test_color_override_explicit_opacity_wins(self):
        """When ``opacity=`` is also passed, it overrides any color alpha."""
        mat = PbrProperties.from_dict(_sample_data())
        new = mat.override(color=(0.5, 0.6, 0.7, 0.4), opacity=0.9)
        assert new.values.color == [0.5, 0.6, 0.7]
        assert new.values.opacity == 0.9

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

    def test_color_override_does_not_mutate_original(self):
        """`override()` returns a new PbrProperties; the source must not be
        modified, regardless of whether it carries a color texture."""
        data = _sample_data()
        data["textures"]["color"] = "data:image/png;base64,abc"
        mat = PbrProperties.from_dict(data)

        new = mat.override(color=(0.5, 0.0, 0.0))
        # New material reflects the override
        assert new.values.color == [0.5, 0.0, 0.0]
        assert new.maps.color == "data:image/png;base64,abc"  # texture preserved (tint)
        # Original unchanged
        assert mat.values.color == [1.0, 0.0, 0.0]
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
# PbrOverrides / TextureTransform — typed config objects
# ---------------------------------------------------------------------------


class TestNormalizeColor:
    """``utils._normalize_color`` produces **linear** RGB output. Used for
    color fields that Three.js consumes in linear space — emissive,
    sheen_color, specular_color, attenuation_color."""

    def test_hex_6chars_decodes_srgb_to_linear(self):
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color("#ff0000")
        # Pure red sRGB (0xff) → 1.0 linear
        assert rgb == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert alpha is None

    def test_hex_6chars_midgray_linearizes(self):
        """sRGB 0x80 (≈0.502) → linear ≈0.216 (gamma-decoded, not byte ratio)."""
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color("#808080")
        assert rgb == pytest.approx((0.2159, 0.2159, 0.2159), abs=1e-3)
        assert alpha is None

    def test_hex_8chars_lifts_alpha(self):
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color("#80808080")
        assert rgb == pytest.approx((0.2159, 0.2159, 0.2159), abs=1e-3)
        assert alpha == pytest.approx(0.5019, abs=1e-3)  # 0x80/255

    def test_named_color(self):
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color("red")
        assert rgb == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert alpha is None

    def test_three_tuple_passthrough(self):
        """Numeric tuples for linear-output normalizer: passthrough (already linear)."""
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color((0.5, 0.5, 0.5))
        assert rgb == (0.5, 0.5, 0.5)
        assert alpha is None

    def test_three_list_passthrough(self):
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color([0.1, 0.2, 0.3])
        assert rgb == (0.1, 0.2, 0.3)
        assert alpha is None

    def test_four_tuple_splits_alpha(self):
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color((0.1, 0.2, 0.3, 0.7))
        assert rgb == (0.1, 0.2, 0.3)
        assert alpha == 0.7

    def test_four_list_splits_alpha(self):
        from threejs_materials.utils import _normalize_color
        rgb, alpha = _normalize_color([0.1, 0.2, 0.3, 0.7])
        assert rgb == (0.1, 0.2, 0.3)
        assert alpha == 0.7

    def test_wrong_tuple_length_raises(self):
        from threejs_materials.utils import _normalize_color
        with pytest.raises(ValueError, match="3 or 4 elements"):
            _normalize_color((0.1, 0.2))
        with pytest.raises(ValueError, match="3 or 4 elements"):
            _normalize_color((0.1, 0.2, 0.3, 0.4, 0.5))

    def test_unsupported_type_raises(self):
        from threejs_materials.utils import _normalize_color
        with pytest.raises(TypeError, match="Unsupported color type"):
            _normalize_color(42)


class TestNormalizeSrgbColor:
    """``utils._normalize_srgb_color`` produces **sRGB byte ratio** output.
    Used for ``values.color`` which Three.js consumes via setRGB(SRGBColorSpace)."""

    def test_hex_6chars_stored_as_srgb(self):
        from threejs_materials.utils import _normalize_srgb_color
        rgb, alpha = _normalize_srgb_color("#ff0000")
        assert rgb == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert alpha is None

    def test_hex_6chars_midgray_no_gamma_decode(self):
        """sRGB 0x80 → stored as 0.502 (byte ratio, no gamma decode).
        The viewer's setRGB(SRGBColorSpace) handles linearization."""
        from threejs_materials.utils import _normalize_srgb_color
        rgb, alpha = _normalize_srgb_color("#808080")
        assert rgb == pytest.approx((0.5020, 0.5020, 0.5020), abs=1e-3)
        assert alpha is None

    def test_hex_8chars_lifts_alpha(self):
        from threejs_materials.utils import _normalize_srgb_color
        rgb, alpha = _normalize_srgb_color("#80808080")
        assert rgb == pytest.approx((0.5020, 0.5020, 0.5020), abs=1e-3)
        assert alpha == pytest.approx(0.5019, abs=1e-3)

    def test_named_color(self):
        from threejs_materials.utils import _normalize_srgb_color
        rgb, alpha = _normalize_srgb_color("red")
        assert rgb == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert alpha is None

    def test_three_tuple_passthrough(self):
        """Numeric tuples for sRGB normalizer: passthrough (already sRGB by convention)."""
        from threejs_materials.utils import _normalize_srgb_color
        rgb, alpha = _normalize_srgb_color((0.5, 0.5, 0.5))
        assert rgb == (0.5, 0.5, 0.5)
        assert alpha is None

    def test_four_tuple_splits_alpha(self):
        from threejs_materials.utils import _normalize_srgb_color
        rgb, alpha = _normalize_srgb_color((0.1, 0.2, 0.3, 0.7))
        assert rgb == (0.1, 0.2, 0.3)
        assert alpha == 0.7

    def test_wrong_tuple_length_raises(self):
        from threejs_materials.utils import _normalize_srgb_color
        with pytest.raises(ValueError, match="3 or 4 elements"):
            _normalize_srgb_color((0.1, 0.2))

    def test_unsupported_type_raises(self):
        from threejs_materials.utils import _normalize_srgb_color
        with pytest.raises(TypeError, match="Unsupported color type"):
            _normalize_srgb_color(42)

    def test_round_trip_with_normalize_color_diverges_at_midtone(self):
        """The two normalizers must produce different outputs for non-extreme
        values — proves the asymmetry is real, not just renamed."""
        from threejs_materials.utils import _normalize_color, _normalize_srgb_color
        srgb, _ = _normalize_srgb_color("#808080")
        linear, _ = _normalize_color("#808080")
        assert srgb[0] == pytest.approx(0.5020, abs=1e-3)
        assert linear[0] == pytest.approx(0.2159, abs=1e-3)


class TestPbrOverrides:
    def test_default_is_all_none(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides()
        assert o.color is None
        assert o.roughness is None
        assert o.metalness is None

    def test_as_kwargs_filters_none(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=(1.0, 0.0, 0.0), roughness=0.4)
        kw = o.as_kwargs()
        assert kw == {"color": (1.0, 0.0, 0.0), "roughness": 0.4}
        # No None entries — only fields the caller actually set.
        assert "metalness" not in kw

    def test_normalizes_hex_color_to_srgb_3tuple(self):
        """``color`` is sRGB-stored — hex passes through as byte ratios."""
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color="#ff0000")
        assert o.color == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert o.opacity is None

    def test_normalizes_midgray_hex_to_srgb_byte_ratio(self):
        """``color`` is sRGB-stored — 0x80 → 0.502 byte ratio (no gamma decode)."""
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color="#808080")
        assert o.color == pytest.approx((0.5020, 0.5020, 0.5020), abs=1e-3)

    def test_normalizes_hex_with_alpha_lifts_opacity(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color="#ff000080")
        assert o.color == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert o.opacity == pytest.approx(0.5019, abs=1e-3)

    def test_4tuple_color_lifts_opacity(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=(0.5, 0.6, 0.7, 0.4))
        assert o.color == (0.5, 0.6, 0.7)
        assert o.opacity == 0.4

    def test_explicit_opacity_wins_over_color_alpha(self):
        """When both ``opacity=`` and an alpha-bearing color are passed,
        the explicit ``opacity`` field is respected."""
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=(0.5, 0.6, 0.7, 0.4), opacity=0.9)
        assert o.color == (0.5, 0.6, 0.7)
        assert o.opacity == 0.9

    def test_3tuple_color_leaves_opacity_unset(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=(0.5, 0.6, 0.7))
        assert o.color == (0.5, 0.6, 0.7)
        assert o.opacity is None

    def test_other_color_fields_normalized(self):
        """Non-``color`` color-typed fields are also normalized (but their
        alpha is dropped — only ``color`` lifts to ``opacity``)."""
        from threejs_materials import PbrOverrides
        o = PbrOverrides(
            emissive="#ff0000",
            sheen_color=(0.1, 0.2, 0.3),
            specular_color="#808080",
            attenuation_color=(0.5, 0.5, 0.5, 0.7),  # alpha dropped silently
        )
        assert o.emissive == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert o.sheen_color == (0.1, 0.2, 0.3)
        assert o.specular_color == pytest.approx((0.2159, 0.2159, 0.2159), abs=1e-3)
        assert o.attenuation_color == (0.5, 0.5, 0.5)
        # 4-tuple alpha on non-color fields does NOT lift to opacity
        assert o.opacity is None

    def test_as_kwargs_drives_override(self):
        """End-to-end: client constructs PbrOverrides → unpacks into
        mat.override(**...). Must produce the same result as calling
        override() with the same kwargs directly."""
        from threejs_materials import PbrOverrides
        mat = PbrProperties.from_dict(_sample_data())
        overrides = PbrOverrides(color=(0.85, 0.10, 0.05), roughness=0.4)

        from_dataclass = mat.override(**overrides.as_kwargs())
        from_kwargs = mat.override(color=(0.85, 0.10, 0.05), roughness=0.4)

        # Identical state: id, values, all the same
        assert from_dataclass.id == from_kwargs.id
        assert from_dataclass.values.color == from_kwargs.values.color
        assert from_dataclass.values.roughness == from_kwargs.values.roughness

    def test_frozen_and_hashable(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=(1, 0, 0))
        # Frozen → can't mutate
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            o.color = (0, 1, 0)  # ty: ignore[invalid-assignment]  (frozen, tested)
        # Hashable → can be used as dict keys / in sets
        assert hash(o) is not None

    def test_repr_hides_none_fields(self):
        from threejs_materials import PbrOverrides
        o = PbrOverrides(roughness=0.4)
        r = repr(o)
        assert "roughness=0.4" in r
        assert "color" not in r  # None fields hidden
        assert "metalness" not in r

    def test_color_to_tuple_default_is_passthrough(self):
        """Base class hook returns input unchanged. ``int`` is converted to
        hex *before* the hook fires (in __post_init__), so subclasses
        never need to handle the int case themselves."""
        from threejs_materials import PbrOverrides
        assert PbrOverrides._color_to_tuple("#ff0000") == "#ff0000"
        assert PbrOverrides._color_to_tuple((1.0, 0.0, 0.0)) == (1.0, 0.0, 0.0)
        assert PbrOverrides._color_to_tuple([0.5, 0.5, 0.5]) == [0.5, 0.5, 0.5]
        # Hook is a passthrough — int stays int (post_init handles it earlier).
        assert PbrOverrides._color_to_tuple(0xff0000) == 0xff0000

    def test_color_int_input_works_end_to_end(self):
        """Integer color flows through PbrOverrides() construction:
        __post_init__ converts int → hex → hook (passthrough) → normalizer."""
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=0xff0000)
        assert o.color == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)

    def test_color_int_midgray_stored_as_srgb_byte_ratio(self):
        """0x808080 → "#808080" → sRGB-stored as 0.502 (no gamma decode at
        the color field; viewer linearizes via setRGB(SRGBColorSpace))."""
        from threejs_materials import PbrOverrides
        o = PbrOverrides(color=0x808080)
        assert o.color == pytest.approx((0.502, 0.502, 0.502), abs=1e-3)

    def test_bool_input_raises_not_treated_as_int(self):
        """``bool`` is a subclass of ``int`` in Python — must NOT be
        treated as a hex code (otherwise ``True`` would become
        ``"#000001"``). It falls through to the normalizer's TypeError."""
        from threejs_materials import PbrOverrides
        with pytest.raises(TypeError, match="Unsupported color type"):
            PbrOverrides(color=True)

    def test_color_to_tuple_subclass_bridges_custom_type(self):
        """Subclasses can override the hook to coerce a custom color class
        into a 3- or 4-tuple. The standard normalizer then handles
        sRGB-vs-linear and alpha lifting unchanged."""
        from threejs_materials import PbrOverrides

        class CustomColor:
            def __init__(self, r, g, b, a=None):
                self.r, self.g, self.b, self.a = r, g, b, a

        class CustomPbrOverrides(PbrOverrides):
            @staticmethod
            def _color_to_tuple(c):
                if isinstance(c, CustomColor):
                    if c.a is not None:
                        return (c.r, c.g, c.b, c.a)
                    return (c.r, c.g, c.b)
                return c

        # 3-tuple via subclass: stored as sRGB, no opacity lift
        o = CustomPbrOverrides(color=CustomColor(0.5, 0.6, 0.7))  # ty: ignore[invalid-argument-type]
        assert o.color == (0.5, 0.6, 0.7)
        assert o.opacity is None

        # 4-tuple via subclass: alpha lifts into opacity (color field only)
        o2 = CustomPbrOverrides(color=CustomColor(0.5, 0.6, 0.7, 0.4))  # ty: ignore[invalid-argument-type]
        assert o2.color == (0.5, 0.6, 0.7)
        assert o2.opacity == 0.4

        # Linear field (emissive): alpha dropped silently, no opacity lift
        o3 = CustomPbrOverrides(emissive=CustomColor(0.1, 0.2, 0.3, 0.9))  # ty: ignore[invalid-argument-type]
        assert o3.emissive == (0.1, 0.2, 0.3)
        assert o3.opacity is None

    def test_color_to_tuple_subclass_preserves_standard_forms(self):
        """A subclass override that handles its custom type must still pass
        through standard forms (string / tuple / list) so existing call
        sites keep working unchanged."""
        from threejs_materials import PbrOverrides

        class CustomPbrOverrides(PbrOverrides):
            @staticmethod
            def _color_to_tuple(c):
                # Subclass passes everything through — same as base default
                return c

        o = CustomPbrOverrides(color="#ff0000")
        assert o.color == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)


class TestTextureTransform:
    def test_default_is_identity(self):
        from threejs_materials import TextureTransform
        t = TextureTransform()
        assert t.scale == (1.0, 1.0)
        assert t.rotation == 0.0
        assert t.fixed_size is True

    def test_as_kwargs_drives_scale(self):
        """End-to-end: client constructs TextureTransform → unpacks into
        mat.scale(**...). Must produce same result as direct scale() call."""
        from threejs_materials import TextureTransform
        mat = PbrProperties.from_dict(_sample_data())
        transform = TextureTransform(scale=(2.0, 2.0), rotation=90, fixed_size=True)

        from_dataclass = mat.scale(**transform.as_kwargs())
        from_kwargs = mat.scale(2.0, 2.0, fixed=True, rotation=90)

        assert from_dataclass.id == from_kwargs.id
        assert from_dataclass.texture_repeat == from_kwargs.texture_repeat
        assert from_dataclass.texture_rotation == from_kwargs.texture_rotation
        assert from_dataclass.normalize_uvs == from_kwargs.normalize_uvs

    def test_frozen_and_hashable(self):
        from threejs_materials import TextureTransform
        t = TextureTransform(scale=(2.0, 2.0), rotation=45)
        with pytest.raises(Exception):
            t.scale = (4.0, 4.0)  # ty: ignore[invalid-assignment]  (frozen, tested)
        assert hash(t) is not None


class TestScaleRotation:
    """``mat.scale(rotation=alpha)`` stores rotation in degrees and emits
    radians on the wire (Three.js / glTF spec)."""

    def test_default_no_rotation(self):
        mat = PbrProperties.from_dict(_sample_data())
        s = mat.scale(2, 2)
        assert s.texture_rotation is None
        # to_dict omits the field entirely when no rotation
        assert "textureRotation" not in s.to_dict()

    def test_rotation_stored_as_degrees(self):
        mat = PbrProperties.from_dict(_sample_data())
        s = mat.scale(2, 2, rotation=90)
        assert s.texture_rotation == 90

    def test_to_dict_emits_radians(self):
        """JSON output uses radians for direct Three.js consumption."""
        import math
        mat = PbrProperties.from_dict(_sample_data())
        s = mat.scale(rotation=90)
        assert s.to_dict()["textureRotation"] == pytest.approx(math.pi / 2, abs=1e-6)

    def test_rotation_affects_variant_id(self):
        """Different rotation → different variant id (so dict-keyed
        materials don't collapse)."""
        mat = PbrProperties.from_dict(_sample_data())
        a = mat.scale(2, 2)
        b = mat.scale(2, 2, rotation=90)
        c = mat.scale(2, 2, rotation=180)
        assert len({a.id, b.id, c.id}) == 3

    def test_rotation_round_trips_through_from_dict(self):
        """from_dict reads texture_rotation from data → preserves on round-trip."""
        mat = PbrProperties.from_dict(_sample_data()).scale(2, 2, rotation=45)
        d = mat.to_dict()
        # Note: to_dict emits camelCase 'textureRotation' (radians) for the
        # viewer; from_dict reads 'texture_rotation' (degrees) for round-trip
        # via the cache JSON shape used internally.
        d["texture_rotation"] = mat.texture_rotation
        round_tripped = PbrProperties.from_dict(d)
        assert round_tripped.texture_rotation == 45

    def test_scale_replaces_rotation(self):
        """Each scale() call replaces the full transform — chaining .scale(2,2)
        after .scale(rotation=90) drops the rotation."""
        mat = PbrProperties.from_dict(_sample_data())
        s = mat.scale(rotation=90).scale(2, 2)
        assert s.texture_rotation is None

    def test_override_preserves_rotation_and_repeat(self):
        """Regression: ``mat.scale(rotation=90).override(color="red")`` used
        to drop ``texture_rotation`` because override() didn't propagate it."""
        mat = PbrProperties.from_dict(_sample_data())
        chained = mat.scale(2, 2, rotation=90).override(color="red")
        assert chained.texture_rotation == 90
        assert chained.texture_repeat == (0.5, 0.5)
        assert chained.values.color == [1.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# clear_cache / list_cache (in sources)
# ---------------------------------------------------------------------------
# interpolate_color — display-space preview color
# ---------------------------------------------------------------------------


class TestInterpolateColor:
    """Returns a perceptually-representative sRGB color + alpha for CAD-mode
    preview. Inputs in different spaces converge on sRGB output."""

    def test_string_color_passthrough(self):
        """values.color = "#ff0000" → sRGB (1, 0, 0) — string is sRGB at source."""
        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {"color": "#ff0000"},
        })
        r, g, b, a = mat.interpolate_color()
        assert (r, g, b) == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
        assert a == 1.0

    def test_list_color_passthrough(self):
        """values.color stored as list is already sRGB — no conversion."""
        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {"color": [0.5, 0.5, 0.5]},
        })
        r, g, b, _ = mat.interpolate_color()
        # No gamma conversion — input was already sRGB
        assert (r, g, b) == pytest.approx((0.5, 0.5, 0.5), abs=1e-3)

    def test_no_color_fallback_is_srgb_midgray(self):
        """Fallback when nothing is set: sRGB perceptual midgray (0.5, 0.5, 0.5)."""
        data = _sample_data()
        data["values"] = {}
        data["textures"] = {}
        mat = PbrProperties.from_dict(data)
        r, g, b, _ = mat.interpolate_color()
        assert (r, g, b) == (0.5, 0.5, 0.5)

    def test_opacity_passes_through(self):
        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {"color": [1.0, 0.0, 0.0], "opacity": 0.6},
        })
        _, _, _, a = mat.interpolate_color()
        assert a == 0.6

    def test_override_color_no_texture_returns_override(self):
        """Without a color texture, override_color is the result directly."""
        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {"color": [1.0, 0.0, 0.0]},  # red, ignored under override
        })
        r, g, b, _ = mat.interpolate_color(override_color="#0000ff")
        assert (r, g, b) == pytest.approx((0.0, 0.0, 1.0), abs=1e-3)

    def test_override_color_with_texture_preserves_luminance(self):
        """texture × override hue, rescaled to texture luminance — preview
        matches rendered brightness instead of the dim physical multiply."""
        from PIL import Image as PILImage
        import io
        import base64

        # Solid mid-gray texture (sRGB 0x80 ≈ linear 0.216 across all channels)
        img = PILImage.new("RGB", (4, 4), (0x80, 0x80, 0x80))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tex = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {},
            "textures": {"color": tex},
        })
        # Gray override × gray texture: luminance preservation pulls the
        # result back up to the texture's brightness (sRGB 0.502).
        r, g, b, _ = mat.interpolate_color(override_color=(0.5, 0.5, 0.5))
        assert (r, g, b) == pytest.approx((0.502, 0.502, 0.502), abs=1e-2)

    def test_override_color_dark_tint_brightens_to_texture_luminance(self):
        """Regression: a dark teal tint on a moderate-luminance texture
        was producing near-black preview (~sRGB 0.25) instead of a readable
        green. Luminance preservation lifts it to ~half intensity per channel."""
        from PIL import Image as PILImage
        import io
        import base64

        # Solid mid-gray texture
        img = PILImage.new("RGB", (4, 4), (0x80, 0x80, 0x80))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tex = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {},
            "textures": {"color": tex},
        })
        # (0, 0.5, 0.5) override: blue+green channels active, red zero.
        # Without rescale: sRGB ~(0, 0.235, 0.235). With rescale: ~(0, 0.55, 0.55).
        r, g, b, _ = mat.interpolate_color(override_color=(0, 0.5, 0.5))
        assert r == pytest.approx(0.0, abs=1e-3)
        assert g > 0.4 and b > 0.4  # readable green/blue, not near-black
        assert g == pytest.approx(b, abs=1e-3)  # symmetric around the swap

    def test_override_color_pure_black_stays_black(self):
        """Pure-black override: y_mul ≈ 0, no rescale, result stays black."""
        from PIL import Image as PILImage
        import io
        import base64
        img = PILImage.new("RGB", (4, 4), (0x80, 0x80, 0x80))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tex = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        mat = PbrProperties.from_dict({
            **_sample_data(), "values": {}, "textures": {"color": tex},
        })
        r, g, b, _ = mat.interpolate_color(override_color=(0, 0, 0))
        assert (r, g, b) == (0.0, 0.0, 0.0)

    def test_override_color_none_preserves_existing_behavior(self):
        """Default override_color=None: same result as no argument."""
        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {"color": [0.7, 0.3, 0.1]},
        })
        assert mat.interpolate_color() == mat.interpolate_color(override_color=None)

    def test_override_applied_color_tints_texture(self):
        """Regression: ``mat.override(color="red").interpolate_color()`` used
        to ignore the override when a color texture existed — the texture
        branch fired before the list-color branch. Now ``values.color`` tints
        the texture identically to passing ``override_color=`` directly."""
        from PIL import Image as PILImage
        import io
        import base64

        img = PILImage.new("RGB", (4, 4), (0x80, 0x80, 0x80))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tex = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        mat = PbrProperties.from_dict({
            **_sample_data(),
            "values": {},
            "textures": {"color": tex},
        })

        from_override = mat.override(color="red").interpolate_color()
        from_arg = mat.interpolate_color(override_color="red")
        assert from_override == from_arg
        # And it must actually be reddish, not the texture's neutral gray
        r, g, b, _ = from_override
        assert r > 0.4 and g < 0.05 and b < 0.05


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
        """values.color is sRGB-stored; baseColorFactor on the wire is linear
        per glTF spec — conversion happens at the gltf.py boundary."""
        from threejs_materials.utils import _srgb_to_linear
        data = _sample_data(values={
            "color": [0.8, 0.2, 0.1],  # sRGB
            "metalness": 0.9,
            "roughness": 0.4,
        })
        m = self._mat(PbrProperties.from_dict(data).to_gltf())
        pbr = m.pbrMetallicRoughness
        expected_linear = [_srgb_to_linear(c) for c in [0.8, 0.2, 0.1]] + [1.0]
        assert pbr.baseColorFactor == pytest.approx(expected_linear, abs=1e-6)
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
