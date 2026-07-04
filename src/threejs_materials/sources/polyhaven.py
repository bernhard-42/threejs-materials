"""PolyHaven: download a material by name + resolution."""

import logging
from pathlib import Path

import requests

from threejs_materials.sources.common import SourceResult, normalize_name
from threejs_materials.utils import OpenEXR

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


def _resolve(name: str, resolution: str) -> tuple[dict, dict, str, str]:
    """Fetch the asset listing and locate the .mtlx entry for *resolution*.

    Returns ``(data, mtlx_info, resolution, name)`` with *resolution* and
    *name* normalized to PolyHaven's slug/key form.
    """
    resolution = _RESOLUTION_MAP.get(resolution.upper(), resolution.lower())
    name = name.replace(" ", "_").lower()

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
    if not mtlx_info.get("url"):
        raise RuntimeError(f"PolyHaven: no .mtlx URL for '{name}' at {resolution}")

    return data, mtlx_info, resolution, name


def _fetch_gltf(data: dict, resolution: str, name: str, out_dir: Path) -> Path:
    """Download a PolyHaven glTF (.gltf + .bin + textures) into *out_dir*,
    preserving the glTF's include paths. Returns the .gltf path."""
    gltf_section = data.get("gltf", {})
    res_data = gltf_section.get(resolution)
    gltf_info = res_data.get("gltf", {}) if res_data else {}
    gltf_url = gltf_info.get("url")
    if not gltf_url:
        raise RuntimeError(
            f"PolyHaven: no glTF available for '{name}' at {resolution} "
            "(needed because openexr is not installed to read the .mtlx EXR maps)"
        )

    gltf_path = out_dir / Path(gltf_url.split("?")[0]).name
    log.info("Downloading PolyHaven glTF: %s", gltf_url)
    resp = requests.get(gltf_url, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    gltf_path.write_bytes(resp.content)

    for rel_path, info in gltf_info.get("include", {}).items():
        url = info.get("url")
        if not url:
            continue
        dst = out_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading glTF include: %s", rel_path)
        r = requests.get(url, headers=_HEADERS, timeout=120)
        r.raise_for_status()
        dst.write_bytes(r.content)

    return gltf_path


def fetch(name: str, resolution: str, out_dir: Path) -> SourceResult:
    """Download a PolyHaven material (.mtlx + textures).

    *name* is the asset slug (e.g. ``"plank_flooring_04"``).
    *resolution* is a normalized key: ``"1K"``, ``"2K"``, ``"4K"``, or ``"8K"``.

    The .mtlx maps come as EXR. When openexr is not installed, falls back to
    PolyHaven's glTF download (PNG/JPG textures) so no EXR decoding is needed.
    """
    data, mtlx_info, resolution, name = _resolve(name, resolution)

    includes = mtlx_info.get("include", {})
    needs_exr = any(k.lower().endswith(".exr") for k in includes)
    if needs_exr and OpenEXR is None:
        gltf_path = _fetch_gltf(data, resolution, name, out_dir)
        return SourceResult(
            gltf_path=gltf_path, license=LICENSE, url=material_url(name)
        )

    mtlx_url = mtlx_info["url"]

    # Download the .mtlx file
    log.info("Downloading PolyHaven mtlx: %s", mtlx_url)
    resp = requests.get(mtlx_url, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    mtlx_path = out_dir / "material.mtlx"
    mtlx_path.write_text(resp.text)

    # Download textures from the "include" map
    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

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

    # Side-load AO from the per-channel API: standard_surface has no AO
    # input, so polyhaven omits it from the .mtlx, but the asset listing
    # exposes a PNG at a parallel URL. We download it next to the other
    # textures and let _SourceLoader merge it into the properties dict.
    extra_textures: dict = {}
    ao_url = (
        data.get("AO", {}).get(resolution, {}).get("png", {}).get("url")
    )
    if ao_url:
        ao_filename = Path(ao_url).name
        ao_path = tex_dir / ao_filename
        log.info("Downloading AO: %s", ao_filename)
        ao_resp = requests.get(ao_url, headers=_HEADERS, timeout=120)
        ao_resp.raise_for_status()
        ao_path.write_bytes(ao_resp.content)
        extra_textures["ao"] = ao_path

    return SourceResult(
        mtlx_path=mtlx_path,
        license=LICENSE,
        url=material_url(name),
        extra_textures=extra_textures,
    )


def download(name: str, resolution: str, dest: Path) -> None:
    """Download a PolyHaven material (.mtlx + textures) into
    ``<dest>/<normalized_name>/``, preserving the .mtlx's include paths so its
    texture references resolve. The side-loaded AO map is written alongside."""
    data, mtlx_info, resolution, name = _resolve(name, resolution)
    mtlx_url = mtlx_info["url"]

    out_dir = dest / normalize_name(name)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Downloading PolyHaven mtlx: %s", mtlx_url)
    resp = requests.get(mtlx_url, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    (out_dir / Path(mtlx_url.split("?")[0]).name).write_text(resp.text)

    for tex_rel_path, tex_info in mtlx_info.get("include", {}).items():
        tex_url = tex_info.get("url")
        if not tex_url:
            continue
        dst = out_dir / tex_rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading texture: %s", tex_rel_path)
        tex_resp = requests.get(tex_url, headers=_HEADERS, timeout=120)
        tex_resp.raise_for_status()
        dst.write_bytes(tex_resp.content)

    ao_url = data.get("AO", {}).get(resolution, {}).get("png", {}).get("url")
    if ao_url:
        ao_path = out_dir / Path(ao_url).name
        log.info("Downloading AO: %s", ao_path.name)
        ao_resp = requests.get(ao_url, headers=_HEADERS, timeout=120)
        ao_resp.raise_for_status()
        ao_path.write_bytes(ao_resp.content)
