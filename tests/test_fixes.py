"""Regression tests for specific bug fixes.

Each test documents which bug it covers so regressions are caught early.
"""

import base64
import io

import numpy as np
import pytest
from PIL import Image

from conftest import _make_1x1_png
from threejs_materials.gltf import (
    _pack_metallic_roughness,
    collect_gltf_textures,
    inject_materials,
)
from threejs_materials.library import PbrProperties
from threejs_materials.models import PbrMaps, PbrValues
from threejs_materials.utils import _open_texture_image, _resolve_to_data_uri


def _b64_png(r=128, g=128, b=128):
    data = _make_1x1_png(r, g, b)
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _make_1bit_png(tmp_path, value=True):
    """Create a 1-bit (mode '1') PNG and return its path."""
    img = Image.new("1", (4, 4), value)
    path = tmp_path / "onebit.png"
    img.save(path)
    return path


def _sample(name="mat", values=None, textures=None):
    return PbrProperties.from_dict({
        "id": name, "name": name, "source": "test",
        "url": "", "license": "CC0",
        "values": values or {"color": [0.5, 0.5, 0.5]},
        "textures": textures or {},
    })


# ---------------------------------------------------------------------------
# Fix: 1-bit boolean textures (commit d13ce30)
#
# Boolean PNGs (mode '1') had True=1 instead of 255 in numpy arrays,
# causing metallicRoughness packing to write near-zero metalness.
# ---------------------------------------------------------------------------


class TestOneBitTextures:
    def test_open_texture_image_converts_1bit_to_L(self, tmp_path):
        """_open_texture_image must convert 1-bit PNGs to mode L with 0/255 values."""
        path = _make_1bit_png(tmp_path, value=True)
        img = _open_texture_image(str(path.name), tmp_path)
        assert img.mode == "L"
        arr = np.array(img)
        assert arr.dtype == np.uint8
        assert arr.max() == 255

    def test_open_texture_image_converts_1bit_false(self, tmp_path):
        """1-bit False should become 0 in uint8."""
        path = _make_1bit_png(tmp_path, value=False)
        img = _open_texture_image(str(path.name), tmp_path)
        arr = np.array(img)
        assert arr.max() == 0

    def test_resolve_to_data_uri_converts_1bit(self, tmp_path):
        """_resolve_to_data_uri must re-encode 1-bit PNGs as 8-bit."""
        path = _make_1bit_png(tmp_path, value=True)
        uri = _resolve_to_data_uri(path.name, tmp_path)
        assert uri.startswith("data:image/png;base64,")
        # Decode and verify it's now 8-bit
        b64 = uri.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert img.mode == "L"
        assert np.array(img).max() == 255

    def test_pack_metallic_roughness_with_1bit_metalness(self, tmp_path):
        """Packed MR texture must have B=255 when metalness is a 1-bit True image."""
        path = _make_1bit_png(tmp_path, value=True)
        packed_uri = _pack_metallic_roughness(
            metalness_ref=path.name,
            roughness_ref=None,
            metalness_scalar=1.0,
            roughness_scalar=0.5,
            texture_dir=tmp_path,
        )
        b64 = packed_uri.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        arr = np.array(img)
        # B channel = metalness, should be 255 not 1
        assert arr[:, :, 2].max() == 255


# ---------------------------------------------------------------------------
# Fix: transmissive materials appearing opaque (commit 7cb7b9d)
#
# PhysicallyBased source didn't emit metalness/color for transmissive
# materials, causing glTF metallicFactor to default to 1.0 (mirror).
# Also, dispersion without KHR_materials_volume violated glTF spec.
# ---------------------------------------------------------------------------


