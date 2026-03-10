"""GPUOpen: download a material by name + resolution."""

import io
import logging
import zipfile
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.matlib.gpuopen.com/api"

LICENSE = None  # per-material, fetched from API

RESOLUTION_MAP = {
    "1K": "1k 8b",
    "2K": "2k 8b",
    "4K": "4k 8b",
}


def material_url(name: str, material_id: str = "") -> str:
    if material_id:
        return f"https://matlib.gpuopen.com/main/materials/all?id={material_id}"
    return "https://matlib.gpuopen.com/main/materials/all"

def download(name: str, resolution: str, out_dir: Path) -> tuple[Path, str, str]:
    """Download a GPUOpen material ZIP and extract .mtlx + textures.

    *name* is the material title (e.g. ``"Car Paint"``).
    *resolution* is the already-mapped label from RESOLUTION_MAP (e.g. ``"1k 8b"``).

    Returns ``(mtlx_path, license, url)`` tuple.
    """
    # Search for the material by name
    log.info("Searching GPUOpen for '%s'", name)
    resp = requests.get(
        f"{API_BASE}/materials",
        params={"search": name},
        headers={"accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"GPUOpen: no material found for '{name}'")

    # Find best match (prefer exact title match)
    material = None
    for item in results:
        if item.get("title", "").lower() == name.lower():
            material = item
            break
    if material is None:
        material = results[0]

    mat_license = material.get("license", "Unknown")
    mat_url = material_url(name, material.get("id", ""))

    packages = material.get("packages", [])
    if not packages:
        raise RuntimeError(f"GPUOpen: no packages for '{material.get('title')}'")

    # Find the package matching the requested resolution label
    pkg_uuid = _find_package(packages, resolution)

    # Download the package ZIP
    download_url = f"{API_BASE}/packages/{pkg_uuid}/download"
    log.info("Downloading GPUOpen package: %s", download_url)
    resp = requests.get(download_url, timeout=120)
    resp.raise_for_status()

    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    mtlx_path = None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for entry in zf.namelist():
            if entry.endswith(".mtlx"):
                mtlx_path = out_dir / "material.mtlx"
                mtlx_path.write_bytes(zf.read(entry))
            elif any(
                entry.lower().endswith(ext)
                for ext in (".png", ".jpg", ".jpeg", ".exr")
            ):
                dst = tex_dir / Path(entry).name
                dst.write_bytes(zf.read(entry))

    if not mtlx_path or not mtlx_path.exists():
        raise RuntimeError(f"No .mtlx found in GPUOpen ZIP for '{name}'")

    return mtlx_path, mat_license, mat_url


def _find_package(package_uuids: list[str], resolution: str) -> str:
    """Find the package UUID whose label matches *resolution*."""
    for pkg_uuid in package_uuids:
        try:
            resp = requests.get(
                f"{API_BASE}/packages/{pkg_uuid}",
                headers={"accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            info = resp.json()
        except requests.RequestException as e:
            log.warning("Error fetching package %s: %s", pkg_uuid, e)
            continue

        label = info.get("label", "")
        if label.lower() == resolution.lower():
            return pkg_uuid

    raise RuntimeError(
        f"GPUOpen: no package with label '{resolution}' "
        f"(checked {len(package_uuids)} packages)"
    )
