"""PhysicallyBased: fetch parametric material data and generate .mtlx.

Generates ``open_pbr_surface`` MaterialX documents matching the logic used by
https://github.com/AntonPalmqvist/physically-based-api/blob/main/scripts/create-materialx.mjs
"""

import logging
from pathlib import Path

import MaterialX as mx
import requests

log = logging.getLogger(__name__)

API_URL = "https://api.physicallybased.info/v2/materials"

LICENSE = "CC0 1.0"

RESOLUTION_MAP = {}  # no resolution needed

# API keys to skip (not shader inputs).
_SKIP = {
    "name", "density", "densityRange", "category", "description",
    "sources", "tags", "reference", "references", "group", "images",
    "viscosity", "surfaceTension", "acousticAbsorption",
    "complexIor",
}


def _extract_color(hit: dict) -> list[float]:
    """Extract srgb-linear color from v2 ``color`` field."""
    for entry in hit.get("color", []):
        if entry.get("colorSpace") == "srgb-linear":
            return entry["color"]
    entries = hit.get("color", [])
    if entries:
        return entries[0].get("color", [0.8, 0.8, 0.8])
    return [0.8, 0.8, 0.8]


def _extract_f82_specular_color(hit: dict) -> list[float] | None:
    """Extract F82-format srgb-linear specularColor from v2 nested structure."""
    spec = hit.get("specularColor")
    if not spec:
        return None
    # Website uses index [1] (F82), then .color[0].color (first colorSpace)
    if len(spec) > 1:
        f82 = spec[1]
    else:
        f82 = spec[0]
    colors = f82.get("color", [])
    for entry in colors:
        if entry.get("colorSpace") == "srgb-linear":
            return entry["color"]
    if colors:
        return colors[0].get("color")
    return None


def material_url(name: str) -> str:
    return "https://physicallybased.info/"


