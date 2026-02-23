"""PhysicallyBased: fetch a parametric material by name."""

import logging

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.physicallybased.info/materials"

RESOLUTION_MAP = {}  # no resolution needed


def download(name: str) -> dict:
    """Fetch a PhysicallyBased material and return Three.js properties directly.

    *name* is the material name (e.g. ``"Titanium"``).

    Returns a ``properties`` dict ready for the output JSON
    (no .mtlx intermediate needed — these are purely parametric).
    """
    log.info("Fetching PhysicallyBased materials list")
    resp = requests.get(API_URL, timeout=10)
    resp.raise_for_status()
    materials = resp.json()

    # Find by name (case-insensitive)
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

    return _to_threejs_properties(mat)


def _to_threejs_properties(mat: dict) -> dict:
    """Map PhysicallyBased API fields to MeshPhysicalMaterial properties."""
    props = {}

    color = mat.get("color")
    if color:
        props["color"] = {"value": color}

    metalness = mat.get("metalness")
    if metalness is not None:
        props["metalness"] = {"value": float(metalness)}

    roughness = mat.get("roughness")
    if roughness is not None:
        props["roughness"] = {"value": float(roughness)}

    ior = mat.get("ior")
    if ior is not None:
        props["ior"] = {"value": float(ior)}

    specular_color = mat.get("specularColor")
    if specular_color:
        props["specularColor"] = {"value": specular_color}

    return props
