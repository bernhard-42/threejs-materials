"""PhysicallyBased: fetch parametric material data and convert directly to Three.js properties.

Maps PhysicallyBased API v2 data → MeshPhysicalMaterial properties without
going through a MaterialX intermediate document.
"""

import logging
from pathlib import Path

import requests

from threejs_materials.sources import SourceResult

log = logging.getLogger(__name__)

API_URL = "https://api.physicallybased.info/v2/materials"

LICENSE = "CC0 1.0"
BROWSE_URL = "https://physicallybased.info/"


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


def _fetch_material_data(name: str) -> dict:
    """Fetch material data from the PhysicallyBased API."""
    log.info("Fetching PhysicallyBased materials list (v2)")
    resp = requests.get(API_URL, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    materials = body.get("data", body)

    for m in materials:
        if m.get("name", "").lower() == name.lower():
            return m

    available = sorted(m.get("name", "") for m in materials)
    raise RuntimeError(
        f"PhysicallyBased: material '{name}' not found. "
        f"Available ({len(available)}): {', '.join(available[:20])}..."
    )


def _to_threejs_properties(mat: dict) -> dict:
    """Convert PhysicallyBased API data directly to Three.js MeshPhysicalMaterial properties.

    This collapses the former _generate_mtlx → to_threejs_physical(open_pbr_surface)
    pipeline into a single step. All materials are parametric (no textures).
    """
    props: dict[str, dict] = {}

    def val(name, value):
        props.setdefault(name, {})["value"] = value

    color = _extract_color(mat)
    metalness = mat.get("metalness", 0)
    roughness = mat.get("roughness", 0.3)
    ior = mat.get("ior")
    transmission = mat.get("transmission")
    subsurface_radius = mat.get("subsurfaceRadius")

    # color — skip when transmission or subsurface is active
    # (transmissive materials use attenuationColor instead;
    #  Three.js has no SSS so subsurface color has no target)
    if not transmission and not subsurface_radius:
        val("color", color)

    # metalness — skip default 0
    if metalness > 0:
        val("metalness", float(metalness))

    # roughness — skip default 0.3
    if roughness != 0.3:
        val("roughness", float(roughness))

    # specularColor (F82 format) + specularIntensity
    spec_color = _extract_f82_specular_color(mat)
    if spec_color:
        val("specularIntensity", 1.0)
        val("specularColor", spec_color)

    # ior — skip default 1.5 and pure metals
    if ior and metalness < 1 and ior != 1.5:
        val("ior", float(ior))

    # transmission
    if transmission:
        val("transmission", float(transmission))

        # attenuationColor from the base color (when not white)
        if color != [1, 1, 1]:
            val("attenuationColor", color)

        # attenuationDistance from transmissionDepth
        tx_depth = mat.get("transmissionDepth")
        if tx_depth:
            val("attenuationDistance", float(tx_depth))

        # dispersion: Abbe number → Three.js dispersion (= 20 / V_d)
        tx_disp = mat.get("transmissionDispersion")
        if tx_disp and tx_disp > 0:
            val("dispersion", 20.0 / tx_disp)

    # thin film → iridescence
    tf_thickness = mat.get("thinFilmThickness")
    if tf_thickness:
        val("iridescence", 1.0)

        # Thickness is in nm; Three.js also expects nm.
        # (The old pipeline did nm÷1000→μm then μm×1000→nm — a no-op.)
        if isinstance(tf_thickness, list):
            nm = tf_thickness[2] if len(tf_thickness) > 2 else tf_thickness[0]
        else:
            nm = tf_thickness
        val("iridescenceThicknessRange", [0.0, float(nm)])

        tf_ior = mat.get("thinFilmIor")
        if tf_ior:
            val("iridescenceIOR", float(tf_ior))

        # thin film + transmission → render both sides
        if transmission:
            val("side", 2)  # THREE.DoubleSide

    return props


def fetch(name: str, resolution: str | None, out_dir: Path) -> SourceResult:
    """Fetch a PhysicallyBased material and convert directly to Three.js properties."""
    mat = _fetch_material_data(name)
    properties = _to_threejs_properties(mat)

    # Collect property overrides (thin-film thickness range from list values)
    overrides = {}
    raw_tf = mat.get("thinFilmThickness")
    if isinstance(raw_tf, list) and len(raw_tf) >= 2:
        overrides["iridescenceThicknessRange"] = [float(raw_tf[0]), float(raw_tf[1])]

    return SourceResult(
        properties=properties,
        license=LICENSE,
        url=material_url(name),
        overrides=overrides,
    )
