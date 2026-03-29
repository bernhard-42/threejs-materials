"""Tests for threejs_materials.convert — offline, no GPU."""

import logging
from pathlib import Path

import MaterialX as mx
import pytest

from threejs_materials.convert import (
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
        # color = base * base_color
        assert props["color"]["value"] == pytest.approx([0.4, 0.24, 0.08])
        assert props["metalness"]["value"] == 0.0
        assert props["roughness"]["value"] == 0.4
        assert props["specularIntensity"]["value"] == 1.0
        assert props["ior"]["value"] == 1.5

    def test_standard_surface_scalar_texture_neutralization(self, tmp_path, tiny_png):
        """When texture exists, scalar should be set to neutral (1.0)."""
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
        # With texture, color value should be neutral [1, 1, 1]
        assert props["color"]["value"] == [1.0, 1.0, 1.0]
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
        # standard_surface thin_film_thickness is already in nm; pass through directly
        assert props["iridescenceThicknessRange"]["value"] == [0.0, 500.0]

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
        assert props["color"]["value"] == [0.8, 0.2, 0.1]
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
        assert props["color"]["value"] == pytest.approx([0.6, 0.6, 0.6])
        assert props["metalness"]["value"] == 0.0
        assert props["roughness"]["value"] == 0.3
        assert props["ior"]["value"] == 1.5

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
        assert props["iridescenceThicknessRange"]["value"] == [0.0, 400.0]

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
    def test_standard_surface_anisotropy(self, tmp_path):
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
        assert props["anisotropy"]["value"] == 0.7
        # 0.25 × 2π ≈ 1.5708
        assert props["anisotropyRotation"]["value"] == pytest.approx(
            0.25 * 2.0 * 3.141592653589793
        )

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

    def test_open_pbr_surface_anisotropy(self, tmp_path):
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
        assert props["anisotropy"]["value"] == 0.6
        assert "anisotropyRotation" not in props


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
            props = to_threejs_physical(mat, tmp_path)
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
        assert props["iridescenceThicknessRange"]["value"] == [0.0, 300.0]


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
