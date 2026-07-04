"""Tests for threejs_materials.convert — offline, no GPU."""

import logging

import MaterialX as mx
import pytest

from threejs_materials.convert import (
    _evaluate_constant_scalar_graph,
    _recover_baker_clobbered_iors,
    _resolution_to_pixels,
    encode_texture_base64,
    extract_materials,
    parse_value,
    to_threejs_physical,
)

from conftest import (
    GLTF_PBR_PARAMS,
    OPEN_PBR_SURFACE_PARAMS,
    STANDARD_SURFACE_PARAMS,
    make_mtlx_string,
)


# ---------------------------------------------------------------------------
# parse_value
# ---------------------------------------------------------------------------


class TestResolutionToPixels:
    """Resolution threading through _process_mtlx / bake_materials."""

    def test_known_resolutions(self):
        assert _resolution_to_pixels("1K") == 1024
        assert _resolution_to_pixels("2K") == 2048
        assert _resolution_to_pixels("4K") == 4096
        assert _resolution_to_pixels("8K") == 8192

    def test_case_insensitive(self):
        assert _resolution_to_pixels("4k") == 4096
        assert _resolution_to_pixels("4K") == 4096

    def test_unknown_falls_back_to_1k(self):
        """Unknown strings return 1024 — preserves historical default
        rather than raising (the baker is downstream of user input)."""
        assert _resolution_to_pixels("42K") == 1024
        assert _resolution_to_pixels("") == 1024
        assert _resolution_to_pixels("garbage") == 1024


class TestParseValue:
    def test_float(self):
        assert parse_value("0.5", "float") == 0.5

    def test_color3(self):
        assert parse_value("0.1, 0.2, 0.3", "color3") == [0.1, 0.2, 0.3]

    def test_vector3(self):
        assert parse_value("1.0, 2.0, 3.0", "vector3") == [1.0, 2.0, 3.0]

    def test_color4(self):
        assert parse_value("0.1, 0.2, 0.3, 0.4", "color4") == [0.1, 0.2, 0.3, 0.4]

    def test_vector4(self):
        assert parse_value("1.0, 2.0, 3.0, 4.0", "vector4") == [1.0, 2.0, 3.0, 4.0]

    def test_vector2(self):
        assert parse_value("1.0, 2.0", "vector2") == [1.0, 2.0]

    def test_matrix33(self):
        vals = ", ".join(str(float(i)) for i in range(9))
        result = parse_value(vals, "matrix33")
        assert len(result) == 9
        assert result[0] == 0.0

    def test_matrix44(self):
        vals = ", ".join(str(float(i)) for i in range(16))
        result = parse_value(vals, "matrix44")
        assert len(result) == 16

    def test_integer(self):
        assert parse_value("42", "integer") == 42

    def test_boolean_true(self):
        assert parse_value("true", "boolean") is True

    def test_boolean_false(self):
        assert parse_value("false", "boolean") is False

    def test_boolean_one(self):
        assert parse_value("1", "boolean") is True

    def test_string(self):
        assert parse_value("hello", "string") == "hello"

    def test_empty(self):
        assert parse_value("", "float") is None

    def test_unknown_type_returns_string(self):
        assert parse_value("foo", "some_custom_type") == "foo"


# ---------------------------------------------------------------------------
# extract_materials — parametric only (no baking)
# ---------------------------------------------------------------------------


def _load_from_string(xml_string):
    """Load a MaterialX document from an XML string."""
    doc = mx.createDocument()
    stdlib = mx.createDocument()
    search_path = mx.getDefaultDataSearchPath()
    mx.loadLibraries(list(mx.getDefaultDataLibraryFolders()), search_path, stdlib)
    mx.readFromXmlString(doc, xml_string)
    doc.setDataLibrary(stdlib)
    return doc


class TestExtractMaterials:
    def test_single_material(self):
        xml = make_mtlx_string(
            "Copper",
            "standard_surface",
            STANDARD_SURFACE_PARAMS,
        )
        doc = _load_from_string(xml)
        mats = extract_materials(doc)
        assert len(mats) == 1
        assert mats[0]["name"] == "Copper"
        assert mats[0]["shader_model"] == "standard_surface"
        assert "base" in mats[0]["params"]
        assert mats[0]["params"]["base"] == 0.8

    def test_multiple_materials(self):
        xml = make_mtlx_string(
            "Mat1",
            "standard_surface",
            {"base": ("float", "1.0")},
            extra_materials=[
                {"name": "Mat2", "params": {"base": ("float", "0.5")}},
            ],
        )
        doc = _load_from_string(xml)
        mats = extract_materials(doc)
        assert len(mats) == 2
        assert mats[0]["name"] == "Mat1"
        assert mats[1]["name"] == "Mat2"

    def test_empty_document(self):
        xml = '<?xml version="1.0"?><materialx version="1.38"></materialx>'
        doc = _load_from_string(xml)
        mats = extract_materials(doc)
        assert mats == []

    def test_gltf_pbr_model(self):
        xml = make_mtlx_string("Steel", "gltf_pbr", GLTF_PBR_PARAMS)
        doc = _load_from_string(xml)
        mats = extract_materials(doc)
        assert len(mats) == 1
        assert mats[0]["shader_model"] == "gltf_pbr"
        assert mats[0]["params"]["roughness"] == 0.5

    def test_open_pbr_model(self):
        xml = make_mtlx_string("Clay", "open_pbr_surface", OPEN_PBR_SURFACE_PARAMS)
        doc = _load_from_string(xml)
        mats = extract_materials(doc)
        assert len(mats) == 1
        assert mats[0]["shader_model"] == "open_pbr_surface"