def download(name: str, out_dir: Path) -> tuple[Path, dict]:
    """Fetch a PhysicallyBased material and generate a .mtlx file.

    Returns ``(mtlx_path, property_overrides)`` — overrides carry values
    that can't round-trip through the .mtlx (e.g. thin-film thickness range).
    """
    log.info("Fetching PhysicallyBased materials list (v2)")
    resp = requests.get(API_URL, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    materials = body.get("data", body)

    mat = None
    for m in materials:
        if m.get("name", "").lower() == name.lower():
            mat = m
            break

    if mat is None:
        available = sorted(m.get("name", "") for m in materials)
        raise RuntimeError(
            f"PhysicallyBased: material '{name}' not found. "
            f"Available ({len(available)}): {', '.join(available[:20])}..."
        )

    # Collect property overrides that can't round-trip through .mtlx
    overrides = {}
    raw_tf = mat.get("thinFilmThickness")
    if isinstance(raw_tf, list) and len(raw_tf) >= 2:
        overrides["iridescenceThicknessRange"] = [float(raw_tf[0]), float(raw_tf[1])]

    mtlx_path = _generate_mtlx(mat, out_dir)
    return mtlx_path, overrides


def _set_input(shader_node, name: str, type_str: str, value):
    """Add an input from the node def and set its value string."""
    inp = shader_node.addInputFromNodeDef(name)
    if not inp:
        log.debug("Skipping unsupported input: %s", name)
        return
    if isinstance(value, list):
        inp.setValueString(", ".join(f"{x:.3f}" if isinstance(x, float) else str(x) for x in value))
    elif isinstance(value, float):
        inp.setValueString(f"{value:.3f}" if "color" in type_str else str(value))
    elif isinstance(value, bool):
        inp.setValueString("true" if value else "false")
    else:
        inp.setValueString(str(value))


def _generate_mtlx(mat: dict, out_dir: Path) -> Path:
    """Generate an open_pbr_surface .mtlx document from PhysicallyBased API data.

    Follows the same logic as the website's create-materialx.mjs.
    """
    mat_name = mat["name"].replace(" ", "_").replace("-", "_").replace(":", "_").replace(".", "_")

    doc = mx.createDocument()
    stdlib = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), stdlib)
    doc.importLibrary(stdlib)

    # Create shader and material nodes
    shader_name = "open_pbr_surface_surfaceshader"
    shader_node = doc.addNode("open_pbr_surface", shader_name, mx.SURFACE_SHADER_TYPE_STRING)

    material_name = doc.createValidChildName(mat_name)
    material_node = doc.addNode(
        mx.SURFACE_MATERIAL_NODE_STRING, material_name, mx.MATERIAL_TYPE_STRING
    )
    shader_input = material_node.addInput(
        mx.SURFACE_SHADER_TYPE_STRING, mx.SURFACE_SHADER_TYPE_STRING
    )
    shader_input.setAttribute("nodename", shader_node.getName())

    color = _extract_color(mat)
    metalness = mat.get("metalness", 0)
    roughness = mat.get("roughness", 0.3)
    ior = mat.get("ior")
    transmission = mat.get("transmission")
    subsurface_radius = mat.get("subsurfaceRadius")

    # base_color — only when not default AND not transmission AND not subsurface
    if color != [0.8, 0.8, 0.8] and not transmission and not subsurface_radius:
        _set_input(shader_node, "base_color", "color3", color)

    # base_metalness
    if metalness > 0:
        _set_input(shader_node, "base_metalness", "float", float(metalness))

    # specular_color (F82 format)
    spec_color = _extract_f82_specular_color(mat)
    if spec_color:
        _set_input(shader_node, "specular_color", "color3", spec_color)

    # specular_roughness — only when not default 0.3
    if roughness != 0.3:
        _set_input(shader_node, "specular_roughness", "float", float(roughness))

    # specular_ior — only for non-metals and non-default
    if ior and metalness < 1 and ior != 1.5:
        _set_input(shader_node, "specular_ior", "float", float(ior))

    # transmission
    if transmission:
        _set_input(shader_node, "transmission_weight", "float", float(transmission))

        # transmission_color = base color when color is not white
        if color != [1, 1, 1]:
            _set_input(shader_node, "transmission_color", "color3", color)

        # transmission_depth
        tx_depth = mat.get("transmissionDepth")
        if tx_depth:
            _set_input(shader_node, "transmission_depth", "float", float(tx_depth))

        # transmission_dispersion
        tx_disp = mat.get("transmissionDispersion")
        if tx_disp:
            _set_input(shader_node, "transmission_dispersion_scale", "float", 1.0)
            _set_input(shader_node, "transmission_dispersion_abbe_number", "float", float(tx_disp))

    # subsurface
    if subsurface_radius:
        _set_input(shader_node, "subsurface_weight", "float", 1.0)
        _set_input(shader_node, "subsurface_color", "color3", color)
        _set_input(shader_node, "subsurface_radius_scale", "color3", subsurface_radius)

    # thin film
    tf_thickness = mat.get("thinFilmThickness")
    if tf_thickness:
        _set_input(shader_node, "thin_film_weight", "float", 1.0)
        # nm → μm (divide by 1000); use typical [2] if available, else [0]
        if isinstance(tf_thickness, list):
            nm = tf_thickness[2] if len(tf_thickness) > 2 else tf_thickness[0]
        else:
            nm = tf_thickness
        _set_input(shader_node, "thin_film_thickness", "float", nm / 1000)

        tf_ior = mat.get("thinFilmIor")
        if tf_ior:
            _set_input(shader_node, "thin_film_ior", "float", float(tf_ior))

        # thin walled when both thin film and transmission
        if transmission:
            _set_input(shader_node, "geometry_thin_walled", "boolean", True)

    # Write without library elements
    out_dir.mkdir(parents=True, exist_ok=True)
    mtlx_path = out_dir / "material.mtlx"

    write_options = mx.XmlWriteOptions()
    write_options.writeXIncludeEnable = False
    write_options.elementPredicate = lambda elem: not elem.hasSourceUri()
    mx.writeToXmlFile(doc, str(mtlx_path), write_options)

    return mtlx_path
