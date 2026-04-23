"""ambientCG: download a material by name + resolution."""

import io
import logging
import zipfile
from pathlib import Path

import requests

from threejs_materials.sources.common import SourceResult

log = logging.getLogger(__name__)

LICENSE = "CC0 1.0"
BROWSE_URL = "https://ambientcg.com/list?type=material"

_RESOLUTIONS = ("1K", "2K", "4K", "8K")
_VARIANTS = ("PNG", "JPG")


def material_url(name: str) -> str:
    return f"https://ambientcg.com/view?id={name}"


def fetch(name: str, resolution: str, out_dir: Path) -> SourceResult:
    """Download an ambientCG material ZIP and extract .mtlx + textures.

    *name* is the assetId (e.g. ``"Onyx015"``).
    *resolution* is a normalized key: ``"1K"``, ``"2K"``, ``"4K"``, or ``"8K"``.

    Prefers the PNG variant; falls back to JPG when the PNG package is 404.
    """
    res_u = resolution.upper()
    if res_u not in _RESOLUTIONS:
        raise ValueError(
            f"Resolution '{resolution}' not available for ambientCG. "
            f"Available: {list(_RESOLUTIONS)}"
        )

    content = None
    last_err = None
    for variant in _VARIANTS:
        url = f"https://ambientCG.com/get?file={name}_{res_u}-{variant}.zip"
        log.info("Downloading ambientCG: %s", url)
        resp = requests.get(url, timeout=120)
        if resp.status_code == 404:
            last_err = f"{url} → 404"
            log.info("ambientCG %s variant not found, trying next", variant)
            continue
        resp.raise_for_status()
        content = resp.content
        break

    if content is None:
        raise RuntimeError(
            f"ambientCG: no package found for '{name}' at {res_u} "
            f"(tried {list(_VARIANTS)}; last: {last_err})"
        )

    mtlx_path = None
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for entry in zf.namelist():
            if entry.endswith(".mtlx"):
                mtlx_path = out_dir / "material.mtlx"
                mtlx_path.write_bytes(zf.read(entry))
            elif any(
                entry.lower().endswith(ext)
                for ext in (".png", ".jpg", ".jpeg", ".exr")
            ):
                # Extract next to .mtlx (ambientCG references textures without subdirectory)
                dst = out_dir / Path(entry).name
                dst.write_bytes(zf.read(entry))

    if not mtlx_path or not mtlx_path.exists():
        raise RuntimeError(f"No .mtlx found in ambientCG ZIP for {name}")

    return SourceResult(mtlx_path=mtlx_path, license=LICENSE, url=material_url(name))
