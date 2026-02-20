"""Fetch ambientCG catalog metadata into the materials DB."""

import json
import logging
import sqlite3

import MaterialX as mx
from materialxMaterials.ambientCGLoader import AmbientCGLoader

from materialx_lib.categories import categorize_by_name
from materialx_lib.db import insert_material, insert_variant

log = logging.getLogger(__name__)


def fetch_and_insert(conn: sqlite3.Connection) -> int:
    """Fetch ambientCG material list and insert into DB. Returns count of materials."""
    loader = AmbientCGLoader(mx, None)
    materials = loader.downloadMaterialsList()
    if not materials:
        log.warning("ambientCG returned no materials")
        return 0

    # Group by assetId
    by_asset: dict[str, list[dict]] = {}
    for item in materials:
        asset_id = item["assetId"]
        by_asset.setdefault(asset_id, []).append(item)

    count = 0
    for asset_id, variants in by_asset.items():
        mat_id = f"acg:{asset_id}"
        category = categorize_by_name(asset_id)
        thumbnail_url = f"https://acg-media.struffelproductions.com/file/acg-media/catalog/{asset_id}/{asset_id}_Preview.png"

        insert_material(
            conn,
            id=mat_id,
            source="ambientcg",
            name=asset_id,
            category=category,
            has_textures=True,
            thumbnail_url=thumbnail_url,
        )

        for v in variants:
            resolution = v.get("downloadAttribute", "unknown")
            insert_variant(
                conn,
                material_id=mat_id,
                resolution=resolution,
                download_url=v.get("downloadLink"),
                download_meta={"format": "zip_with_mtlx"},
                file_size=int(v["size"]) if v.get("size") else None,
            )

        count += 1

    conn.commit()
    return count
