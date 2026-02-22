"""Public API: query the material catalog DB and lazy-download materials."""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from materialx_db.db import DB_PATH, get_connection, create_tables, drop_tables
from materialx_db.convert import convert_material

log = logging.getLogger(__name__)


@dataclass
class MaterialInfo:
    """Summary of a material in the catalog."""

    id: str
    source: str
    name: str
    category: str
    has_textures: bool
    resolutions: list[str] = field(default_factory=list)
    thumbnail_url: str | None = None


class MaterialLibrary:
    """
    Public API for the MaterialX material library.

    Usage::

        lib = MaterialLibrary()

        # Browse
        lib.list_categories()
        lib.list_materials(category="metal")

        # Staged resolution for GPUOpen (labels fetched on demand):
        resolutions = lib.get_material("gpuo:Copper_Brushed")
        # => ["1k 8b", "1k 16b", "2k 8b", "2k 16b", "4k 8b", "4k 16b"]

        mat = lib.get_material("gpuo:Copper_Brushed", resolution="2k 8b")
        # => { "id": ..., "params": ..., "textures": ... }

        # Sources with known resolutions work directly:
        mat = lib.get_material("acg:Plastic012A", resolution="1K-JPG")
        mat = lib.get_material("pb:Aluminum")  # parametric, no resolution needed
    """

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._conn = get_connection(self._db_path)
        print("Sources")
        print("- https://physicallybased.info/")
        print("- https://matlib.gpuopen.com/main/materials/all")
        print("- https://ambientcg.com/list?type=material")
        print("- https://polyhaven.com/textures")

    def close(self):
        self._conn.close()

    # --- Query methods ---

    def list_categories(self) -> list[str]:
        """Return sorted list of all categories present in the DB."""
        rows = self._conn.execute(
            "SELECT DISTINCT category FROM materials ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]

    def list_materials(
        self,
        *,
        category: str | None = None,
        has_textures: bool | None = None,
        source: str | None = None,
        name: str | None = None,
    ) -> list[MaterialInfo]:
        """
        Query materials with optional filters.
        ``name`` does case-insensitive substring matching.
        """
        clauses = []
        params: list = []

        if category is not None:
            clauses.append("m.category = ?")
            params.append(category)
        if has_textures is not None:
            clauses.append("m.has_textures = ?")
            params.append(1 if has_textures else 0)
        if source is not None:
            clauses.append("m.source = ?")
            params.append(source)
        if name is not None:
            clauses.append("m.name LIKE ?")
            params.append(f"%{name}%")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        rows = self._conn.execute(
            f"SELECT m.* FROM materials m{where} ORDER BY m.name",
            params,
        ).fetchall()

        return [self._to_material_info(r) for r in rows]

    def search(self, query: str) -> list[MaterialInfo]:
        """Search materials by name or tags (case-insensitive substring)."""
        pattern = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM materials WHERE name LIKE ? OR tags LIKE ? ORDER BY name",
            (pattern, pattern),
        ).fetchall()
        return [self._to_material_info(r) for r in rows]

    def _to_material_info(self, row: sqlite3.Row) -> MaterialInfo:
        resolutions = self._conn.execute(
            "SELECT resolution FROM material_variants WHERE material_id = ? ORDER BY resolution",
            (row["id"],),
        ).fetchall()
        return MaterialInfo(
            id=row["id"],
            source=row["source"],
            name=row["name"],
            category=row["category"],
            has_textures=bool(row["has_textures"]),
            resolutions=[r["resolution"] for r in resolutions],
            thumbnail_url=row["thumbnail_url"],
        )

    # --- Get material (lazy download + convert) ---

    def get_material(
        self, material_id: str, resolution: str | None = None
    ) -> dict | list[str]:
        """
        Get a material as a Three.js MeshPhysicalMaterial-compatible dict.

        **Staged resolution for GPUOpen materials:**

        GPUOpen packages don't carry resolution labels in the catalog.
        Call without ``resolution`` to fetch labels on demand::

            lib.get_material("gpuo:Copper_Brushed")
            # => ["1k 8b", "1k 16b", "2k 8b", "2k 16b", "4k 8b", "4k 16b"]

        Then call again with the chosen label::

            lib.get_material("gpuo:Copper_Brushed", resolution="2k 8b")

        If you already know the label string (e.g. from the GPUOpen web UI),
        skip straight to the second call.

        **Other sources** have human-readable resolutions stored in the DB
        (e.g. ``"1K-JPG"`` for ambientCG, ``"1k"`` for PolyHaven,
        ``"parametric"`` for PhysicallyBased). For single-variant materials
        (like PhysicallyBased), ``resolution`` can be omitted.

        Returns:
            list[str] — available resolution labels (when resolution is None
                        and multiple variants exist that need label resolution)
            dict     — material JSON with keys: id, name, source, category,
                        params, textures
        """
        mat_row = self._conn.execute(
            "SELECT * FROM materials WHERE id = ?", (material_id,)
        ).fetchone()
        if not mat_row:
            raise ValueError(f"Material not found: {material_id}")

        source = mat_row["source"]

        # GPUOpen: labels stored as pkg_0..pkg_N, need on-demand resolution
        if source == "gpuopen":
            return self._get_gpuopen_material(material_id, resolution)

        # Other sources: resolutions are already human-readable
        if resolution is None:
            variants = self._conn.execute(
                "SELECT resolution FROM material_variants WHERE material_id = ?",
                (material_id,),
            ).fetchall()
            resolutions = [v["resolution"] for v in variants]
            # Single variant (e.g. PhysicallyBased "parametric") → just load it
            if len(resolutions) == 1:
                resolution = resolutions[0]
            else:
                return resolutions

        json_path = convert_material(material_id, resolution, self._conn)
        return json.loads(json_path.read_text())

    def _get_gpuopen_material(
        self, material_id: str, resolution: str | None
    ) -> dict | list[str]:
        """Handle GPUOpen's staged resolution flow."""
        variants = self._conn.execute(
            "SELECT * FROM material_variants WHERE material_id = ? ORDER BY rowid",
            (material_id,),
        ).fetchall()

        if not variants:
            raise ValueError(f"No variants found for {material_id}")

        # Resolve labels: check if already resolved (label != pkg_N pattern)
        needs_label_fetch = any(v["resolution"].startswith("pkg_") for v in variants)

        if needs_label_fetch:
            label_map = self._fetch_and_cache_gpuopen_labels(material_id, variants)
        else:
            label_map = {v["resolution"]: v for v in variants}

        labels = list(label_map.keys())

        if resolution is None:
            return labels

        # Find the variant matching the requested label
        if resolution not in label_map:
            raise ValueError(
                f"Resolution '{resolution}' not found for {material_id}. "
                f"Available: {labels}"
            )

        variant = label_map[resolution]
        db_resolution = variant["resolution"]  # the pkg_N or updated label in DB

        json_path = convert_material(material_id, db_resolution, self._conn)
        return json.loads(json_path.read_text())

    def _fetch_and_cache_gpuopen_labels(
        self, material_id: str, variants: list[sqlite3.Row]
    ) -> dict[str, sqlite3.Row]:
        """Fetch package labels from GPUOpen API and update DB. Returns {label: row}."""
        from materialx_db.sources.gpuopen import fetch_package_labels

        pkg_uuids = []
        for v in variants:
            meta = json.loads(v["download_meta"]) if v["download_meta"] else {}
            pkg_uuids.append(meta.get("package_id", ""))

        labels_info = fetch_package_labels(pkg_uuids)

        # Update DB with real labels and re-read
        for v, pkg_uuid in zip(variants, pkg_uuids):
            label, size = labels_info.get(pkg_uuid, (v["resolution"], None))
            self._conn.execute(
                "UPDATE material_variants SET resolution = ?, file_size = ? WHERE id = ?",
                (label, size, v["id"]),
            )
        self._conn.commit()

        # Re-read updated variants
        updated = self._conn.execute(
            "SELECT * FROM material_variants WHERE material_id = ? ORDER BY rowid",
            (material_id,),
        ).fetchall()
        return {v["resolution"]: v for v in updated}

    # --- Rebuild DB ---

    def rebuild(
        self,
        sources: list[str] | None = None,
    ) -> dict[str, int]:
        """
        Rebuild the catalog DB from scratch.

        Returns dict of source_name -> material_count.
        """
        from materialx_db.sources import ambientcg, gpuopen, polyhaven, physicallybased

        all_sources = {
            "ambientcg": ambientcg,
            "gpuopen": gpuopen,
            "polyhaven": polyhaven,
            "physicallybased": physicallybased,
        }

        if sources is None:
            sources = list(all_sources.keys())

        drop_tables(self._conn)
        create_tables(self._conn)

        counts = {}
        for name in sources:
            mod = all_sources[name]
            counts[name] = mod.fetch_and_insert(self._conn)

        return counts
