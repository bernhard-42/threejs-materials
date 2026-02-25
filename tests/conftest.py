"""Shared fixtures for materialx-db tests."""

import io
import struct
import textwrap

import pytest


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory."""
    return tmp_path


@pytest.fixture
def tiny_png(tmp_path):
    """Create a minimal 1x1 red PNG file and return its path."""
    path = tmp_path / "tiny.png"
    path.write_bytes(_make_1x1_png(255, 0, 0))
    return path


def _make_1x1_png(r, g, b):
    """Build a minimal valid 1x1 RGB PNG in memory."""

    def _chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", _crc32(c))
        return struct.pack(">I", len(data)) + c + crc

    def _crc32(data):
        import zlib
        return zlib.crc32(data) & 0xFFFFFFFF

    import zlib

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit RGB
    ihdr = _chunk(b"IHDR", ihdr_data)

    raw_row = b"\x00" + bytes([r, g, b])  # filter byte + pixel
    compressed = zlib.compress(raw_row)
    idat = _chunk(b"IDAT", compressed)

    iend = _chunk(b"IEND", b"")

    return signature + ihdr + idat + iend


def make_mtlx_string(
    mat_name="TestMat",
    shader_model="standard_surface",
    params=None,
    extra_materials=None,
):
    """Build a minimal MaterialX XML string for testing.

    Parameters
    ----------
    mat_name : str
        Name of the material node.
    shader_model : str
        Shader category (standard_surface, gltf_pbr, open_pbr_surface).
    params : dict, optional
        Input name → (type, value) pairs for the shader node.
    extra_materials : list[dict], optional
        Additional materials to include. Each dict has keys:
        name, shader_model, params (same format as above).
    """
    if params is None:
        params = {}

    def _shader_block(name, model, p, indent="  "):
        shader_name = f"{name}_shader"
        lines = [f'{indent}<{model} name="{shader_name}" type="surfaceshader">']
        for inp_name, (inp_type, inp_val) in p.items():
            lines.append(
                f'{indent}  <input name="{inp_name}" type="{inp_type}" value="{inp_val}" />'
            )
        lines.append(f"{indent}</{model}>")
        lines.append(
            f'{indent}<surfacematerial name="{name}" type="material">'
        )
        lines.append(
            f'{indent}  <input name="surfaceshader" type="surfaceshader"'
            f' nodename="{shader_name}" />'
        )
        lines.append(f"{indent}</surfacematerial>")
        return "\n".join(lines)

    blocks = [_shader_block(mat_name, shader_model, params)]
    if extra_materials:
        for em in extra_materials:
            blocks.append(
                _shader_block(
                    em["name"],
                    em.get("shader_model", shader_model),
                    em.get("params", {}),
                )
            )

    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <materialx version="1.38">
        {chr(10).join(blocks)}
        </materialx>
    """)


# Predefined sample properties for each shader model

STANDARD_SURFACE_PARAMS = {
    "base": ("float", "0.8"),
    "base_color": ("color3", "0.5, 0.3, 0.1"),
    "metalness": ("float", "0.0"),
    "specular_roughness": ("float", "0.4"),
    "specular": ("float", "1.0"),
    "specular_color": ("color3", "1.0, 1.0, 1.0"),
    "specular_IOR": ("float", "1.5"),
}

GLTF_PBR_PARAMS = {
    "base_color": ("color3", "0.8, 0.2, 0.1"),
    "metallic": ("float", "0.0"),
    "roughness": ("float", "0.5"),
    "ior": ("float", "1.5"),
}

OPEN_PBR_SURFACE_PARAMS = {
    "base_weight": ("float", "1.0"),
    "base_color": ("color3", "0.6, 0.6, 0.6"),
    "base_metalness": ("float", "0.0"),
    "specular_roughness": ("float", "0.3"),
    "specular_ior": ("float", "1.5"),
}
