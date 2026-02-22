"""Fetch PolyHaven catalog metadata into the materials DB."""

import json
import logging
import sqlite3

from materialxMaterials.polyHavenLoader import PolyHavenLoader

from materialx_db.categories import categorize_polyhaven, categorize_by_name
from materialx_db.db import insert_material, insert_variant

log = logging.getLogger(__name__)


def fetch_and_insert(conn: sqlite3.Connection) -> int:
    """Fetch PolyHaven material list and insert into DB. Returns count of materials."""
    loader = PolyHavenLoader()
    materialx_assets, all_assets, _ = loader.fetch_materialx_assets(max_items=None)

    if not materialx_assets:
        log.warning("PolyHaven returned no materials")
        return 0

    # Group materialx_assets by base asset id (strip ___resolution suffix)
    by_asset: dict[str, dict] = {}
    for key, data in materialx_assets.items():
        # key format: "asset_id___resolution"
        parts = key.split("___")
        asset_id = parts[0]
        resolution = parts[1] if len(parts) > 1 else "1k"
        by_asset.setdefault(asset_id, {})[resolution] = data

    count = 0
    for asset_id, resolutions in by_asset.items():
        mat_id = f"ph:{asset_id}"

        # Get categories from all_assets if available
        asset_info = all_assets.get(asset_id, {})
        categories = asset_info.get("categories", [])
        if categories:
            category = categorize_polyhaven(categories)
        else:
            category = categorize_by_name(asset_id)

        # Get thumbnail from any resolution entry
        thumbnail_url = None
        for res_data in resolutions.values():
            thumbnail_url = res_data.get("thumbnail_url")
            if thumbnail_url:
                break

        insert_material(
            conn,
            id=mat_id,
            source="polyhaven",
            name=asset_id,
            category=category,
            has_textures=True,
            thumbnail_url=thumbnail_url,
        )

        for resolution, res_data in resolutions.items():
            insert_variant(
                conn,
                material_id=mat_id,
                resolution=resolution,
                download_url=res_data.get("url"),
                download_meta={
                    "mtlx_url": res_data.get("url"),
                    "texture_urls": res_data.get("texture_files", {}),
                },
            )

        count += 1

    conn.commit()
    return count
