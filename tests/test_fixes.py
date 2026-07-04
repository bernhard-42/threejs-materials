"""Regression tests for specific bug fixes.

Each test documents which bug it covers so regressions are caught early.
"""

import base64
import importlib.util
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
from threejs_materials.models import PbrValues
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
# Fix: 16-bit textures crash _pack_metallic_roughness
#
# Baker output for EXR-sourced inputs comes back as PIL mode "I;16" → uint16
# numpy arrays. The packed RGB stack inherited that dtype, which PIL rejects
# with `Cannot handle this data type: (H, W, 3), <u2`. glTF metallicRoughness
# is 8-bit per spec; downconvert at the channel-extraction boundary.
# ---------------------------------------------------------------------------


def _make_16bit_png(tmp_path, name="u16.png", value=40000):
    """Create a 16-bit single-channel PNG (mode 'I;16') and return its path."""
    arr = np.full((4, 4), value, dtype=np.uint16)
    img = Image.fromarray(arr, mode="I;16")
    path = tmp_path / name
    img.save(path)
    return path


class TestSixteenBitTextures:
    def test_pack_metallic_roughness_with_16bit_inputs(self, tmp_path):
        """uint16 metalness + uint16 roughness must produce an 8-bit MR PNG."""
        m_path = _make_16bit_png(tmp_path, "m16.png", value=65535)  # full metalness
        r_path = _make_16bit_png(tmp_path, "r16.png", value=32896)  # ~ 0.502
        packed_uri = _pack_metallic_roughness(
            metalness_ref=m_path.name,
            roughness_ref=r_path.name,
            metalness_scalar=1.0,
            roughness_scalar=1.0,
            texture_dir=tmp_path,
        )
        b64 = packed_uri.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert img.mode == "RGB"
        arr = np.array(img)
        assert arr.dtype == np.uint8
        # B channel = metalness ~255, G channel = roughness ~128 (32896 // 257 ≈ 128)
        assert arr[:, :, 2].max() == 255
        assert 120 < arr[:, :, 1].max() < 135

    def test_pack_metallic_roughness_export_does_not_crash(self, tmp_path):
        """End-to-end: a material with 16-bit metalness map exports without error."""
        m_path = _make_16bit_png(tmp_path, "m16.png", value=65535)
        mat = PbrProperties.from_dict({
            "id": "u16", "name": "u16", "source": "test", "url": "", "license": "CC0",
            "values": {"metalness": 1.0, "roughness": 0.5},
            "textures": {"metalness": m_path.name},
            "maps_dir": str(tmp_path),
        })
        gltf = mat.to_gltf()
        pbr = gltf.materials[0].pbrMetallicRoughness
        assert pbr is not None and pbr.metallicRoughnessTexture is not None


# ---------------------------------------------------------------------------
# Fix: EXR-format texture inputs lost through MaterialX TextureBaker
#
# MaterialX 1.39 TextureBaker can't read .exr files. When a polyhaven .mtlx
# references EXR for normal/rough (their default format for those slots),
# the baker silently falls back to the <input name="default"> scalar of
# the image node — turning a real bake into a flat constant. _transcode_exr_inputs
# pre-converts EXR → 8-bit linear PNG and rewrites the doc so the baker
# sees a format it can process.
# ---------------------------------------------------------------------------


def _write_grayscale_exr(path, value=0.5, size=(8, 8)):
    """Write a 4x4 single-channel ('Y') float16 EXR with a constant value."""
    import OpenEXR
    arr = np.full(size, value, dtype=np.float16)
    hdr = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    with OpenEXR.File(hdr, {"Y": arr}) as f:
        f.write(str(path))


def _write_rgba_exr(path, rgb=(0.5, 0.5, 1.0), size=(8, 8)):
    """Write a 4x4 'RGBA' float16 EXR with a constant pixel value."""
    import OpenEXR
    arr = np.zeros((*size, 4), dtype=np.float16)
    arr[..., 0] = rgb[0]
    arr[..., 1] = rgb[1]
    arr[..., 2] = rgb[2]
    arr[..., 3] = 1.0
    hdr = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    with OpenEXR.File(hdr, {"RGBA": arr}) as f:
        f.write(str(path))


