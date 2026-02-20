"""Fetch PhysicallyBased catalog metadata into the materials DB."""

import json
import logging
import sqlite3

import MaterialX as mx
from materialxMaterials.physicallyBasedMaterialX import PhysicallyBasedMaterialLoader

from materialx_lib.categories import categorize_physicallybased
from materialx_lib.db import insert_material, insert_variant

log = logging.getLogger(__name__)


def fetch_and_insert(conn: sqlite3.Connection) -> int:
    """Fetch PhysicallyBased material list and insert into DB. Returns count of materials."""
    loader = PhysicallyBasedMaterialLoader(mx, None)
    materials = loader.getMaterialsFromURL()
    if not materials:
        log.warning("PhysicallyBased returned no materials")
        return 0

    count = 0
    for mat in materials:
        name = mat.get("name", "")
        mat_id = f"pb:{name}"

        # Category from the material's category field
        categories = mat.get("category", [])
        if categories:
            category = categorize_physicallybased(categories[0])
        else:
            category = "other"

        # Reference image as thumbnail
        refs = mat.get("reference", [])
        thumbnail_url = refs[0] if refs else None

        tags = mat.get("tags", [])

        insert_material(
            conn,
            id=mat_id,
            source="physicallybased",
            name=name,
            category=category,
            has_textures=False,
            thumbnail_url=thumbnail_url,
            tags=tags if tags else None,
        )

        # Store full material data as the download_meta — no download needed
        insert_variant(
            conn,
            material_id=mat_id,
            resolution="parametric",
            download_meta=mat,
        )

        count += 1

    conn.commit()
    return count
