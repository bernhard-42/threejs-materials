"""Shared types for source modules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


def normalize_name(name: str) -> str:
    """Folder-safe form of a material name: lowercase, non-alphanumeric runs
    collapsed to ``_``, leading/trailing ``_`` stripped. ``"Dark Bricks"`` →
    ``"dark_bricks"``."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass
class SourceResult:
    """Uniform return type for all source ``fetch()`` functions."""

    # One of these three must be set:
    mtlx_path: Path | None = None  # MaterialX sources (baked)
    gltf_path: Path | None = None  # glTF sources (parsed via _from_gltf)
    properties: dict | None = None  # Direct sources (no baking needed)
    # Metadata (always set):
    license: str = ""
    url: str = ""
    # Optional post-processing:
    overrides: dict = field(default_factory=dict)
    # Textures the source has but the .mtlx graph doesn't reference
    # (e.g. polyhaven's AO — standard_surface has no AO input).
    # Maps property name → file path on disk under the source's tex_dir.
    extra_textures: dict = field(default_factory=dict)
