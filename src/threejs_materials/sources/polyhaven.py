"""PolyHaven: download a material by name + resolution."""

import logging
from pathlib import Path

import requests

from threejs_materials.sources import SourceResult

log = logging.getLogger(__name__)

LICENSE = "CC0 1.0"
BROWSE_URL = "https://polyhaven.com/textures"

_RESOLUTION_MAP = {
    "1K": "1k",
    "2K": "2k",
    "4K": "4k",
    "8K": "8k",
}

_HEADERS = {"User-Agent": "MTLX_Polyaven_Loader/1.0"}


def material_url(name: str) -> str:
    return f"https://polyhaven.com/a/{name.replace(' ', '_').lower()}"


def fetch(name: str, resolution: str, out_dir: Path) -> SourceResult:
    """Download a PolyHaven material (.mtlx + textures).

    *name* is the asset slug (e.g. ``"plank_flooring_04"``).
    *resolution* is a normalized key: ``"1K"``, ``"2K"``, ``"4K"``, or ``"8K"``.
    """
    resolution = _RESOLUTION_MAP.get(resolution.upper(), resolution.lower())
    name = name.replace(" ", "_").lower()

    # Fetch file listing for this asset
    log.info("Fetching PolyHaven files for '%s'", name)
    resp = requests.get(
        f"https://api.polyhaven.com/files/{name}",
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Structure: data["mtlx"][resolution]["mtlx"]["url"] + ["include"]
    mtlx_section = data.get("mtlx", {})
    res_data = mtlx_section.get(resolution)
    if not res_data:
        available = list(mtlx_section.keys())
        raise RuntimeError(
            f"PolyHaven: resolution '{resolution}' not available for '{name}'. "
            f"Available: {available}"
        )

    mtlx_info = res_data.get("mtlx", {})
    mtlx_url = mtlx_info.get("url")
    if not mtlx_url:
        raise RuntimeError(f"PolyHaven: no .mtlx URL for '{name}' at {resolution}")

    # Download the .mtlx file
    log.info("Downloading PolyHaven mtlx: %s", mtlx_url)
    resp = requests.get(mtlx_url, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    mtlx_path = out_dir / "material.mtlx"
    mtlx_path.write_text(resp.text)

    # Download textures from the "include" map
    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    includes = mtlx_info.get("include", {})
    for tex_rel_path, tex_info in includes.items():
        tex_url = tex_info.get("url")
        if not tex_url:
            continue
        tex_name = Path(tex_rel_path).name
        log.info("Downloading texture: %s", tex_name)
        tex_resp = requests.get(tex_url, headers=_HEADERS, timeout=120)
        tex_resp.raise_for_status()
        dst = tex_dir / tex_name
        dst.write_bytes(tex_resp.content)

    return SourceResult(mtlx_path=mtlx_path, license=LICENSE, url=material_url(name))
