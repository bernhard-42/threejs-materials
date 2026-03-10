"""Extract UsdPreviewSurface materials from USD files (.usda, .usdc, .usdz)."""

import logging
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdShade

from threejs_materials.convert import encode_texture_base64

log = logging.getLogger(__name__)

# UsdPreviewSurface defaults (from the spec).
# Inputs at their default value are skipped in the output.
_DEFAULTS = {
    "diffuseColor": Gf.Vec3f(0.18, 0.18, 0.18),
    "emissiveColor": Gf.Vec3f(0.0, 0.0, 0.0),
    "specularColor": Gf.Vec3f(0.0, 0.0, 0.0),
    "metallic": 0.0,
    "roughness": 0.5,
    "clearcoat": 0.0,
    "clearcoatRoughness": 0.01,
    "opacity": 1.0,
    "opacityThreshold": 0.0,
    "ior": 1.5,
    "displacement": 0.0,
    "occlusion": 1.0,
    "useSpecularWorkflow": 0,
}

# Map UsdPreviewSurface input names → Three.js property names.
_NAME_MAP = {
    "diffuseColor": "color",
    "metallic": "metalness",
    "roughness": "roughness",
    "normal": "normal",
    "emissiveColor": "emissive",
    "clearcoat": "clearcoat",
    "clearcoatRoughness": "clearcoatRoughness",
    "ior": "ior",
    "occlusion": "ao",
    "displacement": "displacement",
    "opacity": "opacity",
    "opacityThreshold": "alphaTest",
    "specularColor": "specularColor",
}


def _resolve_texture(shader, input_name, usd_dir):
    """Follow a connection from a UsdPreviewSurface input to a UsdUVTexture
    node and return the resolved texture file path, or None."""
    inp = shader.GetInput(input_name)
    if not inp or not inp.HasConnectedSource():
        return None

    source_info = inp.GetConnectedSource()
    if not source_info or not source_info[0]:
        return None

    source_shader = UsdShade.Shader(source_info[0].GetPrim())
    if not source_shader:
        return None

    shader_id = source_shader.GetIdAttr().Get()
    if shader_id != "UsdUVTexture":
        return None

    file_input = source_shader.GetInput("file")
    if not file_input:
        return None

    file_val = file_input.Get()
    if file_val is None:
        return None

    # SdfAssetPath → resolve to actual path
    if isinstance(file_val, Sdf.AssetPath):
        resolved = file_val.resolvedPath or file_val.path
    else:
        resolved = str(file_val)

    if not resolved:
        return None

    tex_path = Path(resolved)
    if not tex_path.is_absolute():
        tex_path = usd_dir / tex_path

    if tex_path.exists():
        return tex_path

    return None


def _extract_value(shader, input_name):
    """Read a scalar or color value from a UsdPreviewSurface input.
    Returns None if the input doesn't exist or has no authored value."""
    inp = shader.GetInput(input_name)
    if not inp:
        return None

    # Skip if connected to a texture (value will come from texture)
    if inp.HasConnectedSource():
        return None

    attr = inp.GetAttr()
    if attr is None or not attr.HasAuthoredValue():
        return None

    raw = attr.Get()
    if raw is None:
        return None

    # Convert USD types to Python primitives
    if isinstance(raw, (Gf.Vec3f, Gf.Vec3d)):
        return [float(raw[0]), float(raw[1]), float(raw[2])]
    if isinstance(raw, (Gf.Vec4f, Gf.Vec4d)):
        return [float(raw[i]) for i in range(4)]
    if isinstance(raw, (int, float, bool)):
        return raw

    return raw


def _is_default(input_name, value):
    """Check if a value matches the UsdPreviewSurface default for that input."""
    default = _DEFAULTS.get(input_name)
    if default is None:
        return False

    if isinstance(default, Gf.Vec3f) and isinstance(value, list):
        return all(abs(value[i] - default[i]) < 1e-6 for i in range(3))

    if isinstance(default, (int, float)) and isinstance(value, (int, float)):
        return abs(float(value) - float(default)) < 1e-6

    return value == default


def _find_preview_surface(material):
    """Find the UsdPreviewSurface shader connected to a material's surface output.
    Returns a UsdShade.Shader or None."""
    surface_output = material.GetSurfaceOutput()
    if not surface_output:
        return None

    source_info = surface_output.GetConnectedSource()
    if not source_info or not source_info[0]:
        return None

    shader = UsdShade.Shader(source_info[0].GetPrim())
    if not shader:
        return None

    shader_id = shader.GetIdAttr().Get()
    if shader_id == "UsdPreviewSurface":
        return shader

    return None