class TestExrTranscode:
    """EXR transcode path — only runs when the optional openexr dep is installed."""

    pytestmark = pytest.mark.skipif(
        importlib.util.find_spec("OpenEXR") is None,
        reason="openexr is an optional dependency and is not installed",
    )

    def test_exr_to_png_grayscale(self, tmp_path):
        """Single-channel ('Y') EXR transcodes to 8-bit grayscale PNG with
        the same linear pixel value (0.5 → 128 ± 1)."""
        from threejs_materials.convert import _exr_to_png
        exr = tmp_path / "rough.exr"
        png = tmp_path / "rough.png"
        _write_grayscale_exr(exr, value=0.5)
        _exr_to_png(exr, png)
        img = Image.open(png)
        assert img.mode == "L"
        arr = np.array(img)
        assert 127 <= arr.max() <= 129

    def test_exr_to_png_rgba(self, tmp_path):
        """RGBA EXR transcodes to 8-bit RGB PNG (alpha dropped)."""
        from threejs_materials.convert import _exr_to_png
        exr = tmp_path / "normal.exr"
        png = tmp_path / "normal.png"
        _write_rgba_exr(exr, rgb=(0.5, 0.5, 1.0))
        _exr_to_png(exr, png)
        img = Image.open(png)
        assert img.mode == "RGB"
        arr = np.array(img)
        assert 127 <= arr[..., 0].max() <= 129
        assert 127 <= arr[..., 1].max() <= 129
        assert arr[..., 2].max() == 255

    def test_transcode_exr_inputs_rewrites_doc(self, tmp_path):
        """After running _transcode_exr_inputs, image nodes that referenced
        .exr now reference .png, the .png exists on disk, and the file
        input has colorspace='lin_rec709' set."""
        from threejs_materials.convert import _transcode_exr_inputs, load_document_with_stdlib
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        _write_grayscale_exr(tex_dir / "rough.exr", value=0.3)
        _write_rgba_exr(tex_dir / "normal.exr", rgb=(0.5, 0.5, 1.0))
        mtlx = tmp_path / "m.mtlx"
        mtlx.write_text("""<?xml version="1.0"?>
<materialx version="1.39">
  <nodegraph name="NG">
    <image name="rough" type="float">
      <input name="file" type="filename" value="textures/rough.exr" />
    </image>
    <image name="normal" type="vector3">
      <input name="file" type="filename" value="textures/normal.exr" />
    </image>
  </nodegraph>
</materialx>
""")
        doc, _ = load_document_with_stdlib(mtlx)
        _transcode_exr_inputs(doc, tmp_path)

        assert (tex_dir / "rough.png").exists()
        assert (tex_dir / "normal.png").exists()

        ng = doc.getNodeGraph("NG")
        rough_file = ng.getNode("rough").getInput("file")
        normal_file = ng.getNode("normal").getInput("file")
        assert rough_file.getValueString() == "textures/rough.png"
        assert normal_file.getValueString() == "textures/normal.png"
        assert rough_file.getColorSpace() == "lin_rec709"
        assert normal_file.getColorSpace() == "lin_rec709"

    def test_transcode_preserves_existing_colorspace(self, tmp_path):
        """When an .exr image already declares a colorspace, transcode must
        not overwrite it."""
        from threejs_materials.convert import _transcode_exr_inputs, load_document_with_stdlib
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        _write_grayscale_exr(tex_dir / "rough.exr", value=0.3)
        mtlx = tmp_path / "m.mtlx"
        mtlx.write_text("""<?xml version="1.0"?>
<materialx version="1.39">
  <nodegraph name="NG">
    <image name="rough" type="float">
      <input name="file" type="filename" value="textures/rough.exr" colorspace="srgb_texture" />
    </image>
  </nodegraph>
</materialx>
""")
        doc, _ = load_document_with_stdlib(mtlx)
        _transcode_exr_inputs(doc, tmp_path)
        rough_file = doc.getNodeGraph("NG").getNode("rough").getInput("file")
        assert rough_file.getColorSpace() == "srgb_texture"  # unchanged


# ---------------------------------------------------------------------------
# Fix: SourceResult.extra_textures (e.g. polyhaven AO) merged into properties
#
# The .mtlx graph for polyhaven materials doesn't reference AO (no AO input
# on standard_surface). Polyhaven exposes AO at a sibling URL; the loader
# downloads it and lists it on SourceResult.extra_textures. _SourceLoader
# merges those into the properties dict so they survive into the cache.
# ---------------------------------------------------------------------------