class TestTransmissiveMaterials:
    def test_transmissive_has_metalness_zero_in_gltf(self):
        """Transmissive dielectric must have metallicFactor=0 in glTF.

        The fix was in physicallybased.py: always emit metalness=0 for
        non-metallic materials. This test verifies that when metalness=0
        is set, it survives into the glTF output (not overridden to 1.0).
        """
        mat = PbrProperties(
            id="glass", name="glass", source="test", url="", license="",
            values=PbrValues(metalness=0.0, transmission=1.0, ior=1.5, roughness=0.0),
        )
        gltf = mat.to_gltf()
        pbr = gltf.materials[0].pbrMetallicRoughness
        assert pbr.metallicFactor == 0.0

    def test_transmissive_has_base_color_in_gltf(self):
        """Transmissive material must have a baseColorFactor (not None)."""
        mat = PbrProperties(
            id="glass", name="glass", source="test", url="", license="",
            values=PbrValues(color=[1, 1, 1], transmission=1.0, ior=1.5),
        )
        gltf = mat.to_gltf()
        bcf = gltf.materials[0].pbrMetallicRoughness.baseColorFactor
        assert bcf is not None
        assert bcf[:3] == [1, 1, 1]

    def test_dispersion_adds_volume_extension(self):
        """Dispersion requires KHR_materials_volume per glTF spec."""
        mat = PbrProperties(
            id="prism", name="prism", source="test", url="", license="",
            values=PbrValues(transmission=1.0, dispersion=0.5, ior=1.5),
        )
        gltf = mat.to_gltf()
        exts = gltf.materials[0].extensions
        assert "KHR_materials_dispersion" in exts
        assert "KHR_materials_volume" in exts

    def test_dispersion_without_explicit_volume_gets_thickness_zero(self):
        """Auto-added volume extension should have thicknessFactor=0."""
        mat = PbrProperties(
            id="prism", name="prism", source="test", url="", license="",
            values=PbrValues(transmission=1.0, dispersion=0.5, ior=1.5),
        )
        gltf = mat.to_gltf()
        volume = gltf.materials[0].extensions["KHR_materials_volume"]
        assert volume["thicknessFactor"] == 0

    def test_explicit_volume_not_overwritten_by_dispersion(self):
        """When volume is already present, dispersion should not replace it."""
        mat = PbrProperties(
            id="glass", name="glass", source="test", url="", license="",
            values=PbrValues(transmission=1.0, dispersion=0.5, ior=1.5, thickness=2.0),
        )
        gltf = mat.to_gltf()
        volume = gltf.materials[0].extensions["KHR_materials_volume"]
        assert volume["thicknessFactor"] == 2.0


# ---------------------------------------------------------------------------
# Fix: inject_materials collapsing same-name materials (commit 6163351)
#
# Two materials with the same name but different values (e.g. color
# overrides of "Car Paint") were deduplicated into one glTF material.
# ---------------------------------------------------------------------------


class TestInjectMaterialsNameCollision:
    def test_same_name_different_colors_both_survive(self):
        """Two materials named identically but with different colors must produce two glTF materials."""
        mat_a = _sample("shared_name", values={"color": [1, 0, 0]})
        mat_b = _sample("shared_name", values={"color": [0, 1, 0]})

        gltf = collect_gltf_textures({"a": mat_a, "b": mat_b})
        assert len(gltf.materials) == 2

        colors = [
            m.pbrMetallicRoughness.baseColorFactor[:3]
            for m in gltf.materials
        ]
        assert [1, 0, 0] in colors
        assert [0, 1, 0] in colors

    def test_same_name_different_textures_both_survive(self):
        """Same name, different textures — must not collapse."""
        tex_a = _b64_png(200, 0, 0)
        tex_b = _b64_png(0, 200, 0)

        mat_a = _sample("shared", values={"color": [1, 1, 1]}, textures={"color": tex_a})
        mat_b = _sample("shared", values={"color": [1, 1, 1]}, textures={"color": tex_b})

        gltf = collect_gltf_textures({"a": mat_a, "b": mat_b})
        assert len(gltf.materials) == 2


# ---------------------------------------------------------------------------
# Fix: no-op KHR_texture_transform with scale (1,1)
#
# scale(1,1) should not add a redundant KHR_texture_transform extension.
# ---------------------------------------------------------------------------


class TestNoOpTextureTransform:
    def test_scale_1_1_no_transform_extension(self):
        """scale(1, 1) must not produce KHR_texture_transform in glTF."""
        tex = _b64_png()
        mat = _sample(textures={"color": tex}).scale(1, 1)
        gltf = mat.to_gltf()
        bct = gltf.materials[0].pbrMetallicRoughness.baseColorTexture
        assert bct is not None
        assert not bct.extensions  # empty dict or no KHR_texture_transform

    def test_scale_2_2_has_transform_extension(self):
        """scale(2, 2) must produce KHR_texture_transform."""
        tex = _b64_png()
        mat = _sample(textures={"color": tex}).scale(2, 2)
        gltf = mat.to_gltf()
        bct = gltf.materials[0].pbrMetallicRoughness.baseColorTexture
        assert "KHR_texture_transform" in bct.extensions
        assert bct.extensions["KHR_texture_transform"]["scale"] == pytest.approx([0.5, 0.5])


# ---------------------------------------------------------------------------
# Fix: inject_materials index out of range
#
# Requesting material indices beyond the current array length must not crash.
# ---------------------------------------------------------------------------


