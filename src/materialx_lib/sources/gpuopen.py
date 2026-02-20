"""Fetch GPUOpen catalog metadata into the materials DB."""

import json
import logging
import re
import sqlite3

import requests as _requests

from materialxMaterials.GPUOpenLoader import GPUOpenMaterialLoader

from materialx_lib.categories import categorize_by_name
from materialx_lib.db import insert_material, insert_variant

log = logging.getLogger(__name__)

PACKAGE_URL = "https://api.matlib.gpuopen.com/api/packages"

_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}
_SIZE_RE = re.compile(r"([\d.]+)\s*(gb|mb|kb|b)", re.IGNORECASE)


def _parse_size(raw: str | None) -> int | None:
    """Parse human-readable size like '2.1 MB' into bytes."""
    if not raw:
        return None
    # Try plain int first
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass
    m = _SIZE_RE.match(raw.strip())
    if m:
        return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).lower()])
    return None


def fetch_package_labels(package_uuids: list[str]) -> dict[str, tuple[str, int | None]]:
    """Fetch label and size for a list of package UUIDs. Returns {uuid: (label, size_bytes)}."""
    results = {}
    for pkg_uuid in package_uuids:
        try:
            resp = _requests.get(
                f"{PACKAGE_URL}/{pkg_uuid}",
                headers={"accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except _requests.RequestException as e:
            log.warning("API error fetching package %s: %s", pkg_uuid, e)
            results[pkg_uuid] = (pkg_uuid, None)
            continue

        label = data.get("label") or pkg_uuid
        size = _parse_size(data.get("size"))
        results[pkg_uuid] = (label, size)
    return results


def fetch_and_insert(conn: sqlite3.Connection) -> int:
    """Fetch GPUOpen material list and insert into DB. Returns count of materials."""
    loader = GPUOpenMaterialLoader()
    batches = loader.getMaterials()
    if not batches:
        log.warning("GPUOpen returned no materials")
        return 0

    count = 0
    for batch in batches:
        results = batch.get("results", [])
        for item in results:
            title = item.get("title", "")
            mtlx_name = item.get("mtlx_material_name", title.replace(" ", "_"))
            mat_id = f"gpuo:{mtlx_name}"
            category = categorize_by_name(title)
            material_type = item.get("material_type", "")

            # Build thumbnail URL from first render if available
            renders = item.get("renders_order") or item.get("renders") or []
            thumbnail_url = None
            if renders:
                thumbnail_url = f"https://api.matlib.gpuopen.com/api/renders/{renders[0]}/download"

            insert_material(
                conn,
                id=mat_id,
                source="gpuopen",
                name=title,
                category=category,
                has_textures=True,
                thumbnail_url=thumbnail_url,
                tags=[material_type] if material_type else None,
            )

            packages = item.get("packages", [])
            for i, pkg_uuid in enumerate(packages):
                insert_variant(
                    conn,
                    material_id=mat_id,
                    resolution=f"pkg_{i}",
                    download_url=f"{PACKAGE_URL}/{pkg_uuid}/download",
                    download_meta={
                        "package_id": pkg_uuid,
                        "type": "procedural" if material_type == "Parametric" else "static",
                    },
                )

            count += 1

    conn.commit()
    return count
