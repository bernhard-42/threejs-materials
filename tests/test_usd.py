"""Tests for USD (UsdPreviewSurface) material extraction."""

import logging
import textwrap
from pathlib import Path

import pytest

from materialx_db.library import Material
from materialx_db.usd_reader import extract_usd_properties

from conftest import _make_1x1_png


# ---------------------------------------------------------------------------
# USDA template helpers
# ---------------------------------------------------------------------------


def _usda_material(
    mat_name="TestMat",
    shader_inputs="",
    texture_nodes="",
):
    """Build a minimal USDA string with a UsdPreviewSurface material."""
    shader_block = textwrap.indent(shader_inputs, "        ").rstrip()
    tex_block = textwrap.indent(texture_nodes, "    ").rstrip()
    lines = [
        '#usda 1.0',
        '',
        f'def Material "{mat_name}"',
        '{',
        f'    token outputs:surface.connect = </{mat_name}/PreviewSurface.outputs:surface>',
        '',
        '    def Shader "PreviewSurface"',
        '    {',
        '        uniform token info:id = "UsdPreviewSurface"',
    ]
    if shader_block:
        lines.append(shader_block)
    lines += [
        '        token outputs:surface',
        '    }',
    ]
    if tex_block:
        lines.append('')
        lines.append(tex_block)
    lines += [
        '}',
        '',
    ]
    return '\n'.join(lines)


def _texture_node(node_name, file_path, output_type="float3"):
    """Build a UsdUVTexture node USDA snippet."""
    if output_type == "float3":
        output_line = 'float3 outputs:rgb'
    else:
        output_line = 'float outputs:r'
    return '\n'.join([
        f'def Shader "{node_name}"',
        '{',
        f'    uniform token info:id = "UsdUVTexture"',
        f'    asset inputs:file = @{file_path}@',
        f'    {output_line}',
        '}',
    ])


def _write_usda(tmp_path, usda_string, filename="test.usda"):
    """Write a USDA string to a temp file and return the path."""
    p = tmp_path / filename
    p.write_text(usda_string)
    return p


# ---------------------------------------------------------------------------
# Basic scalar extraction
# ---------------------------------------------------------------------------