class TestInjectMaterialsPadArray:
    def _make_test_glb(self, path):
        """Create a minimal valid GLB with 1 material, 2 meshes, 3 nodes."""
        import struct
        from pygltflib import (
            GLTF2, Accessor, Attributes, Buffer, BufferView,
            Mesh, Node, Primitive, Scene,
        )
        from pygltflib import Material as GltfMaterial

        # 3 vertices (a triangle) as float32 VEC3
        vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0]
        vbytes = struct.pack("<9f", *vertices)

        gltf = GLTF2()
        gltf.asset.generator = "test"
        gltf.materials = [GltfMaterial(name="placeholder")]
        gltf.buffers = [Buffer(byteLength=len(vbytes) * 2)]
        gltf.set_binary_blob(vbytes * 2)
        gltf.bufferViews = [
            BufferView(buffer=0, byteOffset=0, byteLength=len(vbytes)),
            BufferView(buffer=0, byteOffset=len(vbytes), byteLength=len(vbytes)),
        ]
        gltf.accessors = [
            Accessor(bufferView=0, componentType=5126, count=3, type="VEC3",
                     min=[0, 0, 0], max=[1, 1, 0]),
            Accessor(bufferView=1, componentType=5126, count=3, type="VEC3",
                     min=[0, 0, 0], max=[1, 1, 0]),
        ]
        gltf.meshes = [
            Mesh(primitives=[Primitive(attributes=Attributes(POSITION=0), material=0)]),
            Mesh(primitives=[Primitive(attributes=Attributes(POSITION=1), material=0)]),
        ]
        gltf.nodes = [Node(children=[1, 2]), Node(mesh=0), Node(mesh=1)]
        gltf.scenes = [Scene(nodes=[0])]
        gltf.scene = 0
        gltf.save_binary(str(path))

    def test_pad_materials_array(self, tmp_path):
        """inject_materials must pad the materials array when indices exceed length."""
        path = tmp_path / "test.glb"
        self._make_test_glb(path)

        mat_a = _sample("mat_a", values={"color": [1, 0, 0]})
        mat_a.normalize_uvs = False
        mat_b = _sample("mat_b", values={"color": [0, 1, 0]})
        mat_b.normalize_uvs = False

        # Node 1 and 2 get different materials — requires indices 0 and 1
        # but original file only has 1 material slot
        inject_materials(str(path), {1: mat_a, 2: mat_b})

        from pygltflib import GLTF2
        result = GLTF2.load(str(path))
        assert len(result.materials) >= 2


# ---------------------------------------------------------------------------
# Fix: always bake procedural MaterialX materials (uncommitted)
#
# Materials without textures but with procedural node graphs (e.g. "Brass")
# had their colors lost because baking was skipped.
# This test uses a minimal MaterialX document with a constant color node.
# ---------------------------------------------------------------------------


def _materialx_available():
    try:
        from threejs_materials.utils import ensure_materialx
        ensure_materialx()
        return True
    except ImportError:
        return False


class TestAlwaysBakeProcedural:
    @pytest.mark.skipif(
        not _materialx_available(),
        reason="MaterialX not installed",
    )
    def test_bake_called_without_textures(self):
        """_process_mtlx must bake even when the original has no textures.

        Procedural materials (e.g. GPUOpen "Brass") have colors in shader
        node graphs, not flat params. The baker resolves these to scalars.
        We use a .mtlx with flat base_color (no textures) to verify
        baking still runs and the color survives.
        """
        import tempfile
        from pathlib import Path
        from threejs_materials.convert import _process_mtlx

        mtlx_content = """\
<?xml version="1.0" encoding="utf-8"?>
<materialx version="1.38">
  <standard_surface name="SR_brass" type="surfaceshader">
    <input name="base" type="float" value="1.0" />
    <input name="base_color" type="color3" value="0.95, 0.79, 0.37" />
    <input name="metalness" type="float" value="1.0" />
    <input name="specular_roughness" type="float" value="0.2" />
  </standard_surface>
  <surfacematerial name="Brass" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="SR_brass" />
  </surfacematerial>
</materialx>
"""
        with tempfile.TemporaryDirectory() as tmp:
            mtlx_path = Path(tmp) / "brass.mtlx"
            mtlx_path.write_text(mtlx_content)
            properties, _, _ = _process_mtlx(mtlx_path)

        color = properties.get("color", {}).get("value")
        assert color is not None, "color must be present after baking"
        assert color[0] > 0.9, f"expected brass red ~0.95, got {color[0]}"
        assert color[2] < 0.5, f"expected brass blue ~0.37, got {color[2]}"
