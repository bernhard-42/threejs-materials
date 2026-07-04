"""Tests for glTF export (collect_gltf_textures) and import (from_gltf)."""

import base64

import pytest

from conftest import _make_1x1_png
from threejs_materials.library import PbrProperties
from threejs_materials.gltf import collect_gltf_textures


def _b64_png(r=128, g=128, b=128):
    data = _make_1x1_png(r, g, b)
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _sample(name="mat", values=None, textures=None):
    vals = {"color": [0.5, 0.5, 0.5]}
    if values:
        vals.update(values)
    return PbrProperties.from_dict({
        "id": name, "name": name, "source": "test",
        "url": "", "license": "CC0",
        "values": vals,
        "textures": textures or {},
    })


# ---------------------------------------------------------------------------
# collect_gltf_textures
# ---------------------------------------------------------------------------


class TestCollectGltfTextures:
    def test_single_material(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(name="body", values={"color": [1, 1, 1]}, textures={"color": tex})
        g = collect_gltf_textures({"body": mat})
        assert len(g.materials) == 1
        assert g.materials[0].name == "body"
        assert len(g.images) == 1
        assert g.images[0].uri == tex

    def test_multiple_materials(self):
        tex1 = _b64_png(200, 100, 50)
        tex2 = _b64_png(50, 100, 200)
        mat1 = _sample(name="a", values={"color": [1, 1, 1]}, textures={"color": tex1})
        mat2 = _sample(name="b", values={"color": [1, 1, 1]}, textures={"color": tex2})
        g = collect_gltf_textures({"a": mat1, "b": mat2})
        assert len(g.materials) == 2
        assert len(g.images) == 2
        assert g.materials[0].name == "a"
        assert g.materials[1].name == "b"

    def test_texture_deduplication(self):
        tex = _b64_png(200, 100, 50)
        mat1 = _sample(name="a", values={"color": [1, 1, 1]}, textures={"color": tex})
        mat2 = _sample(name="b", values={"color": [0.5, 0.5, 0.5]}, textures={"color": tex})
        g = collect_gltf_textures({"a": mat1, "b": mat2})
        # Same texture → deduplicated to one image
        assert len(g.images) == 1
        # Both materials reference index 0
        pbr0 = g.materials[0].pbrMetallicRoughness
        pbr1 = g.materials[1].pbrMetallicRoughness
        assert pbr0 is not None and pbr0.baseColorTexture is not None
        assert pbr1 is not None and pbr1.baseColorTexture is not None
        assert pbr0.baseColorTexture.index == pbr1.baseColorTexture.index == 0

    def test_no_textures(self):
        mat = _sample(name="gold", values={"color": [1, 0.8, 0.3]})
        g = collect_gltf_textures({"gold": mat})
        assert len(g.images) == 0
        assert len(g.materials) == 1

    def test_extensions_used_merged(self):
        mat1 = _sample(name="a", values={"ior": 1.45})
        mat2 = _sample(name="b", values={"transmission": 0.8})
        g = collect_gltf_textures({"a": mat1, "b": mat2})
        assert "KHR_materials_ior" in g.extensionsUsed
        assert "KHR_materials_transmission" in g.extensionsUsed

    def test_samplers_present(self):
        tex = _b64_png()
        mat = _sample(name="x", textures={"color": tex})
        g = collect_gltf_textures({"x": mat})
        assert len(g.samplers) == 1
        assert g.samplers[0].magFilter == 9729

    def test_textures_array(self):
        tex = _b64_png()
        mat = _sample(name="x", textures={"color": tex})
        g = collect_gltf_textures({"x": mat})
        assert g.textures[0].source == 0
        assert g.textures[0].sampler == 0

    def test_name_override(self):
        """Dict key overrides material.name."""
        mat = _sample(name="original")
        g = collect_gltf_textures({"override_name": mat})
        assert g.materials[0].name == "override_name"

    def test_texture_repeat(self):
        tex = _b64_png()
        mat = _sample(name="tiled", textures={"color": tex}).scale(2, 2)
        g = collect_gltf_textures({"tiled": mat})
        pbr = g.materials[0].pbrMetallicRoughness
        assert pbr is not None and pbr.baseColorTexture is not None
        bc_tex = pbr.baseColorTexture
        assert bc_tex.extensions is not None
        assert bc_tex.extensions["KHR_texture_transform"]["scale"] == [0.5, 0.5]
        assert "KHR_texture_transform" in g.extensionsUsed


# ---------------------------------------------------------------------------
# PbrProperties.from_gltf
# ---------------------------------------------------------------------------


class TestFromGltf:
    def test_basic_pbr(self):
        mat = _sample(values={
            "color": [0.8, 0.2, 0.1],
            "metalness": 0.9,
            "roughness": 0.4,
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.color == pytest.approx([0.8, 0.2, 0.1])
        assert imported.values.metalness == pytest.approx(0.9)
        assert imported.values.roughness == pytest.approx(0.4)
        assert imported.source == "gltf"

    def test_texture_resolved(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(values={"color": [1, 1, 1]}, textures={"color": tex})
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.maps.color == tex

    def test_alpha_blend(self):
        mat = _sample(values={
            "color": [1, 1, 1],
            "opacity": 0.5,
            "transparent": True,
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.transparent is True

    def test_alpha_mask(self):
        mat = _sample(values={"alphaTest": 0.3})
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.alpha_test == pytest.approx(0.3)

    def test_double_sided(self):
        mat = _sample(values={"side": 2})
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.side == 2

    def test_ior(self):
        mat = _sample(values={"ior": 1.45})
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.ior == pytest.approx(1.45)

    def test_transmission(self):
        mat = _sample(values={"transmission": 0.8})
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.transmission == pytest.approx(0.8)

    def test_clearcoat(self):
        mat = _sample(values={
            "clearcoat": 0.8,
            "clearcoatRoughness": 0.1,
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.clearcoat == pytest.approx(0.8)
        assert imported.values.clearcoat_roughness == pytest.approx(0.1)

    def test_sheen(self):
        mat = _sample(values={
            "sheen": 1.0,
            "sheenColor": [0.9, 0.8, 0.7],
            "sheenRoughness": 0.3,
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.sheen_color == pytest.approx([0.9, 0.8, 0.7])
        assert imported.values.sheen_roughness == pytest.approx(0.3)

    def test_iridescence(self):
        mat = _sample(values={
            "iridescence": 1.0,
            "iridescenceIOR": 1.3,
            "iridescenceThicknessRange": [100.0, 400.0],
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.iridescence == pytest.approx(1.0)
        assert imported.values.iridescence_ior == pytest.approx(1.3)
        assert imported.values.iridescence_thickness_range == pytest.approx([100.0, 400.0])

    def test_specular(self):
        mat = _sample(values={
            "specularIntensity": 0.8,
            "specularColor": [1.0, 0.9, 0.8],
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.specular_intensity == pytest.approx(0.8)
        assert imported.values.specular_color == pytest.approx([1.0, 0.9, 0.8])

    def test_dispersion(self):
        mat = _sample(values={"dispersion": 0.5})
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.dispersion == pytest.approx(0.5)

    def test_emissive_strength(self):
        mat = _sample(values={
            "emissive": [1, 1, 1],
            "emissiveIntensity": 2.0,
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.emissive_intensity == pytest.approx(2.0)

    def test_clearcoat_roughness_texture(self):
        tex = _b64_png(120, 60, 30)
        mat = _sample(
            values={"clearcoat": 0.8, "clearcoatRoughness": 0.2},
            textures={"clearcoat_roughness": tex},
        )
        g = mat.to_gltf()
        cc = g.materials[0].extensions["KHR_materials_clearcoat"]
        assert "clearcoatRoughnessTexture" in cc
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.maps.clearcoat_roughness == tex

    def test_sheen_roughness_texture(self):
        tex = _b64_png(80, 40, 20)
        mat = _sample(
            values={"sheen": 1.0, "sheenRoughness": 0.3},
            textures={"sheen_roughness": tex},
        )
        g = mat.to_gltf()
        sh = g.materials[0].extensions["KHR_materials_sheen"]
        assert "sheenRoughnessTexture" in sh
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.maps.sheen_roughness == tex

    def test_anisotropy_texture(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(
            values={"anisotropy": 0.5, "anisotropyRotation": 1.57},
            textures={"anisotropy": tex},
        )
        g = mat.to_gltf()
        an = g.materials[0].extensions["KHR_materials_anisotropy"]
        assert "anisotropyTexture" in an
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.maps.anisotropy == tex

    def test_texture_repeat_restored(self):
        tex = _b64_png()
        mat = _sample(textures={"color": tex}).scale(2, 2)
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.texture_repeat == pytest.approx((0.5, 0.5))

    def test_from_collect(self):
        """Import from collect_gltf_textures output."""
        tex = _b64_png(200, 100, 50)
        mat1 = _sample(name="a", values={"color": [0.8, 0.2, 0.1]}, textures={"color": tex})
        mat2 = _sample(name="b", values={"metalness": 0.9})
        g = collect_gltf_textures({"a": mat1, "b": mat2})

        imported = PbrProperties.from_gltf(g)
        assert "a" in imported
        assert "b" in imported
        assert imported["a"].values.color is not None or imported["a"].maps.color is not None
        assert imported["a"].maps.color == tex
        assert imported["b"].values.metalness == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_values_preserved(self):
        mat = _sample(values={
            "color": [0.8, 0.2, 0.1],
            "metalness": 0.9,
            "roughness": 0.4,
            "ior": 1.45,
            "clearcoat": 0.5,
            "clearcoatRoughness": 0.1,
        })
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.color == pytest.approx([0.8, 0.2, 0.1])
        assert imported.values.metalness == pytest.approx(0.9)
        assert imported.values.roughness == pytest.approx(0.4)
        assert imported.values.ior == pytest.approx(1.45)
        assert imported.values.clearcoat == pytest.approx(0.5)

    def test_texture_preserved(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(
            values={"color": [1, 1, 1], "metalness": 1.0},
            textures={"color": tex},
        )
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.maps.color == tex

    def test_texture_rotation_round_trip(self):
        """rotation in degrees → KHR_texture_transform.rotation in radians →
        back to degrees on import. Lossless within float precision."""
        import math
        tex = _b64_png(200, 100, 50)
        mat = _sample(
            values={"color": [1, 1, 1]},
            textures={"color": tex},
        ).scale(2, 2, rotation=90)
        g = mat.to_gltf()
        # Spec compliance: wire is radians
        bct = g.materials[0].pbrMetallicRoughness.baseColorTexture
        transform = bct.extensions["KHR_texture_transform"]
        assert transform["rotation"] == pytest.approx(math.pi / 2, abs=1e-6)
        assert transform["scale"] == [0.5, 0.5]
        # Round-trip back to degrees
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.texture_rotation == pytest.approx(90, abs=1e-6)
        assert imported.texture_repeat == (0.5, 0.5)

    def test_texture_preserved_through_binary_buffer_view(self):
        """Regression: .glb files store images in bufferViews (no URI). The
        previous from_gltf code only handled URI-based images and silently
        dropped bufferView-stored ones, leaving PbrMaps() empty."""
        from threejs_materials.gltf import _embed_data_uri_images
        tex = _b64_png(200, 100, 50)
        mat = _sample(
            values={"color": [1, 1, 1], "metalness": 1.0},
            textures={"color": tex, "normal": _b64_png(0, 0, 255)},
        )
        g = mat.to_gltf()
        # Mimic the .glb shape: convert data-URI images to bufferView storage.
        _embed_data_uri_images(g)
        assert all(img.bufferView is not None and img.uri is None for img in g.images)
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.maps.color is not None and imported.maps.color.startswith("data:")
        assert imported.maps.normal is not None and imported.maps.normal.startswith("data:")

    def test_export_reimport_reexport_stable(self):
        """export → import → export is stable (second round-trip is identical)."""
        mat = _sample(values={
            "color": [0.8, 0.2, 0.1],
            "metalness": 0.9,
            "roughness": 0.4,
            "ior": 1.45,
        })
        g1 = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g1).values()))
        g2 = imported.to_gltf()
        pbr1 = g1.materials[0].pbrMetallicRoughness
        pbr2 = g2.materials[0].pbrMetallicRoughness
        assert pbr1 is not None and pbr2 is not None
        assert pbr1.baseColorFactor == pbr2.baseColorFactor
        assert pbr1.metallicFactor == pbr2.metallicFactor
        assert pbr1.roughnessFactor == pbr2.roughnessFactor
        assert g1.materials[0].extensions == g2.materials[0].extensions

    def test_color_midgray_round_trips_losslessly(self):
        """sRGB midgray exercises the gamma curve in both directions —
        catches sign or factor errors that pass on extreme values like (1,0,0)."""
        mat = _sample(values={"color": [0.5020, 0.5020, 0.5020]})  # sRGB 0x80
        g = mat.to_gltf()
        imported = next(iter(PbrProperties.from_gltf(g).values()))
        assert imported.values.color == pytest.approx([0.5020, 0.5020, 0.5020], abs=1e-3)

    def test_basecolorfactor_is_linear_on_wire(self):
        """Explicit assertion that the boundary conversion happens: input
        sRGB → wire linear (per glTF spec)."""
        from threejs_materials.utils import _srgb_to_linear
        mat = _sample(values={"color": [0.5, 0.7, 0.3]})  # sRGB
        g = mat.to_gltf()
        bcf = g.materials[0].pbrMetallicRoughness.baseColorFactor
        expected = [_srgb_to_linear(c) for c in [0.5, 0.7, 0.3]]
        assert bcf[:3] == pytest.approx(expected, abs=1e-6)


class TestStringColorExport:
    """Regression tests for the pre-existing string-color silent-drop bug
    in _build_pbr (now fixed: strings are normalized before linear conversion)."""

    def test_hex_string_color_exports(self):
        """`values.color = "#ff8000"` previously fell through the
        isinstance-list branch and exported as `[1.0, 1.0, 1.0]` — color lost.
        Now: normalized to sRGB then sRGB→linear at the boundary."""
        from threejs_materials.utils import _srgb_to_linear
        mat = _sample(values={"color": "#ff8000"})
        g = mat.to_gltf()
        bcf = g.materials[0].pbrMetallicRoughness.baseColorFactor
        # "#ff8000" sRGB byte ratios → (1.0, 0.502, 0.0) → linear via _srgb_to_linear
        expected = [
            _srgb_to_linear(1.0),
            _srgb_to_linear(0x80 / 255.0),
            _srgb_to_linear(0.0),
        ]
        assert bcf[:3] == pytest.approx(expected, abs=1e-3)

    def test_named_color_exports(self):
        from threejs_materials.utils import _srgb_to_linear
        mat = _sample(values={"color": "red"})
        g = mat.to_gltf()
        bcf = g.materials[0].pbrMetallicRoughness.baseColorFactor
        assert bcf[:3] == pytest.approx([_srgb_to_linear(1.0), 0.0, 0.0], abs=1e-3)


# ---------------------------------------------------------------------------
# save_gltf overwrite handling
# ---------------------------------------------------------------------------


class TestSaveGltf:
    def _mat_with_texture(self):
        tex = _b64_png(200, 100, 50)
        return _sample(values={"color": [1, 1, 1]}, textures={"color": tex})

    def test_creates_gltf_and_texture_dir(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        mat.save_gltf(out)
        assert out.exists()
        tex_dir = tmp_path / "wood"
        assert tex_dir.is_dir()
        assert any(tex_dir.iterdir())

    def test_creates_glb(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.glb"
        mat.save_gltf(out)
        assert out.exists()
        assert not (tmp_path / "wood").exists()

    def test_no_overwrite_file_exists(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        out.write_text("{}")
        with pytest.raises(FileExistsError, match="wood.gltf"):
            mat.save_gltf(out)

    def test_no_overwrite_tex_dir_exists(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        (tmp_path / "wood").mkdir()
        with pytest.raises(FileExistsError, match="wood"):
            mat.save_gltf(out)

    def test_no_overwrite_tex_dir_is_file(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        (tmp_path / "wood").write_text("oops")
        with pytest.raises(FileExistsError, match="wood"):
            mat.save_gltf(out)

    def test_overwrite_replaces_file(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        out.write_text("{}")
        mat.save_gltf(out, overwrite=True)
        assert out.stat().st_size > 2  # replaced with real content

    def test_overwrite_replaces_textures_in_dir(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        tex_dir = tmp_path / "wood"
        tex_dir.mkdir()
        (tex_dir / "stale.png").write_text("old")
        mat.save_gltf(out, overwrite=True)
        assert out.exists()
        assert tex_dir.is_dir()
        # New texture files written
        assert any(f.suffix == ".png" for f in tex_dir.iterdir())

    def test_overwrite_tex_dir_is_file_raises(self, tmp_path):
        mat = self._mat_with_texture()
        out = tmp_path / "wood.gltf"
        (tmp_path / "wood").write_text("oops")
        with pytest.raises(FileExistsError, match="not a directory"):
            mat.save_gltf(out, overwrite=True)

    def test_gltf_file_round_trip(self, tmp_path):
        """save_gltf → load_gltf preserves textures."""
        mat = self._mat_with_texture()
        out = tmp_path / "rt.gltf"
        mat.save_gltf(out)
        imported = next(iter(PbrProperties.load_gltf(str(out)).values()))
        assert imported.maps.color is not None
        assert imported.maps.color.startswith("data:")

    def test_glb_file_round_trip(self, tmp_path):
        """save_gltf(.glb) → load_gltf preserves textures."""
        mat = self._mat_with_texture()
        out = tmp_path / "rt.glb"
        mat.save_gltf(out)
        imported = next(iter(PbrProperties.load_gltf(str(out)).values()))
        assert imported.maps.color is not None
