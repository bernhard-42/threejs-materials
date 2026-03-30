"""Tests for glTF export (collect_gltf_textures) and import (from_gltf)."""

import base64

import pytest

from conftest import _make_1x1_png
from threejs_materials.library import Material, collect_gltf_textures


def _b64_png(r=128, g=128, b=128):
    data = _make_1x1_png(r, g, b)
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _sample(name="mat", **prop_overrides):
    props = {"color": {"value": [0.5, 0.5, 0.5]}}
    props.update(prop_overrides)
    return Material({
        "id": name, "name": name, "source": "test",
        "url": "", "license": "CC0", "properties": props,
    })


# ---------------------------------------------------------------------------
# collect_gltf_textures
# ---------------------------------------------------------------------------


class TestCollectGltfTextures:
    def test_single_material(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(name="body", color={"value": [1, 1, 1], "texture": tex})
        g = collect_gltf_textures({"body": mat})
        assert len(g.materials) == 1
        assert g.materials[0].name == "body"
        assert len(g.images) == 1
        assert g.images[0].uri == tex

    def test_multiple_materials(self):
        tex1 = _b64_png(200, 100, 50)
        tex2 = _b64_png(50, 100, 200)
        mat1 = _sample(name="a", color={"value": [1, 1, 1], "texture": tex1})
        mat2 = _sample(name="b", color={"value": [1, 1, 1], "texture": tex2})
        g = collect_gltf_textures({"a": mat1, "b": mat2})
        assert len(g.materials) == 2
        assert len(g.images) == 2
        assert g.materials[0].name == "a"
        assert g.materials[1].name == "b"

    def test_texture_deduplication(self):
        tex = _b64_png(200, 100, 50)
        mat1 = _sample(name="a", color={"value": [1, 1, 1], "texture": tex})
        mat2 = _sample(name="b", color={"value": [0.5, 0.5, 0.5], "texture": tex})
        g = collect_gltf_textures({"a": mat1, "b": mat2})
        # Same texture → deduplicated to one image
        assert len(g.images) == 1
        # Both materials reference index 0
        idx_a = g.materials[0].pbrMetallicRoughness.baseColorTexture.index
        idx_b = g.materials[1].pbrMetallicRoughness.baseColorTexture.index
        assert idx_a == idx_b == 0

    def test_no_textures(self):
        mat = _sample(name="gold", color={"value": [1, 0.8, 0.3]})
        g = collect_gltf_textures({"gold": mat})
        assert len(g.images) == 0
        assert len(g.materials) == 1

    def test_extensions_used_merged(self):
        mat1 = _sample(name="a", ior={"value": 1.45})
        mat2 = _sample(name="b", transmission={"value": 0.8})
        g = collect_gltf_textures({"a": mat1, "b": mat2})
        assert "KHR_materials_ior" in g.extensionsUsed
        assert "KHR_materials_transmission" in g.extensionsUsed

    def test_samplers_present(self):
        tex = _b64_png()
        mat = _sample(name="x", color={"texture": tex})
        g = collect_gltf_textures({"x": mat})
        assert len(g.samplers) == 1
        assert g.samplers[0].magFilter == 9729

    def test_textures_array(self):
        tex = _b64_png()
        mat = _sample(name="x", color={"texture": tex})
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
        mat = _sample(name="tiled", color={"texture": tex}).scale(2, 2)
        g = collect_gltf_textures({"tiled": mat})
        bc_tex = g.materials[0].pbrMetallicRoughness.baseColorTexture
        assert bc_tex.extensions["KHR_texture_transform"]["scale"] == [0.5, 0.5]
        assert "KHR_texture_transform" in g.extensionsUsed


# ---------------------------------------------------------------------------
# Material.from_gltf
# ---------------------------------------------------------------------------


class TestFromGltf:
    def test_basic_pbr(self):
        mat = _sample(
            color={"value": [0.8, 0.2, 0.1]},
            metalness={"value": 0.9},
            roughness={"value": 0.4},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["color"]["value"] == pytest.approx([0.8, 0.2, 0.1])
        assert imported.properties["metalness"]["value"] == pytest.approx(0.9)
        assert imported.properties["roughness"]["value"] == pytest.approx(0.4)
        assert imported.source == "gltf"

    def test_texture_resolved(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(color={"value": [1, 1, 1], "texture": tex})
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["color"]["texture"] == tex

    def test_alpha_blend(self):
        mat = _sample(
            color={"value": [1, 1, 1]},
            opacity={"value": 0.5},
            transparent={"value": True},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["transparent"]["value"] is True

    def test_alpha_mask(self):
        mat = _sample(alphaTest={"value": 0.3})
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["alphaTest"]["value"] == pytest.approx(0.3)

    def test_double_sided(self):
        mat = _sample(side={"value": 2})
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["side"]["value"] == 2

    def test_ior(self):
        mat = _sample(ior={"value": 1.45})
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["ior"]["value"] == pytest.approx(1.45)

    def test_transmission(self):
        mat = _sample(transmission={"value": 0.8})
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["transmission"]["value"] == pytest.approx(0.8)

    def test_clearcoat(self):
        mat = _sample(
            clearcoat={"value": 0.8},
            clearcoatRoughness={"value": 0.1},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["clearcoat"]["value"] == pytest.approx(0.8)
        assert imported.properties["clearcoatRoughness"]["value"] == pytest.approx(0.1)

    def test_sheen(self):
        mat = _sample(
            sheen={"value": 1.0},
            sheenColor={"value": [0.9, 0.8, 0.7]},
            sheenRoughness={"value": 0.3},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["sheenColor"]["value"] == pytest.approx([0.9, 0.8, 0.7])
        assert imported.properties["sheenRoughness"]["value"] == pytest.approx(0.3)

    def test_iridescence(self):
        mat = _sample(
            iridescence={"value": 1.0},
            iridescenceIOR={"value": 1.3},
            iridescenceThicknessRange={"value": [100.0, 400.0]},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["iridescence"]["value"] == pytest.approx(1.0)
        assert imported.properties["iridescenceIOR"]["value"] == pytest.approx(1.3)
        assert imported.properties["iridescenceThicknessRange"]["value"] == pytest.approx([100.0, 400.0])

    def test_specular(self):
        mat = _sample(
            specularIntensity={"value": 0.8},
            specularColor={"value": [1.0, 0.9, 0.8]},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["specularIntensity"]["value"] == pytest.approx(0.8)
        assert imported.properties["specularColor"]["value"] == pytest.approx([1.0, 0.9, 0.8])

    def test_dispersion(self):
        mat = _sample(dispersion={"value": 0.5})
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["dispersion"]["value"] == pytest.approx(0.5)

    def test_emissive_strength(self):
        mat = _sample(
            emissive={"value": [1, 1, 1]},
            emissiveIntensity={"value": 2.0},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["emissiveIntensity"]["value"] == pytest.approx(2.0)

    def test_texture_repeat_restored(self):
        tex = _b64_png()
        mat = _sample(color={"texture": tex}).scale(2, 2)
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.texture_repeat == pytest.approx((0.5, 0.5))

    def test_from_collect(self):
        """Import from collect_gltf_textures output."""
        tex = _b64_png(200, 100, 50)
        mat1 = _sample(name="a", color={"value": [0.8, 0.2, 0.1], "texture": tex})
        mat2 = _sample(name="b", metalness={"value": 0.9})
        g = collect_gltf_textures({"a": mat1, "b": mat2})

        imported = Material.from_gltf(g)
        assert "a" in imported
        assert "b" in imported
        assert "color" in imported["a"].properties
        assert imported["a"].properties["color"]["texture"] == tex
        assert imported["b"].properties["metalness"]["value"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_values_preserved(self):
        mat = _sample(
            color={"value": [0.8, 0.2, 0.1]},
            metalness={"value": 0.9},
            roughness={"value": 0.4},
            ior={"value": 1.45},
            clearcoat={"value": 0.5},
            clearcoatRoughness={"value": 0.1},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["color"]["value"] == pytest.approx([0.8, 0.2, 0.1])
        assert imported.properties["metalness"]["value"] == pytest.approx(0.9)
        assert imported.properties["roughness"]["value"] == pytest.approx(0.4)
        assert imported.properties["ior"]["value"] == pytest.approx(1.45)
        assert imported.properties["clearcoat"]["value"] == pytest.approx(0.5)

    def test_texture_preserved(self):
        tex = _b64_png(200, 100, 50)
        mat = _sample(
            color={"value": [1, 1, 1], "texture": tex},
            metalness={"value": 1.0},
        )
        g = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g).values()))
        assert imported.properties["color"]["texture"] == tex

    def test_export_reimport_reexport_stable(self):
        """export → import → export is stable (second round-trip is identical)."""
        mat = _sample(
            color={"value": [0.8, 0.2, 0.1]},
            metalness={"value": 0.9},
            roughness={"value": 0.4},
            ior={"value": 1.45},
        )
        g1 = mat.to_gltf()
        imported = next(iter(Material.from_gltf(g1).values()))
        g2 = imported.to_gltf()
        m1 = g1.materials[0]
        m2 = g2.materials[0]
        assert m1.pbrMetallicRoughness.baseColorFactor == m2.pbrMetallicRoughness.baseColorFactor
        assert m1.pbrMetallicRoughness.metallicFactor == m2.pbrMetallicRoughness.metallicFactor
        assert m1.pbrMetallicRoughness.roughnessFactor == m2.pbrMetallicRoughness.roughnessFactor
        assert m1.extensions == m2.extensions


# ---------------------------------------------------------------------------
# save_gltf overwrite handling
# ---------------------------------------------------------------------------


class TestSaveGltf:
    def _mat_with_texture(self):
        tex = _b64_png(200, 100, 50)
        return _sample(color={"value": [1, 1, 1], "texture": tex})

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
        imported = next(iter(Material.load_gltf(str(out)).values()))
        assert "texture" in imported.properties["color"]
        assert imported.properties["color"]["texture"].startswith("data:")

    def test_glb_file_round_trip(self, tmp_path):
        """save_gltf(.glb) → load_gltf preserves textures."""
        mat = self._mat_with_texture()
        out = tmp_path / "rt.glb"
        mat.save_gltf(out)
        imported = next(iter(Material.load_gltf(str(out)).values()))
        assert "texture" in imported.properties["color"]