class TestExtraTextures:
    def test_source_result_default(self):
        """SourceResult.extra_textures defaults to an empty dict."""
        from threejs_materials.sources.common import SourceResult
        r = SourceResult()
        assert r.extra_textures == {}

    def test_extra_textures_field_carries_path(self):
        """SourceResult accepts and stores arbitrary {prop_name: Path} entries."""
        from pathlib import Path
        from threejs_materials.sources.common import SourceResult
        r = SourceResult(extra_textures={"ao": Path("/tmp/ao.png")})
        assert r.extra_textures["ao"] == Path("/tmp/ao.png")


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
        assert pbr is not None
        assert pbr.metallicFactor == 0.0

    def test_transmissive_has_base_color_in_gltf(self):
        """Transmissive material must have a baseColorFactor (not None)."""
        mat = PbrProperties(
            id="glass", name="glass", source="test", url="", license="",
            values=PbrValues(color=[1, 1, 1], transmission=1.0, ior=1.5),
        )
        gltf = mat.to_gltf()
        pbr = gltf.materials[0].pbrMetallicRoughness
        assert pbr is not None
        bcf = pbr.baseColorFactor
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
        assert exts is not None
        assert "KHR_materials_dispersion" in exts
        assert "KHR_materials_volume" in exts

    def test_dispersion_without_explicit_volume_gets_thickness_zero(self):
        """Auto-added volume extension should have thicknessFactor=0."""
        mat = PbrProperties(
            id="prism", name="prism", source="test", url="", license="",
            values=PbrValues(transmission=1.0, dispersion=0.5, ior=1.5),
        )
        gltf = mat.to_gltf()
        exts = gltf.materials[0].extensions
        assert exts is not None
        volume = exts["KHR_materials_volume"]
        assert volume["thicknessFactor"] == 0

    def test_explicit_volume_not_overwritten_by_dispersion(self):
        """When volume is already present, dispersion should not replace it."""
        mat = PbrProperties(
            id="glass", name="glass", source="test", url="", license="",
            values=PbrValues(transmission=1.0, dispersion=0.5, ior=1.5, thickness=2.0),
        )
        gltf = mat.to_gltf()
        exts = gltf.materials[0].extensions
        assert exts is not None
        volume = exts["KHR_materials_volume"]
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

        colors = []
        for m in gltf.materials:
            pbr = m.pbrMetallicRoughness
            assert pbr is not None and pbr.baseColorFactor is not None
            colors.append(pbr.baseColorFactor[:3])
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

    def test_inject_into_gltf_ascii(self, tmp_path):
        """inject_materials must work with .gltf files that have external .bin buffers."""
        import struct
        from pygltflib import (
            GLTF2, Accessor, Attributes, Buffer, BufferView,
            Mesh, Node, Primitive, Scene,
        )
        from pygltflib import Material as GltfMaterial

        vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0]
        vbytes = struct.pack("<9f", *vertices)

        gltf = GLTF2()
        gltf.asset.generator = "test"
        gltf.materials = [GltfMaterial(name="placeholder")]
        # Write binary data to external .bin file
        bin_path = tmp_path / "test.bin"
        bin_path.write_bytes(vbytes)
        gltf.buffers = [Buffer(byteLength=len(vbytes), uri="test.bin")]
        gltf.bufferViews = [
            BufferView(buffer=0, byteOffset=0, byteLength=len(vbytes)),
        ]
        gltf.accessors = [
            Accessor(bufferView=0, componentType=5126, count=3, type="VEC3",
                     min=[0, 0, 0], max=[1, 1, 0]),
        ]
        gltf.meshes = [
            Mesh(primitives=[Primitive(attributes=Attributes(POSITION=0), material=0)]),
        ]
        gltf.nodes = [Node(mesh=0)]
        gltf.scenes = [Scene(nodes=[0])]
        gltf.scene = 0

        gltf_path = str(tmp_path / "test.gltf")
        gltf.save(gltf_path)

        mat = _sample("test_mat", values={"color": [1, 0, 0]})
        # Should not crash with "a bytes-like object is required, not 'NoneType'"
        inject_materials(gltf_path, {0: mat})

        result = GLTF2.load(gltf_path)
        assert len(result.materials) >= 1
        assert result.materials[0].name == "test_mat"