def extract_usd_properties(usd_path: Path) -> dict:
    """Extract Three.js MeshPhysicalMaterial properties from a USD file.

    Parameters
    ----------
    usd_path : Path
        Path to a USD file (.usda, .usdc, .usdz).

    Returns
    -------
    dict
        ``{property: {value: ..., texture: data_uri}}`` in the same
        format as ``to_threejs_physical`` output.

    Raises
    ------
    RuntimeError
        If no UsdPreviewSurface material is found.
    """
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError(f"Failed to open USD stage: {usd_path}")

    usd_dir = usd_path.parent

    # Find all UsdPreviewSurface materials
    materials = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        mat = UsdShade.Material(prim)
        shader = _find_preview_surface(mat)
        if shader:
            materials.append((mat, shader))

    if not materials:
        raise RuntimeError(f"No UsdPreviewSurface materials found in {usd_path}")

    if len(materials) > 1:
        log.warning(
            "USD file contains %d UsdPreviewSurface materials, using only the first ('%s')",
            len(materials),
            materials[0][0].GetPrim().GetName(),
        )

    _, shader = materials[0]

    # Check specular workflow flag
    use_specular = _extract_value(shader, "useSpecularWorkflow")
    is_specular_workflow = use_specular == 1

    props: dict[str, dict] = {}

    def val(name, value):
        props.setdefault(name, {})["value"] = value

    def tex(name, input_name):
        tex_path = _resolve_texture(shader, input_name, usd_dir)
        if tex_path:
            entry = props.setdefault(name, {})
            entry["texture"] = encode_texture_base64(tex_path)

    # --- diffuseColor ---
    has_diffuse_tex = _resolve_texture(shader, "diffuseColor", usd_dir) is not None
    if has_diffuse_tex:
        val("color", [1.0, 1.0, 1.0])
    else:
        diffuse = _extract_value(shader, "diffuseColor")
        if diffuse is not None and not _is_default("diffuseColor", diffuse):
            val("color", diffuse)
        elif diffuse is not None:
            val("color", diffuse)
    tex("color", "diffuseColor")

    # --- metallic ---
    if not is_specular_workflow:
        has_metallic_tex = _resolve_texture(shader, "metallic", usd_dir) is not None
        if has_metallic_tex:
            val("metalness", 1.0)
        else:
            metallic = _extract_value(shader, "metallic")
            if metallic is not None and not _is_default("metallic", metallic):
                val("metalness", metallic)
        tex("metalness", "metallic")

    # --- roughness ---
    has_roughness_tex = _resolve_texture(shader, "roughness", usd_dir) is not None
    if has_roughness_tex:
        val("roughness", 1.0)
    else:
        roughness = _extract_value(shader, "roughness")
        if roughness is not None and not _is_default("roughness", roughness):
            val("roughness", roughness)
    tex("roughness", "roughness")

    # --- normal (texture only) ---
    tex("normal", "normal")

    # --- emissiveColor ---
    has_emissive_tex = _resolve_texture(shader, "emissiveColor", usd_dir) is not None
    if has_emissive_tex:
        val("emissive", [1.0, 1.0, 1.0])
    else:
        emissive = _extract_value(shader, "emissiveColor")
        if emissive is not None and not _is_default("emissiveColor", emissive):
            val("emissive", emissive)
    tex("emissive", "emissiveColor")

    # --- clearcoat ---
    clearcoat = _extract_value(shader, "clearcoat")
    if clearcoat is not None and not _is_default("clearcoat", clearcoat):
        val("clearcoat", clearcoat)
        clearcoat_rough = _extract_value(shader, "clearcoatRoughness")
        if clearcoat_rough is not None:
            val("clearcoatRoughness", clearcoat_rough)

    # --- ior ---
    ior = _extract_value(shader, "ior")
    if ior is not None and not _is_default("ior", ior):
        val("ior", ior)

    # --- occlusion (texture only) ---
    tex("ao", "occlusion")

    # --- displacement (texture only) ---
    tex("displacement", "displacement")

    # --- opacity ---
    opacity_threshold = _extract_value(shader, "opacityThreshold")
    opacity = _extract_value(shader, "opacity")
    has_opacity_tex = _resolve_texture(shader, "opacity", usd_dir) is not None

    if opacity_threshold is not None and not _is_default(
        "opacityThreshold", opacity_threshold
    ):
        # Mask/cutout mode
        val("alphaTest", opacity_threshold)
        tex("opacity", "opacity")
    elif has_opacity_tex or (
        opacity is not None and not _is_default("opacity", opacity)
    ):
        # Blend mode
        if opacity is not None and not _is_default("opacity", opacity):
            val("opacity", opacity)
            val("transparent", True)
        tex("opacity", "opacity")

    # --- specularColor (only for specular workflow) ---
    if is_specular_workflow:
        has_spec_tex = _resolve_texture(shader, "specularColor", usd_dir) is not None
        if has_spec_tex:
            val("specularColor", [1.0, 1.0, 1.0])
        else:
            spec = _extract_value(shader, "specularColor")
            if spec is not None and not _is_default("specularColor", spec):
                val("specularColor", spec)
        tex("specularColor", "specularColor")

    return props