# ---------------------------------------------------------------------------
# to_threejs_physical — one test per shader model
# ---------------------------------------------------------------------------


class TestToThreejsPhysical:
    """Test three.js property mapping for each shader model."""

    def test_standard_surface_basic(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 0.8,
                "base_color": [0.5, 0.3, 0.1],
                "metalness": 0.0,
                "specular_roughness": 0.4,
                "specular": 1.0,
                "specular_color": [1.0, 1.0, 1.0],
                "specular_IOR": 1.5,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        # color = sRGB-encoded(base * base_color); MaterialX is linear,
        # values.color is sRGB-stored (matches three-cad-viewer's
        # setRGB(SRGBColorSpace) consumption).
        from threejs_materials.utils import _linear_to_srgb
        expected = [_linear_to_srgb(c) for c in [0.4, 0.24, 0.08]]
        assert props["color"]["value"] == pytest.approx(expected)
        assert props["metalness"]["value"] == 0.0
        assert props["roughness"]["value"] == 0.4
        assert props["specularIntensity"]["value"] == 1.0
        assert props["ior"]["value"] == 1.5

    def test_standard_surface_scalar_texture_neutralization(self, tmp_path, tiny_png):
        """When texture exists, color should carry the base weight."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        tex_file = tex_dir / "base_color.png"
        tex_file.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {"base": 0.5, "base_color": [0.5, 0.5, 0.5]},
            "textures": {"base_color": {"file": "textures/base_color.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        # With texture, color carries the base weight as a multiplier (sRGB-encoded).
        from threejs_materials.utils import _linear_to_srgb
        expected_v = _linear_to_srgb(0.5)
        assert props["color"]["value"] == pytest.approx([expected_v] * 3)
        assert "texture" in props["color"]

    def test_standard_surface_emission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "emission": 2.0,
                "emission_color": [1.0, 0.5, 0.0],
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["emissive"]["value"] == pytest.approx([2.0, 1.0, 0.0])
        assert props["emissiveIntensity"]["value"] == 2.0

    def test_standard_surface_opacity(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "opacity": 0.5,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["opacity"]["value"] == 0.5
        assert props["transparent"]["value"] is True
        # Scalar-only opacity: no alphaTest (nothing to test against).
        assert "alphaTest" not in props

    def test_standard_surface_opacity_texture_promotes_alpha_test(
        self, tmp_path, tiny_png,
    ):
        """ambientCG Smear regression: opacity texture alone must set
        alphaTest=0.5 (MASK mode). transparent=True is deliberately NOT set —
        it would disable depth writes and cause back-face bleed-through on
        closed shapes."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        (tex_dir / "opacity.png").write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Smear001",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                # No scalar opacity — the MaterialX default is 1.0.
            },
            "textures": {"opacity": {"file": "textures/opacity.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "opacity" in props and "texture" in props["opacity"]
        assert props["alphaTest"]["value"] == 0.5
        assert "transparent" not in props

    def test_standard_surface_no_opacity_signal(self, tmp_path):
        """Plain opaque material must not emit transparent/alphaTest."""
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1.0, 1.0, 1.0]},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "transparent" not in props
        assert "alphaTest" not in props
        assert "opacity" not in props

    def test_standard_surface_transmission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "transmission": 0.8,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transmission"]["value"] == 0.8
        # transparent should NOT be set — Three.js handles transmissive
        # objects in a dedicated render pass.
        assert "transparent" not in props
        # opacity should NOT be set when transmission is active
        assert "opacity" not in props

    def test_standard_surface_clearcoat(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "coat": 0.5,
                "coat_roughness": 0.2,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["clearcoat"]["value"] == 0.5
        assert props["clearcoatRoughness"]["value"] == 0.2

    def test_standard_surface_sheen(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "sheen": 0.5,
                "sheen_color": [0.8, 0.8, 0.8],
                "sheen_roughness": 0.4,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 0.5
        assert props["sheenColor"]["value"] == [0.8, 0.8, 0.8]
        assert props["sheenRoughness"]["value"] == 0.4

    def test_standard_surface_iridescence(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "thin_film_thickness": 500.0,
                "thin_film_IOR": 1.3,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["iridescence"]["value"] == 1.0
        assert props["iridescenceIOR"]["value"] == 1.3
        # standard_surface thin_film_thickness is already in nm; MaterialX
        # exposes a single scalar, so it's emitted as uniform thickness.
        assert props["iridescenceThicknessRange"]["value"] == [500.0, 500.0]

    def test_gltf_pbr_basic(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {
                "base_color": [0.8, 0.2, 0.1],
                "metallic": 0.0,
                "roughness": 0.5,
                "ior": 1.5,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        # MaterialX gltf_pbr base_color is linear; values.color is sRGB-stored.
        from threejs_materials.utils import _linear_to_srgb
        assert props["color"]["value"] == pytest.approx(
            [_linear_to_srgb(c) for c in [0.8, 0.2, 0.1]]
        )
        assert props["metalness"]["value"] == 0.0
        assert props["roughness"]["value"] == 0.5

    def test_gltf_pbr_packed_texture(self, tmp_path, tiny_png):
        """Metallic-roughness packed texture gets special key."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        mr_tex = tex_dir / "mr.png"
        mr_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"metallic": 0.5, "roughness": 0.5},
            "textures": {"metallic_roughness": {"file": "textures/mr.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "metallicRoughness" in props
        assert "texture" in props["metallicRoughness"]
        # With packed texture, scalars should be neutral (1.0)
        assert props["metalness"]["value"] == 1.0
        assert props["roughness"]["value"] == 1.0
        # Channel mapping metadata
        assert props["metallicRoughness"]["channelMapping"] == {
            "roughness": "g",
            "metalness": "b",
        }

    def test_gltf_pbr_emission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {
                "emissive": [1.0, 0.5, 0.0],
                "emissive_strength": 2.0,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["emissive"]["value"] == [1.0, 0.5, 0.0]
        assert props["emissiveIntensity"]["value"] == 2.0

    def test_gltf_pbr_clearcoat(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"clearcoat": 1.0, "clearcoat_roughness": 0.1},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["clearcoat"]["value"] == 1.0
        assert props["clearcoatRoughness"]["value"] == 0.1

    def test_gltf_pbr_sheen(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {
                "sheen_color": [0.9, 0.9, 0.9],
                "sheen_roughness": 0.3,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheenColor"]["value"] == [0.9, 0.9, 0.9]
        assert props["sheen"]["value"] == 1.0

    def test_gltf_pbr_transmission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {
                "transmission": 0.9,
                "attenuation_color": [0.8, 0.9, 1.0],
                "attenuation_distance": 0.5,
                "thickness": 0.1,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transmission"]["value"] == 0.9
        assert props["attenuationColor"]["value"] == [0.8, 0.9, 1.0]
        assert props["attenuationDistance"]["value"] == 0.5
        assert props["thickness"]["value"] == 0.1

    def test_open_pbr_surface_basic(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [0.6, 0.6, 0.6],
                "base_metalness": 0.0,
                "specular_roughness": 0.3,
                "specular_ior": 1.5,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        # MaterialX values are linear; values.color is sRGB-stored.
        from threejs_materials.utils import _linear_to_srgb
        assert props["color"]["value"] == pytest.approx([_linear_to_srgb(0.6)] * 3)
        assert props["metalness"]["value"] == 0.0
        assert props["roughness"]["value"] == 0.3
        assert props["ior"]["value"] == 1.5

    def test_open_pbr_surface_base_weight_preserved_with_texture(
        self, tmp_path, tiny_png,
    ):
        """open_pbr_surface's `base_weight` must survive as a scalar when
        `base_color` is a texture.

        The MaterialX baker leaves `base_weight` as a literal on the shader
        and does NOT fold it into the baked `base_color` texture (verified
        via TextureBaker source — see materialx_baker.md). Three.js
        reproduces the `base_weight × base_color` shading math at render
        time, so emitting a neutral [1,1,1] would drop the factor and
        brighten the material.

        Regression for the asymmetry-with-standard_surface bug fixed in
        v1.1.1: previously `has_tex("base_color")` forced color=[1,1,1]
        regardless of `base_weight`.
        """
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        (tex_dir / "base_color.png").write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "T",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 0.5,
                "base_metalness": 0.0,
            },
            "textures": {"base_color": {"file": "textures/base_color.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        # base_weight=0.5 linear → sRGB-encoded scalar carries through.
        from threejs_materials.utils import _linear_to_srgb
        assert props["color"]["value"] == pytest.approx([_linear_to_srgb(0.5)] * 3)
        assert "texture" in props["color"]

    def test_open_pbr_surface_base_weight_default_emits_neutral(
        self, tmp_path, tiny_png,
    ):
        """When `base_weight` is its default 1.0 and a texture is present,
        the scalar remains [1,1,1] (neutral) — texture controls fully."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        (tex_dir / "base_color.png").write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "T",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_metalness": 0.0,
            },
            "textures": {"base_color": {"file": "textures/base_color.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        # base_weight=1.0 → neutral; [1,1,1] is invariant under linear↔sRGB
        # (modulo float epsilon).
        assert props["color"]["value"] == pytest.approx([1.0, 1.0, 1.0], abs=1e-9)

    def test_open_pbr_surface_emission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "emission_luminance": 5000.0,
                "emission_color": [1.0, 0.8, 0.0],
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["emissive"]["value"] == [1.0, 0.8, 0.0]
        assert props["emissiveIntensity"]["value"] == pytest.approx(5.0)

    def test_open_pbr_surface_transmission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "transmission_weight": 0.9,
                "transmission_color": [0.9, 0.95, 1.0],
                "transmission_depth": 0.3,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transmission"]["value"] == 0.9
        # transparent should NOT be set — Three.js handles transmissive
        # objects in a dedicated render pass.
        assert "transparent" not in props
        assert props["attenuationColor"]["value"] == [0.9, 0.95, 1.0]
        assert props["attenuationDistance"]["value"] == 0.3

    def test_open_pbr_surface_clearcoat(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "coat_weight": 0.7,
                "coat_roughness": 0.1,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["clearcoat"]["value"] == 0.7
        assert props["clearcoatRoughness"]["value"] == 0.1

    def test_open_pbr_surface_iridescence(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "thin_film_weight": 0.8,
                "thin_film_ior": 1.4,
                "thin_film_thickness": 0.4,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["iridescence"]["value"] == 0.8
        assert props["iridescenceIOR"]["value"] == 1.4
        assert props["iridescenceThicknessRange"]["value"] == [400.0, 400.0]

    def test_open_pbr_surface_dispersion(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "transmission_dispersion_abbe_number": 40.0,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["dispersion"]["value"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# encode_texture_base64
# ---------------------------------------------------------------------------


class TestEncodeTextureBase64:
    def test_png_data_uri(self, tiny_png):
        result = encode_texture_base64(tiny_png)
        assert result.startswith("data:image/png;base64,")
        # Must be valid base64
        import base64

        payload = result.split(",", 1)[1]
        decoded = base64.b64decode(payload)
        assert decoded[:4] == b"\x89PNG"

    def test_jpeg_mime(self, tmp_path):
        # Create a tiny file with .jpg extension (content doesn't matter for mime)
        jpg = tmp_path / "test.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0dummy")
        result = encode_texture_base64(jpg)
        assert result.startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# Multi-material warning
# ---------------------------------------------------------------------------


class TestAnisotropy:
    def test_standard_surface_anisotropy_not_mapped(self, tmp_path):
        """standard_surface anisotropy is intentionally not mapped — the models
        are structurally incompatible (see comment in convert.py)."""
        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "specular_anisotropy": 0.7,
                "specular_rotation": 0.25,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "anisotropy" not in props
        assert "anisotropyRotation" not in props

    def test_gltf_pbr_anisotropy(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {
                "anisotropy_strength": 0.5,
                "anisotropy_rotation": 1.2,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["anisotropy"]["value"] == 0.5
        assert props["anisotropyRotation"]["value"] == 1.2

    def test_open_pbr_surface_anisotropy_not_mapped(self, tmp_path):
        """open_pbr_surface anisotropy is intentionally not mapped — same
        structural mismatch as standard_surface (see comment in convert.py)."""
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "specular_roughness_anisotropy": 0.6,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "anisotropy" not in props


class TestOcclusion:
    def test_gltf_pbr_occlusion_texture(self, tmp_path, tiny_png):
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        ao_tex = tex_dir / "ao.png"
        ao_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {},
            "textures": {"occlusion": {"file": "textures/ao.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "ao" in props
        assert "texture" in props["ao"]


class TestClearcoatNormal:
    def test_standard_surface_clearcoat_normal(self, tmp_path, tiny_png):
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        cn_tex = tex_dir / "coat_normal.png"
        cn_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "coat": 1.0,
            },
            "textures": {"coat_normal": {"file": "textures/coat_normal.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "clearcoatNormal" in props
        assert "texture" in props["clearcoatNormal"]

    def test_gltf_pbr_clearcoat_normal(self, tmp_path, tiny_png):
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        cn_tex = tex_dir / "clearcoat_normal.png"
        cn_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"clearcoat": 1.0},
            "textures": {"clearcoat_normal": {"file": "textures/clearcoat_normal.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "clearcoatNormal" in props
        assert "texture" in props["clearcoatNormal"]

    def test_open_pbr_surface_clearcoat_normal(self, tmp_path, tiny_png):
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        cn_tex = tex_dir / "coat_normal.png"
        cn_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "coat_weight": 1.0,
            },
            "textures": {"geometry_coat_normal": {"file": "textures/coat_normal.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "clearcoatNormal" in props
        assert "texture" in props["clearcoatNormal"]


class TestDisplacement:
    def test_displacement_extraction(self):
        """extract_materials() picks up displacement from material node."""
        xml = make_mtlx_string(
            "DispMat",
            "standard_surface",
            {"base": ("float", "1.0"), "base_color": ("color3", "1, 1, 1")},
            displacement={"scale": 0.05, "texture_file": "disp.png"},
        )
        doc = _load_from_string(xml)
        mats = extract_materials(doc)
        assert len(mats) == 1
        assert "displacement" in mats[0]["textures"]
        assert mats[0]["textures"]["displacement"]["file"] == "disp.png"
        assert mats[0]["params"]["displacement_scale"] == pytest.approx(0.05)

    def test_displacement_mapping(self, tmp_path, tiny_png):
        """to_threejs_physical() maps displacement texture + scale."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        disp_tex = tex_dir / "disp.png"
        disp_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "standard_surface",
            "params": {
                "base": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "displacement_scale": 0.05,
            },
            "textures": {"displacement": {"file": "textures/disp.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "displacement" in props
        assert "texture" in props["displacement"]
        assert props["displacementScale"]["value"] == pytest.approx(0.05)

    def test_warning_logged(self, tmp_path, caplog):
        """_process_mtlx should log a warning for multi-material documents."""
        xml = make_mtlx_string(
            "Mat1",
            "standard_surface",
            {"base": ("float", "1.0"), "base_color": ("color3", "1, 1, 1")},
            extra_materials=[
                {
                    "name": "Mat2",
                    "params": {
                        "base": ("float", "0.5"),
                        "base_color": ("color3", "0.5, 0.5, 0.5"),
                    },
                },
            ],
        )
        mtlx_file = tmp_path / "multi.mtlx"
        mtlx_file.write_text(xml)

        from threejs_materials.convert import _process_mtlx

        with caplog.at_level(logging.WARNING, logger="threejs_materials.convert"):
            props, model, _tex_dir = _process_mtlx(mtlx_file)

        assert any("contains 2 materials" in r.message for r in caplog.records)
        assert props  # should still return first material's properties


# ---------------------------------------------------------------------------
# Unknown shader model warning
# ---------------------------------------------------------------------------


class TestUnknownShaderModel:
    def test_unknown_model_warns(self, tmp_path, caplog):
        mat = {
            "name": "Test",
            "shader_model": "some_unknown_model",
            "params": {},
            "textures": {},
        }
        with caplog.at_level(logging.WARNING, logger="threejs_materials.convert"):
            to_threejs_physical(mat, tmp_path)
        assert any("Unsupported shader model" in r.message for r in caplog.records)

    def test_unknown_model_still_maps_displacement(self, tmp_path, tiny_png):
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        disp_tex = tex_dir / "disp.png"
        disp_tex.write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "some_unknown_model",
            "params": {"displacement_scale": 0.1},
            "textures": {"displacement": {"file": "textures/disp.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "displacement" in props
        assert props["displacementScale"]["value"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# gltf_pbr: alpha, iridescence, dispersion
# ---------------------------------------------------------------------------


class TestGltfPbrAlpha:
    def test_alpha_blend_mode(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"alpha": 0.5, "alpha_mode": 2},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["opacity"]["value"] == 0.5
        assert props["transparent"]["value"] is True

    def test_alpha_blend_mode_texture_only(self, tmp_path, tiny_png):
        """BLEND + alpha texture with scalar=1.0: transparent must still be
        set, otherwise Three.js ignores the alpha texture entirely."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        (tex_dir / "alpha.png").write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"alpha_mode": 2},  # BLEND, scalar defaults to 1.0
            "textures": {"alpha": {"file": "textures/alpha.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transparent"]["value"] is True
        assert "opacity" in props and "texture" in props["opacity"]
        # BLEND is an explicit author choice — don't force MASK.
        assert "alphaTest" not in props

    def test_alpha_mask_mode(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"alpha_mode": 1, "alpha_cutoff": 0.3},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["alphaTest"]["value"] == 0.3
        assert "opacity" not in props

    def test_alpha_opaque_mode(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"alpha": 0.5, "alpha_mode": 0},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "opacity" not in props
        assert "transparent" not in props


class TestGltfPbrIridescence:
    def test_iridescence(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {
                "iridescence": 0.8,
                "iridescence_ior": 1.4,
                "iridescence_thickness": 300.0,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["iridescence"]["value"] == 0.8
        assert props["iridescenceIOR"]["value"] == 1.4
        assert props["iridescenceThicknessRange"]["value"] == [300.0, 300.0]


class TestGltfPbrDispersion:
    def test_dispersion(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"dispersion": 0.3},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["dispersion"]["value"] == 0.3


# ---------------------------------------------------------------------------
# gltf_pbr: separate metallic/roughness textures
# ---------------------------------------------------------------------------


class TestGltfPbrNormalScale:
    def test_normal_scale(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"normal_scale": 0.5},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["normalScale"]["value"] == [0.5, 0.5]

    def test_normal_scale_default_omitted(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {},
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "normalScale" not in props


class TestGltfPbrSeparateTextures:
    def test_separate_metallic_roughness(self, tmp_path, tiny_png):
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        (tex_dir / "metallic.png").write_bytes(tiny_png.read_bytes())
        (tex_dir / "roughness.png").write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Test",
            "shader_model": "gltf_pbr",
            "params": {"metallic": 0.5, "roughness": 0.5},
            "textures": {
                "metallic": {"file": "textures/metallic.png"},
                "roughness": {"file": "textures/roughness.png"},
            },
        }
        props = to_threejs_physical(mat, tmp_path)
        # Scalars should be neutral since textures exist
        assert props["metalness"]["value"] == 1.0
        assert props["roughness"]["value"] == 1.0
        assert "texture" in props["metalness"]
        assert "texture" in props["roughness"]
        # No packed texture key
        assert "metallicRoughness" not in props


# ---------------------------------------------------------------------------
# open_pbr_surface: sheen (fuzz)
# ---------------------------------------------------------------------------


class TestOpenPbrGeometryOpacity:
    def test_geometry_opacity(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "geometry_opacity": [0.5, 0.5, 0.5],
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["opacity"]["value"] == pytest.approx(0.5)
        assert props["transparent"]["value"] is True

    def test_geometry_opacity_not_set_with_transmission(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "transmission_weight": 0.9,
                "geometry_opacity": [0.5, 0.5, 0.5],
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "opacity" not in props

    def test_geometry_opacity_default_omitted(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "opacity" not in props
        assert "transparent" not in props

    def test_geometry_opacity_texture_promotes_alpha_test(
        self, tmp_path, tiny_png,
    ):
        """ambientCG Smear005 regression: open_pbr_surface with a
        geometry_opacity texture must emit opacity map + alphaTest=0.5.
        transparent=True is deliberately NOT set (would disable depth writes
        and cause back-face bleed-through on closed shapes)."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir()
        (tex_dir / "opacity.png").write_bytes(tiny_png.read_bytes())

        mat = {
            "name": "Smear005",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
            },
            "textures": {"geometry_opacity": {"file": "textures/opacity.png"}},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "opacity" in props and "texture" in props["opacity"]
        assert props["alphaTest"]["value"] == 0.5
        assert "transparent" not in props


class TestOpenPbrThinWalled:
    def test_thin_walled_double_side(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "geometry_thin_walled": True,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["side"]["value"] == 2  # THREE.DoubleSide

    def test_not_thin_walled_no_side(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "side" not in props


class TestOpenPbrSheen:
    def test_fuzz_mapping(self, tmp_path):
        mat = {
            "name": "Test",
            "shader_model": "open_pbr_surface",
            "params": {
                "base_weight": 1.0,
                "base_color": [1.0, 1.0, 1.0],
                "fuzz_weight": 0.6,
                "fuzz_color": [0.9, 0.8, 0.7],
                "fuzz_roughness": 0.4,
            },
            "textures": {},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 0.6
        assert props["sheenColor"]["value"] == [0.9, 0.8, 0.7]
        assert props["sheenRoughness"]["value"] == 0.4


# ---------------------------------------------------------------------------
# Procedural feature inputs — baker wrote a texture, scalar param is absent.
# Regression tests for the previous silent-drop behavior where a gated
# feature with no constant scalar lost both the scalar AND the texture.
# ---------------------------------------------------------------------------


def _make_tex(tmp_path, tiny_png, name):
    """Helper: place a tiny PNG in textures/ and return the mtlx-relative dict."""
    tex_dir = tmp_path / "textures"
    tex_dir.mkdir(exist_ok=True)
    (tex_dir / f"{name}.png").write_bytes(tiny_png.read_bytes())
    return {"file": f"textures/{name}.png"}


class TestStandardSurfaceProceduralFeatures:
    """standard_surface: procedural feature inputs must survive conversion."""

    def test_procedural_transmission(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1, 1, 1]},
            "textures": {"transmission": _make_tex(tmp_path, tiny_png, "trans")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transmission"]["value"] == 1.0
        assert "texture" in props["transmission"]

    def test_procedural_clearcoat(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1, 1, 1]},
            "textures": {"coat": _make_tex(tmp_path, tiny_png, "coat")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["clearcoat"]["value"] == 1.0
        assert "texture" in props["clearcoat"]

    def test_procedural_sheen_color(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1, 1, 1]},
            "textures": {"sheen_color": _make_tex(tmp_path, tiny_png, "sheen")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 1.0
        assert "texture" in props["sheenColor"]

    def test_literal_sheen_survives_procedural_sheen_color(
        self, tmp_path, tiny_png,
    ):
        """Literal `sheen = 0.5` + procedural `sheen_color` must preserve
        the 0.5. Previously the has_tex("sheen_color") check clobbered
        the literal scalar to 1.0, rendering the sheen layer 2× too strong.
        """
        mat = {
            "name": "T", "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1, 1, 1], "sheen": 0.5},
            "textures": {"sheen_color": _make_tex(tmp_path, tiny_png, "sheen")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 0.5      # ← was 1.0 before fix
        assert "texture" in props["sheenColor"]

    def test_procedural_emission_color_without_weight_stays_off(
        self, tmp_path, tiny_png,
    ):
        """Per MaterialX spec, standard_surface `emission` default is 0.0 →
        no emission. A procedural `emission_color` alone must not force
        emission on; the author's explicit scalar is required."""
        mat = {
            "name": "T", "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1, 1, 1]},
            "textures": {"emission_color": _make_tex(tmp_path, tiny_png, "em")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "emissive" not in props
        assert "emissiveIntensity" not in props

    def test_procedural_thin_film_weight_without_thickness_stays_off(
        self, tmp_path, tiny_png,
    ):
        """Per MaterialX spec, standard_surface `thin_film_thickness`
        default is 0.0 → no thin-film interference. Procedural
        `thin_film_weight` alone must not invent a thickness."""
        mat = {
            "name": "T", "shader_model": "standard_surface",
            "params": {"base": 1.0, "base_color": [1, 1, 1]},
            "textures": {"thin_film_weight": _make_tex(tmp_path, tiny_png, "tf")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "iridescence" not in props
        assert "iridescenceThicknessRange" not in props


class TestGltfPbrProceduralFeatures:
    """gltf_pbr: procedural feature inputs must survive conversion."""

    def test_procedural_transmission(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1]},
            "textures": {"transmission": _make_tex(tmp_path, tiny_png, "trans")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transmission"]["value"] == 1.0
        assert "texture" in props["transmission"]

    def test_procedural_thickness(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1], "transmission": 1.0},
            "textures": {"thickness": _make_tex(tmp_path, tiny_png, "th")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["thickness"]["value"] == 1.0
        assert "texture" in props["thickness"]

    def test_procedural_clearcoat(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1]},
            "textures": {"clearcoat": _make_tex(tmp_path, tiny_png, "cc")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["clearcoat"]["value"] == 1.0
        assert "texture" in props["clearcoat"]

    def test_procedural_sheen_color(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1]},
            "textures": {"sheen_color": _make_tex(tmp_path, tiny_png, "sh")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 1.0
        assert props["sheenColor"]["value"] == [1.0, 1.0, 1.0]
        assert "texture" in props["sheenColor"]

    def test_procedural_sheen_roughness(self, tmp_path, tiny_png):
        """sheen_roughness wired through the baker must survive conversion
        as a sheenRoughness texture with the scalar promoted to neutral."""
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1]},
            "textures": {
                "sheen_roughness": _make_tex(tmp_path, tiny_png, "sr"),
            },
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 1.0
        assert props["sheenRoughness"]["value"] == 1.0
        assert "texture" in props["sheenRoughness"]

    def test_procedural_iridescence(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1]},
            "textures": {"iridescence": _make_tex(tmp_path, tiny_png, "ir")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["iridescence"]["value"] == 1.0
        assert "texture" in props["iridescence"]

    def test_procedural_emissive_without_factor_stays_off(
        self, tmp_path, tiny_png,
    ):
        """Per glTF spec, emissiveFactor default is [0,0,0] → no emission.
        A procedural `emissive` texture alone must not force emission on."""
        mat = {
            "name": "T", "shader_model": "gltf_pbr",
            "params": {"base_color": [1, 1, 1]},
            "textures": {"emissive": _make_tex(tmp_path, tiny_png, "em")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "emissive" not in props
        assert "emissiveIntensity" not in props


class TestOpenPbrProceduralFeatures:
    """open_pbr_surface: procedural feature inputs must survive conversion."""

    def test_procedural_transmission(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "open_pbr_surface",
            "params": {"base_weight": 1.0, "base_color": [1, 1, 1]},
            "textures": {"transmission_weight": _make_tex(tmp_path, tiny_png, "tr")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["transmission"]["value"] == 1.0
        assert "texture" in props["transmission"]

    def test_procedural_coat(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "open_pbr_surface",
            "params": {"base_weight": 1.0, "base_color": [1, 1, 1]},
            "textures": {"coat_weight": _make_tex(tmp_path, tiny_png, "cw")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["clearcoat"]["value"] == 1.0
        assert "texture" in props["clearcoat"]

    def test_procedural_fuzz(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "open_pbr_surface",
            "params": {"base_weight": 1.0, "base_color": [1, 1, 1]},
            "textures": {"fuzz_color": _make_tex(tmp_path, tiny_png, "fz")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == 1.0
        assert "texture" in props["sheenColor"]

    def test_literal_fuzz_weight_survives_procedural_fuzz_color(
        self, tmp_path, tiny_png,
    ):
        """Literal `fuzz_weight = 0.7` + procedural `fuzz_color` must
        preserve the 0.7. Previously the has_tex("fuzz_color") check
        clobbered the literal scalar to 1.0.
        """
        mat = {
            "name": "T", "shader_model": "open_pbr_surface",
            "params": {"base_weight": 1.0, "base_color": [1, 1, 1], "fuzz_weight": 0.7},
            "textures": {"fuzz_color": _make_tex(tmp_path, tiny_png, "fz")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["sheen"]["value"] == pytest.approx(0.7)
        assert "texture" in props["sheenColor"]

    def test_procedural_emission_color_without_luminance_stays_off(
        self, tmp_path, tiny_png,
    ):
        """Per OpenPBR spec, `emission_luminance` default is 0 → no
        emission. A procedural `emission_color` alone must not force
        emission on."""
        mat = {
            "name": "T", "shader_model": "open_pbr_surface",
            "params": {"base_weight": 1.0, "base_color": [1, 1, 1]},
            "textures": {"emission_color": _make_tex(tmp_path, tiny_png, "em")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert "emissive" not in props
        assert "emissiveIntensity" not in props

    def test_procedural_iridescence(self, tmp_path, tiny_png):
        mat = {
            "name": "T", "shader_model": "open_pbr_surface",
            "params": {"base_weight": 1.0, "base_color": [1, 1, 1]},
            "textures": {"thin_film_weight": _make_tex(tmp_path, tiny_png, "tf")},
        }
        props = to_threejs_physical(mat, tmp_path)
        assert props["iridescence"]["value"] == 1.0
        assert "texture" in props["iridescence"]


# ---------------------------------------------------------------------------
# MaterialX TextureBaker workaround — scalar IOR recovery from constant graphs
# ---------------------------------------------------------------------------


def _build_ior_via_nodegraph_doc(ior_value: float = 1.39) -> mx.Document:
    """Build a standard_surface material with specular_IOR wired through
    a nodegraph containing a constant — the exact pattern GPUOpen uses for
    Old Paint and the Marble family."""
    doc = mx.createDocument()
    ng = doc.addNodeGraph("NG_test")
    const = ng.addNode("constant", "ior_const", "float")
    const.addInput("value", "float").setValueString(str(ior_value))
    dot = ng.addNode("dot", "ior_dot", "float")
    dot.addInput("in", "float").setNodeName("ior_const")
    ng.addOutput("ior_out", "float").setNodeName("ior_dot")

    shader = doc.addNode("standard_surface", "test_shader", "surfaceshader")
    ior_inp = shader.addInput("specular_IOR", "float")
    ior_inp.setAttribute("nodegraph", "NG_test")
    ior_inp.setAttribute("output", "ior_out")

    mat = doc.addNode("surfacematerial", "test_mat", "material")
    mat.addInput("surfaceshader", "surfaceshader").setNodeName("test_shader")
    return doc


class TestBakerIorWorkaround:
    def test_evaluate_constant_direct(self):
        """specular_IOR wired via dot → constant evaluates to the constant."""
        doc = _build_ior_via_nodegraph_doc(1.39)
        shader = next(iter(doc.getNodes("standard_surface")))
        inp = shader.getInput("specular_IOR")
        assert _evaluate_constant_scalar_graph(inp) == pytest.approx(1.39)

    def test_evaluate_non_graph_returns_none(self):
        """A direct-value scalar input (no graph) is out of scope — helper
        returns None because there's no upstream node to walk."""
        doc = mx.createDocument()
        shader = doc.addNode("standard_surface", "s", "surfaceshader")
        inp = shader.addInput("specular_IOR", "float")
        inp.setValueString("1.5")
        assert _evaluate_constant_scalar_graph(inp) is None

    def test_evaluate_non_trivial_graph_returns_none(self):
        """An unhandled node category (e.g., multiply) returns None so the
        baker's value stays in place — conservative by design."""
        doc = mx.createDocument()
        ng = doc.addNodeGraph("NG_mul")
        c1 = ng.addNode("constant", "c1", "float")
        c1.addInput("value", "float").setValueString("1.5")
        c2 = ng.addNode("constant", "c2", "float")
        c2.addInput("value", "float").setValueString("2.0")
        mul = ng.addNode("multiply", "m", "float")
        mul.addInput("in1", "float").setNodeName("c1")
        mul.addInput("in2", "float").setNodeName("c2")
        ng.addOutput("out", "float").setNodeName("m")

        shader = doc.addNode("standard_surface", "s", "surfaceshader")
        inp = shader.addInput("specular_IOR", "float")
        inp.setAttribute("nodegraph", "NG_mul")
        inp.setAttribute("output", "out")
        assert _evaluate_constant_scalar_graph(inp) is None

    def test_recover_patches_params_in_place(self):
        """End-to-end: _recover_baker_clobbered_iors overwrites a clobbered
        IOR in the mats dict with the graph-evaluated value."""
        doc = _build_ior_via_nodegraph_doc(1.39)
        # Simulate the post-bake state: params carry the clobbered 1.0
        mats = [{
            "name": "test_mat",
            "shader_model": "standard_surface",
            "params": {"specular_IOR": 1.0},
            "textures": {},
        }]
        _recover_baker_clobbered_iors(doc, mats)
        assert mats[0]["params"]["specular_IOR"] == pytest.approx(1.39)

    def test_recover_ignores_non_ior_inputs(self):
        """The workaround is scoped to IOR-family inputs only; other
        scalars wired through the same pattern are left alone (we have no
        evidence they need the workaround, and intervening would risk
        over-reach)."""
        doc = mx.createDocument()
        ng = doc.addNodeGraph("NG_spec")
        c = ng.addNode("constant", "c", "float")
        c.addInput("value", "float").setValueString("0.7")
        ng.addOutput("out", "float").setNodeName("c")

        shader = doc.addNode("standard_surface", "ss", "surfaceshader")
        inp = shader.addInput("specular", "float")
        inp.setAttribute("nodegraph", "NG_spec")
        inp.setAttribute("output", "out")
        mat = doc.addNode("surfacematerial", "m", "material")
        mat.addInput("surfaceshader", "surfaceshader").setNodeName("ss")

        mats = [{
            "name": "m",
            "shader_model": "standard_surface",
            "params": {"specular": 1.0},  # hypothetical "clobbered" value
            "textures": {},
        }]
        _recover_baker_clobbered_iors(doc, mats)
        # Specular is NOT in the recovery list → param untouched
        assert mats[0]["params"]["specular"] == 1.0

    def test_recover_handles_missing_shader_nodes(self):
        """Material node without a surface shader is skipped gracefully."""
        doc = mx.createDocument()
        # Material node with no shader connection
        doc.addNode("surfacematerial", "lonely", "material")
        mats = [{"name": "lonely", "shader_model": None,
                 "params": {}, "textures": {}}]
        _recover_baker_clobbered_iors(doc, mats)  # must not raise
        assert mats[0]["params"] == {}
