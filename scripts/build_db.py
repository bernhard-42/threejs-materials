#!/usr/bin/env python
"""
Rebuild the MaterialX material catalog from scratch.

Usage:
    python scripts/build_db.py [--sources ambientcg,gpuopen,polyhaven,physicallybased]
"""

import argparse
import logging
import sys
import time

from materialx_db.db import DB_PATH, get_connection, create_tables, drop_tables
from materialx_db.sources import ambientcg, gpuopen, polyhaven, physicallybased

ALL_SOURCES = {
    "ambientcg": ambientcg,
    "gpuopen": gpuopen,
    "polyhaven": polyhaven,
    "physicallybased": physicallybased,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_db")


def main():
    parser = argparse.ArgumentParser(
        description="Build the MaterialX material catalog DB"
    )
    parser.add_argument(
        "--sources",
        default=",".join(ALL_SOURCES),
        help="Comma-separated list of sources to fetch (default: all)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help=f"Path to SQLite DB (default: {DB_PATH})",
    )
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",")]
    for s in sources:
        if s not in ALL_SOURCES:
            print(f"Unknown source: {s}. Valid: {', '.join(ALL_SOURCES)}")
            sys.exit(1)

    db_path = args.db if args.db else None
    conn = get_connection(db_path)

    log.info("Dropping and recreating tables...")
    drop_tables(conn)
    create_tables(conn)

    total = 0
    for source_name in sources:
        mod = ALL_SOURCES[source_name]
        log.info(f"Fetching {source_name}...")
        t0 = time.time()
        try:
            count = mod.fetch_and_insert(conn)
        except Exception:
            log.exception(f"Failed to fetch {source_name}")
            continue
        elapsed = time.time() - t0
        log.info(f"  {source_name}: {count} materials ({elapsed:.1f}s)")
        total += count

    # Print summary
    log.info(f"Total: {total} materials in {DB_PATH if not db_path else db_path}")

    cursor = conn.execute(
        "SELECT source, count(*) as cnt FROM materials GROUP BY source ORDER BY source"
    )
    for row in cursor:
        log.info(f"  {row['source']}: {row['cnt']}")

    variant_count = conn.execute("SELECT count(*) FROM material_variants").fetchone()[0]
    log.info(f"  Total variants: {variant_count}")

    conn.close()


if __name__ == "__main__":
    main()
