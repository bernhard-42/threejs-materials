"""SQLite schema, creation, and insert helpers for the material catalog."""

import json
import sqlite3
from pathlib import Path

DB_DIR = Path.home() / ".materialx"
DB_PATH = DB_DIR / "materials.db"

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS materials (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    has_textures  INTEGER NOT NULL,
    shader_model  TEXT,
    thumbnail_url TEXT,
    tags          TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS material_variants (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id   TEXT NOT NULL REFERENCES materials(id),
    resolution    TEXT NOT NULL,
    download_url  TEXT,
    download_meta TEXT,
    file_size     INTEGER,
    UNIQUE(material_id, resolution)
);

CREATE INDEX IF NOT EXISTS idx_materials_source ON materials(source);
CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category);
CREATE INDEX IF NOT EXISTS idx_materials_has_textures ON materials(has_textures);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the SQLite database and return a connection."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """Create (or recreate) the schema."""
    conn.executescript(SCHEMA_SQL)


def drop_tables(conn: sqlite3.Connection) -> None:
    """Drop all tables so we can rebuild from scratch."""
    conn.executescript("""\
        DROP TABLE IF EXISTS material_variants;
        DROP TABLE IF EXISTS materials;
    """)


def insert_material(
    conn: sqlite3.Connection,
    *,
    id: str,
    source: str,
    name: str,
    category: str,
    has_textures: bool,
    shader_model: str | None = None,
    thumbnail_url: str | None = None,
    tags: list[str] | None = None,
) -> None:
    conn.execute(
        """\
        INSERT OR REPLACE INTO materials
            (id, source, name, category, has_textures, shader_model, thumbnail_url, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id,
            source,
            name,
            category,
            1 if has_textures else 0,
            shader_model,
            thumbnail_url,
            json.dumps(tags) if tags else None,
        ),
    )


def insert_variant(
    conn: sqlite3.Connection,
    *,
    material_id: str,
    resolution: str,
    download_url: str | None = None,
    download_meta: dict | None = None,
    file_size: int | None = None,
) -> None:
    conn.execute(
        """\
        INSERT OR REPLACE INTO material_variants
            (material_id, resolution, download_url, download_meta, file_size)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            material_id,
            resolution,
            download_url,
            json.dumps(download_meta) if download_meta else None,
            file_size,
        ),
    )