# ---------------------------------------------------------------------------
# Round-trip tests: inject_materials across .gltf/.glb formats
#
# Verify that materials survive: save → load → verify for all combinations
# of input format (.gltf+.bin, .glb) and output format.
# ---------------------------------------------------------------------------


class TestInjectMaterialsRoundTrip:
    """Round-trip tests for inject_materials across .gltf and .glb formats."""

    @staticmethod
    def _make_gltf_object():
        """Build a GLTF2 object with 2 nodes, position + UV data, 1 placeholder material."""
        import struct
        from pygltflib import (
            GLTF2, Accessor, Attributes, Buffer, BufferView,
            Mesh, Node, Primitive, Scene,
        )
        from pygltflib import Material as GltfMaterial

        # Triangle: 3 verts with positions and UVs
        positions = [0, 0, 0, 10, 0, 0, 0, 10, 0]
        uvs = [0, 0, 10, 0, 0, 10]
        pos_bytes = struct.pack("<9f", *positions)
        uv_bytes = struct.pack("<6f", *uvs)
        blob = (pos_bytes + uv_bytes) * 2  # duplicate for 2 meshes

        chunk = len(pos_bytes) + len(uv_bytes)

        gltf = GLTF2()
        gltf.asset.generator = "test"
        gltf.materials = [GltfMaterial(name="placeholder")]
        gltf.buffers = [Buffer(byteLength=len(blob))]
        gltf.bufferViews = [
            BufferView(buffer=0, byteOffset=0, byteLength=len(pos_bytes)),
            BufferView(buffer=0, byteOffset=len(pos_bytes), byteLength=len(uv_bytes)),
            BufferView(buffer=0, byteOffset=chunk, byteLength=len(pos_bytes)),
            BufferView(buffer=0, byteOffset=chunk + len(pos_bytes), byteLength=len(uv_bytes)),
        ]
        gltf.accessors = [
            Accessor(bufferView=0, componentType=5126, count=3, type="VEC3",
                     min=[0, 0, 0], max=[10, 10, 0]),
            Accessor(bufferView=1, componentType=5126, count=3, type="VEC2",
                     min=[0, 0], max=[10, 10]),
            Accessor(bufferView=2, componentType=5126, count=3, type="VEC3",
                     min=[0, 0, 0], max=[10, 10, 0]),
            Accessor(bufferView=3, componentType=5126, count=3, type="VEC2",
                     min=[0, 0], max=[10, 10]),
        ]
        gltf.meshes = [
            Mesh(primitives=[Primitive(
                attributes=Attributes(POSITION=0, TEXCOORD_0=1), material=0)]),
            Mesh(primitives=[Primitive(
                attributes=Attributes(POSITION=2, TEXCOORD_0=3), material=0)]),
        ]
        gltf.nodes = [Node(children=[1, 2]), Node(mesh=0), Node(mesh=1)]
        gltf.scenes = [Scene(nodes=[0])]
        gltf.scene = 0

        return gltf, blob

    def _save_as_gltf(self, gltf, blob, tmp_path):
        """Save as .gltf + .bin pair."""
        bin_path = tmp_path / "test.bin"
        bin_path.write_bytes(blob)
        gltf.buffers[0].uri = "test.bin"
        path = str(tmp_path / "test.gltf")
        gltf.save(path)
        return path

    def _save_as_glb(self, gltf, blob, tmp_path):
        """Save as .glb."""
        gltf.set_binary_blob(blob)
        gltf.buffers[0].uri = None
        path = str(tmp_path / "test.glb")
        gltf.save_binary(path)
        return path

    def _make_materials(self):
        """Create two distinct test materials with values and textures.

        Returns (mat_red, mat_green, expected_hashes) where expected_hashes
        maps texture name → sha256 hex digest of the source PNG bytes.
        Color and normal textures should survive the round-trip byte-identical.
        """
        import hashlib

        color_tex = _b64_png(200, 100, 50)
        normal_tex = _b64_png(128, 128, 255)
        rough_tex = _b64_png(180, 180, 180)

        def _hash_data_uri(uri):
            b64 = uri.split(",", 1)[1]
            return hashlib.sha256(base64.b64decode(b64)).hexdigest()

        expected = {
            "color": _hash_data_uri(color_tex),
            "normal": _hash_data_uri(normal_tex),
        }

        mat_red = _sample(
            "red_metal",
            values={"color": [0.8, 0.1, 0.1], "metalness": 1.0, "roughness": 1.0},
            textures={"roughness": rough_tex, "normal": normal_tex},
        )
        mat_red.normalize_uvs = False

        mat_green = _sample(
            "green_plastic",
            values={"color": [1.0, 1.0, 1.0], "metalness": 0.0, "roughness": 1.0, "ior": 1.45},
            textures={"color": color_tex, "roughness": rough_tex, "normal": normal_tex},
        )
        mat_green.normalize_uvs = False
        return mat_red, mat_green, expected

    def _verify_result(self, path, expected_hashes):
        """Load the file and verify both materials survived the round-trip."""
        import hashlib
        from pygltflib import GLTF2, ImageFormat

        result = GLTF2.load(path)

        # Convert file-referenced images to data URIs for uniform access
        if any(img.uri and not img.uri.startswith("data:") for img in (result.images or [])):
            result.convert_images(ImageFormat.DATAURI)

        assert len(result.materials) >= 2, f"Expected >=2 materials, got {len(result.materials)}"

        names = [m.name for m in result.materials]
        assert "red_metal" in names, f"'red_metal' not in {names}"
        assert "green_plastic" in names, f"'green_plastic' not in {names}"

        red = next(m for m in result.materials if m.name == "red_metal")
        green = next(m for m in result.materials if m.name == "green_plastic")

        # --- Red metal: values ---
        # values.color is sRGB-stored; baseColorFactor is linear per glTF spec.
        # Conversion happens at the gltf.py write boundary.
        from threejs_materials.utils import _srgb_to_linear
        expected_linear = [_srgb_to_linear(c) for c in [0.8, 0.1, 0.1]]
        assert red.pbrMetallicRoughness.baseColorFactor[:3] == pytest.approx(expected_linear, abs=0.01)
        assert red.pbrMetallicRoughness.metallicFactor == pytest.approx(1.0)
        assert red.pbrMetallicRoughness.roughnessFactor == pytest.approx(1.0)

        # --- Red metal: textures ---
        assert red.pbrMetallicRoughness.metallicRoughnessTexture is not None, \
            "red_metal should have a metallicRoughnessTexture (packed from roughness)"
        assert red.normalTexture is not None, "red_metal should have a normalTexture"

        # --- Green plastic: values ---
        assert green.pbrMetallicRoughness.metallicFactor == pytest.approx(0.0)
        assert green.pbrMetallicRoughness.roughnessFactor == pytest.approx(1.0)
        assert "KHR_materials_ior" in (green.extensions or {})
        assert green.extensions["KHR_materials_ior"]["ior"] == pytest.approx(1.45)

        # --- Green plastic: textures ---
        assert green.pbrMetallicRoughness.baseColorTexture is not None, \
            "green_plastic should have a baseColorTexture"
        assert green.pbrMetallicRoughness.metallicRoughnessTexture is not None, \
            "green_plastic should have a metallicRoughnessTexture (packed from roughness)"
        assert green.normalTexture is not None, "green_plastic should have a normalTexture"

        # --- Image count ---
        assert len(result.images) >= 3, \
            f"Expected >=3 images (color, MR, normal), got {len(result.images)}"

        # --- Verify texture image data integrity via hash ---
        def _img_hash(tex_info):
            """Get sha256 of the image bytes referenced by a TextureInfo."""
            if tex_info is None:
                return None
            tex_idx = tex_info.index
            src = result.textures[tex_idx].source
            img = result.images[src]
            if img.uri and img.uri.startswith("data:"):
                raw = base64.b64decode(img.uri.split(",", 1)[1])
            elif img.bufferView is not None:
                bv = result.bufferViews[img.bufferView]
                blob = result.binary_blob()
                offset = bv.byteOffset or 0
                raw = blob[offset:offset + bv.byteLength]
            else:
                return None
            return hashlib.sha256(raw).hexdigest()

        # Normal texture: used by both materials, should match original
        red_normal_hash = _img_hash(red.normalTexture)
        green_normal_hash = _img_hash(green.normalTexture)
        assert red_normal_hash == expected_hashes["normal"], \
            "red_metal normal texture hash mismatch"
        assert green_normal_hash == expected_hashes["normal"], \
            "green_plastic normal texture hash mismatch"

        # Color texture on green: should match original
        green_color_hash = _img_hash(green.pbrMetallicRoughness.baseColorTexture)
        assert green_color_hash == expected_hashes["color"], \
            "green_plastic color texture hash mismatch"

        # MR texture: packed from roughness + metalness scalar. Can't compare
        # against source bytes (packing creates new PNG), but verify the image
        # data is present and non-empty.
        red_mr_hash = _img_hash(red.pbrMetallicRoughness.metallicRoughnessTexture)
        green_mr_hash = _img_hash(green.pbrMetallicRoughness.metallicRoughnessTexture)
        assert red_mr_hash is not None, "red_metal MR texture data missing after round-trip"
        assert green_mr_hash is not None, "green_plastic MR texture data missing after round-trip"

        # --- Meshes should point to different materials ---
        mat_indices = {result.meshes[i].primitives[0].material for i in range(2)}
        assert len(mat_indices) == 2, f"Both meshes point to same material: {mat_indices}"

    def test_gltf_to_gltf(self, tmp_path):
        """Load .gltf+.bin → inject → save .gltf+.bin → reload and verify."""
        gltf, blob = self._make_gltf_object()
        path = self._save_as_gltf(gltf, blob, tmp_path)

        mat_red, mat_green, expected_hashes = self._make_materials()
        inject_materials(path, {1: mat_red, 2: mat_green})

        self._verify_result(path, expected_hashes)

    def test_gltf_to_glb(self, tmp_path):
        """Load .gltf+.bin → inject → save as .glb → reload and verify."""
        gltf, blob = self._make_gltf_object()
        gltf_path = self._save_as_gltf(gltf, blob, tmp_path)

        mat_red, mat_green, expected_hashes = self._make_materials()

        # Inject saves back to gltf_path (.gltf), then we convert to .glb
        inject_materials(gltf_path, {1: mat_red, 2: mat_green})

        # Load the injected .gltf and re-save as .glb
        from pygltflib import GLTF2
        injected = GLTF2.load(gltf_path)
        # Load the .bin into memory for GLB
        bin_path = tmp_path / "test.bin"
        if bin_path.exists():
            injected.set_binary_blob(bin_path.read_bytes())
            injected.buffers[0].uri = None
        glb_path = str(tmp_path / "output.glb")
        injected.save_binary(glb_path)

        self._verify_result(glb_path, expected_hashes)

    def test_glb_to_glb(self, tmp_path):
        """Load .glb → inject → save .glb → reload and verify."""
        gltf, blob = self._make_gltf_object()
        path = self._save_as_glb(gltf, blob, tmp_path)

        mat_red, mat_green, expected_hashes = self._make_materials()
        inject_materials(path, {1: mat_red, 2: mat_green})

        self._verify_result(path, expected_hashes)

    def test_glb_to_gltf(self, tmp_path):
        """Load .glb → inject → save as .gltf+.bin → reload and verify."""
        gltf, blob = self._make_gltf_object()
        glb_path = self._save_as_glb(gltf, blob, tmp_path)

        mat_red, mat_green, expected_hashes = self._make_materials()

        # Inject saves back as .glb, then we convert to .gltf
        inject_materials(glb_path, {1: mat_red, 2: mat_green})

        # Load the injected .glb and re-save as .gltf
        from pygltflib import GLTF2, ImageFormat
        injected = GLTF2.load(glb_path)
        gltf_path = str(tmp_path / "output.gltf")
        # Extract images to files for .gltf format
        if injected.images:
            tex_dir = tmp_path / "output"
            tex_dir.mkdir(exist_ok=True)
            injected.convert_images(ImageFormat.FILE, path=str(tex_dir), override=True)
            for img in injected.images:
                if img.uri and not img.uri.startswith("data:"):
                    img.uri = "output/" + img.uri
        injected.save(gltf_path)

        self._verify_result(gltf_path, expected_hashes)