class TestBasicScalars:
    def test_diffuse_color(self, tmp_path):
        usda = _usda_material(shader_inputs='color3f inputs:diffuseColor = (0.8, 0.2, 0.1)')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["color"]["value"] == pytest.approx([0.8, 0.2, 0.1])

    def test_metallic(self, tmp_path):
        usda = _usda_material(shader_inputs='float inputs:metallic = 0.9')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["metalness"]["value"] == pytest.approx(0.9)

    def test_roughness_non_default(self, tmp_path):
        usda = _usda_material(shader_inputs='float inputs:roughness = 0.3')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["roughness"]["value"] == pytest.approx(0.3)

    def test_emissive_color(self, tmp_path):
        usda = _usda_material(shader_inputs='color3f inputs:emissiveColor = (1.0, 0.5, 0.0)')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["emissive"]["value"] == pytest.approx([1.0, 0.5, 0.0])

    def test_clearcoat(self, tmp_path):
        usda = _usda_material(shader_inputs=textwrap.dedent("""\
            float inputs:clearcoat = 0.8
            float inputs:clearcoatRoughness = 0.1"""))
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["clearcoat"]["value"] == pytest.approx(0.8)
        assert props["clearcoatRoughness"]["value"] == pytest.approx(0.1)

    def test_ior(self, tmp_path):
        usda = _usda_material(shader_inputs='float inputs:ior = 1.45')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["ior"]["value"] == pytest.approx(1.45)

    def test_full_material(self, tmp_path):
        """A material with multiple scalar inputs."""
        inputs = textwrap.dedent("""\
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)
            float inputs:metallic = 1.0
            float inputs:roughness = 0.2
            float inputs:ior = 2.0""")
        usda = _usda_material(shader_inputs=inputs)
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["color"]["value"] == pytest.approx([0.5, 0.5, 0.5])
        assert props["metalness"]["value"] == pytest.approx(1.0)
        assert props["roughness"]["value"] == pytest.approx(0.2)
        assert props["ior"]["value"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Texture connections
# ---------------------------------------------------------------------------


class TestTextureConnections:
    def test_diffuse_texture(self, tmp_path):
        """Texture connected to diffuseColor → color property with base64."""
        # Write a tiny PNG
        tex_path = tmp_path / "diffuse.png"
        tex_path.write_bytes(_make_1x1_png(128, 64, 32))

        shader_inputs = 'color3f inputs:diffuseColor.connect = </TestMat/DiffuseTex.outputs:rgb>'
        tex_nodes = _texture_node("DiffuseTex", "diffuse.png", "float3")
        usda = _usda_material(shader_inputs=shader_inputs, texture_nodes=tex_nodes)
        path = _write_usda(tmp_path, usda)

        props = extract_usd_properties(path)
        assert "color" in props
        assert "texture" in props["color"]
        assert props["color"]["texture"].startswith("data:image/png;base64,")
        # Scalar should be neutral when texture exists
        assert props["color"]["value"] == [1.0, 1.0, 1.0]

    def test_normal_texture(self, tmp_path):
        """Normal map texture → normal property (texture only)."""
        tex_path = tmp_path / "normal.png"
        tex_path.write_bytes(_make_1x1_png(128, 128, 255))

        shader_inputs = 'normal3f inputs:normal.connect = </TestMat/NormalTex.outputs:rgb>'
        tex_nodes = _texture_node("NormalTex", "normal.png", "float3")
        usda = _usda_material(shader_inputs=shader_inputs, texture_nodes=tex_nodes)
        path = _write_usda(tmp_path, usda)

        props = extract_usd_properties(path)
        assert "normal" in props
        assert "texture" in props["normal"]

    def test_roughness_texture(self, tmp_path):
        """Roughness texture → roughness property with neutral scalar."""
        tex_path = tmp_path / "roughness.png"
        tex_path.write_bytes(_make_1x1_png(128, 128, 128))

        shader_inputs = 'float inputs:roughness.connect = </TestMat/RoughTex.outputs:r>'
        tex_nodes = _texture_node("RoughTex", "roughness.png", "float")
        usda = _usda_material(shader_inputs=shader_inputs, texture_nodes=tex_nodes)
        path = _write_usda(tmp_path, usda)

        props = extract_usd_properties(path)
        assert "roughness" in props
        assert "texture" in props["roughness"]
        assert props["roughness"]["value"] == 1.0

    def test_metallic_texture(self, tmp_path):
        tex_path = tmp_path / "metallic.png"
        tex_path.write_bytes(_make_1x1_png(255, 255, 255))

        shader_inputs = 'float inputs:metallic.connect = </TestMat/MetalTex.outputs:r>'
        tex_nodes = _texture_node("MetalTex", "metallic.png", "float")
        usda = _usda_material(shader_inputs=shader_inputs, texture_nodes=tex_nodes)
        path = _write_usda(tmp_path, usda)

        props = extract_usd_properties(path)
        assert "metalness" in props
        assert "texture" in props["metalness"]
        assert props["metalness"]["value"] == 1.0

    def test_occlusion_texture(self, tmp_path):
        tex_path = tmp_path / "ao.png"
        tex_path.write_bytes(_make_1x1_png(200, 200, 200))

        shader_inputs = 'float inputs:occlusion.connect = </TestMat/AOTex.outputs:r>'
        tex_nodes = _texture_node("AOTex", "ao.png", "float")
        usda = _usda_material(shader_inputs=shader_inputs, texture_nodes=tex_nodes)
        path = _write_usda(tmp_path, usda)

        props = extract_usd_properties(path)
        assert "ao" in props
        assert "texture" in props["ao"]


# ---------------------------------------------------------------------------
# Opacity modes
# ---------------------------------------------------------------------------


class TestOpacity:
    def test_blend_mode(self, tmp_path):
        """opacity < 1 without threshold → transparent blend mode."""
        usda = _usda_material(shader_inputs='float inputs:opacity = 0.5')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["opacity"]["value"] == pytest.approx(0.5)
        assert props["transparent"]["value"] is True

    def test_mask_mode(self, tmp_path):
        """opacityThreshold > 0 → alphaTest cutout mode."""
        inputs = textwrap.dedent("""\
            float inputs:opacity = 1.0
            float inputs:opacityThreshold = 0.5""")
        usda = _usda_material(shader_inputs=inputs)
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["alphaTest"]["value"] == pytest.approx(0.5)
        # Should NOT have transparent in mask mode
        assert "transparent" not in props

    def test_opaque_default(self, tmp_path):
        """Default opacity (1.0) → no opacity/transparent properties."""
        usda = _usda_material(shader_inputs='color3f inputs:diffuseColor = (0.5, 0.5, 0.5)')
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert "opacity" not in props
        assert "transparent" not in props


# ---------------------------------------------------------------------------
# Specular workflow
# ---------------------------------------------------------------------------


class TestSpecularWorkflow:
    def test_specular_workflow(self, tmp_path):
        """useSpecularWorkflow=1 → specularColor, no metalness."""
        inputs = textwrap.dedent("""\
            int inputs:useSpecularWorkflow = 1
            color3f inputs:specularColor = (0.9, 0.8, 0.7)
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)""")
        usda = _usda_material(shader_inputs=inputs)
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["specularColor"]["value"] == pytest.approx([0.9, 0.8, 0.7])
        assert "metalness" not in props

    def test_metallic_workflow_default(self, tmp_path):
        """Default workflow → metalness present, no specularColor."""
        inputs = textwrap.dedent("""\
            float inputs:metallic = 0.8
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)""")
        usda = _usda_material(shader_inputs=inputs)
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert props["metalness"]["value"] == pytest.approx(0.8)
        assert "specularColor" not in props


# ---------------------------------------------------------------------------
# Skip defaults
# ---------------------------------------------------------------------------


class TestSkipDefaults:
    def test_default_roughness_omitted(self, tmp_path):
        """roughness=0.5 (default) should not appear in output."""
        usda = _usda_material(shader_inputs=textwrap.dedent("""\
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)
            float inputs:roughness = 0.5"""))
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert "roughness" not in props

    def test_default_metallic_omitted(self, tmp_path):
        """metallic=0.0 (default) should not appear in output."""
        usda = _usda_material(shader_inputs=textwrap.dedent("""\
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)
            float inputs:metallic = 0.0"""))
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert "metalness" not in props

    def test_default_ior_omitted(self, tmp_path):
        """ior=1.5 (default) should not appear in output."""
        usda = _usda_material(shader_inputs=textwrap.dedent("""\
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)
            float inputs:ior = 1.5"""))
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert "ior" not in props

    def test_default_emissive_omitted(self, tmp_path):
        """emissiveColor=(0,0,0) (default) should not appear in output."""
        usda = _usda_material(shader_inputs=textwrap.dedent("""\
            color3f inputs:diffuseColor = (0.5, 0.5, 0.5)
            color3f inputs:emissiveColor = (0.0, 0.0, 0.0)"""))
        path = _write_usda(tmp_path, usda)
        props = extract_usd_properties(path)
        assert "emissive" not in props


# ---------------------------------------------------------------------------
# Multiple materials warning
# ---------------------------------------------------------------------------


class TestMultipleMaterials:
    def test_multiple_materials_warns(self, tmp_path, caplog):
        """Multiple materials → warning, first material used."""
        usda = textwrap.dedent("""\
            #usda 1.0

            def Material "Mat1"
            {
                token outputs:surface.connect = </Mat1/PreviewSurface.outputs:surface>

                def Shader "PreviewSurface"
                {
                    uniform token info:id = "UsdPreviewSurface"
                    color3f inputs:diffuseColor = (1.0, 0.0, 0.0)
                    token outputs:surface
                }
            }

            def Material "Mat2"
            {
                token outputs:surface.connect = </Mat2/PreviewSurface.outputs:surface>

                def Shader "PreviewSurface"
                {
                    uniform token info:id = "UsdPreviewSurface"
                    color3f inputs:diffuseColor = (0.0, 1.0, 0.0)
                    token outputs:surface
                }
            }
        """)
        path = _write_usda(tmp_path, usda)
        with caplog.at_level(logging.WARNING, logger="materialx_db.usd_reader"):
            props = extract_usd_properties(path)

        assert any("2 UsdPreviewSurface materials" in r.message for r in caplog.records)
        # First material should be used
        assert props["color"]["value"] == pytest.approx([1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Material.from_usd("/nonexistent/path/model.usda")

    def test_no_materials(self, tmp_path):
        """USD file with no materials → RuntimeError."""
        usda = textwrap.dedent("""\
            #usda 1.0

            def Xform "Root"
            {
            }
        """)
        path = _write_usda(tmp_path, usda)
        with pytest.raises(RuntimeError, match="No UsdPreviewSurface"):
            extract_usd_properties(path)


# ---------------------------------------------------------------------------
# Material.from_usd() integration
# ---------------------------------------------------------------------------


class TestFromUsd:
    def test_basic_integration(self, tmp_path):
        """from_usd() returns a Material with correct metadata."""
        usda = _usda_material(
            shader_inputs='color3f inputs:diffuseColor = (0.7, 0.3, 0.1)')
        path = _write_usda(tmp_path, usda, filename="copper.usda")

        mat = Material.from_usd(str(path))
        assert mat.name == "copper"
        assert mat.source == "usd"
        assert mat.id == "copper"
        assert "color" in mat.properties
        assert mat.properties["color"]["value"] == pytest.approx([0.7, 0.3, 0.1])

    def test_override_works(self, tmp_path):
        """Material.override() works on USD-loaded materials."""
        usda = _usda_material(
            shader_inputs='color3f inputs:diffuseColor = (0.5, 0.5, 0.5)')
        path = _write_usda(tmp_path, usda)

        mat = Material.from_usd(str(path))
        mat2 = mat.override(roughness=0.9)
        assert mat2.properties["roughness"]["value"] == 0.9

    def test_to_dict(self, tmp_path):
        """to_dict() / to_json() work on USD-loaded materials."""
        usda = _usda_material(
            shader_inputs='color3f inputs:diffuseColor = (0.5, 0.5, 0.5)')
        path = _write_usda(tmp_path, usda)

        mat = Material.from_usd(str(path))
        d = mat.to_dict()
        assert d["source"] == "usd"
        assert "properties" in d

        import json
        j = mat.to_json()
        parsed = json.loads(j)
        assert parsed["source"] == "usd"
