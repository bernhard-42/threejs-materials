"""Shared types for source modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SourceResult:
    """Uniform return type for all source ``fetch()`` functions."""

    # One of these two must be set:
    mtlx_path: Path | None = None  # MaterialX sources
    properties: dict | None = None  # Direct sources (no baking needed)
    # Metadata (always set):
    license: str = ""
    url: str = ""
    # Optional post-processing:
    overrides: dict = field(default_factory=dict)