# ---------------------------------------------------------------------------
# Fix: always bake procedural MaterialX materials (uncommitted)
#
# Materials without textures but with procedural node graphs (e.g. "Brass")
# had their colors lost because baking was skipped.
# This test uses a minimal MaterialX document with a constant color node.
# ---------------------------------------------------------------------------


def _materialx_available():
    return importlib.util.find_spec("MaterialX") is not None


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
        # values.color is sRGB-stored. Linear input (0.95, 0.79, 0.37) → sRGB
        # ~(0.978, 0.901, 0.642). Verify the brass shape: red & green high,
        # blue meaningfully lower (gap ≥ 0.2 in sRGB).
        assert color[0] > 0.9, f"expected brass red ~0.98, got {color[0]}"
        assert color[0] - color[2] > 0.2, (
            f"expected brass red >> blue, got red={color[0]} blue={color[2]}"
        )


# ---------------------------------------------------------------------------
# Fix: Silent failures in conversion pipeline
#
# Multiple code paths silently swallowed errors, producing empty materials
# that then got cached permanently. Now all failure points log warnings
# and empty results are never cached.
# ---------------------------------------------------------------------------


class TestSilentFailures:
    def test_extract_materials_warns_on_no_shader_nodes(self, caplog):
        """extract_materials must log a warning when a material has no shader nodes."""
        if not _materialx_available():
            pytest.skip("MaterialX not installed")
        import logging
        import tempfile
        from pathlib import Path
        from threejs_materials.convert import extract_materials, load_document_with_stdlib

        # A material with no surfaceshader connection
        mtlx = """\
<?xml version="1.0" encoding="utf-8"?>
<materialx version="1.38">
  <surfacematerial name="Empty" type="material">
  </surfacematerial>
</materialx>
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty.mtlx"
            p.write_text(mtlx)
            doc, _ = load_document_with_stdlib(p)
            with caplog.at_level(logging.WARNING, logger="threejs_materials.convert"):
                result = extract_materials(doc)

        assert result == []
        assert "no shader nodes" in caplog.text.lower()

    def test_to_threejs_physical_warns_on_empty_output(self, caplog):
        """to_threejs_physical must warn when no PBR properties are produced."""
        import logging
        from pathlib import Path
        from threejs_materials.convert import to_threejs_physical

        mat = {
            "name": "Empty",
            "shader_model": "unsupported_model_xyz",
            "params": {},
            "textures": {},
        }
        with caplog.at_level(logging.WARNING, logger="threejs_materials.convert"):
            props = to_threejs_physical(mat, Path("/tmp"))

        assert not props  # empty or only displacement
        assert "no pbr properties" in caplog.text.lower()

    def test_empty_material_not_cached(self, tmp_path, monkeypatch):
        """_SourceLoader.load must not write empty materials to cache."""

        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", tmp_path)

        # Simulate: _process_mtlx returns empty properties
        def fake_process_mtlx(path, resolution="1K"):
            return {}, None, path.parent

        monkeypatch.setattr("threejs_materials.sources._process_mtlx", fake_process_mtlx)

        # Simulate: source fetch returns a result with mtlx_path
        from threejs_materials.sources.common import SourceResult
        from threejs_materials.sources import _SourceLoader

        class FakeModule:
            BROWSE_URL = "https://example.com"
            @staticmethod
            def fetch(name, res, out_dir):
                mtlx = out_dir / "test.mtlx"
                mtlx.write_text("")
                return SourceResult(mtlx_path=mtlx, license="test", url="")

        loader = _SourceLoader("gpuopen")
        monkeypatch.setattr(
            "threejs_materials.sources._SOURCE_MODULES",
            {"gpuopen": FakeModule},
        )

        result = loader.load("EmptyMat")

        # Result should have empty values
        assert result["values"] == {}
        # No cache file should exist
        cache_files = list(tmp_path.glob("*.json"))
        assert len(cache_files) == 0, f"Empty material was cached: {cache_files}"

    def test_process_mtlx_warns_on_empty_properties(self, caplog):
        """_process_mtlx must warn when conversion produces empty properties."""
        if not _materialx_available():
            pytest.skip("MaterialX not installed")
        import logging
        import tempfile
        from pathlib import Path
        from threejs_materials.convert import _process_mtlx

        # A material with unsupported shader model
        mtlx = """\
<?xml version="1.0" encoding="utf-8"?>
<materialx version="1.38">
  <surface name="SR_weird" type="surfaceshader">
  </surface>
  <surfacematerial name="Weird" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="SR_weird" />
  </surfacematerial>
</materialx>
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "weird.mtlx"
            p.write_text(mtlx)
            with caplog.at_level(logging.WARNING, logger="threejs_materials.convert"):
                properties, _, _ = _process_mtlx(p)

        # Should have warned about empty/unsupported
        assert any(
            "no pbr properties" in r.message.lower() or
            "unsupported" in r.message.lower()
            for r in caplog.records
        )
