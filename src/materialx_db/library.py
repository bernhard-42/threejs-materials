"""Public API: load materials on demand with local JSON caching."""

import json
import logging
import re
import tempfile
from pathlib import Path

from materialx_db.convert import _process_mtlx
from materialx_db.sources import ambientcg, gpuopen, polyhaven, physicallybased

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".materialx-cache"

_SOURCES = {
    "ambientcg": {
        "module": ambientcg,
        "url": "https://ambientcg.com/list?type=material",
    },
    "gpuopen": {
        "module": gpuopen,
        "url": "https://matlib.gpuopen.com/main/materials/all",
    },
    "polyhaven": {
        "module": polyhaven,
        "url": "https://polyhaven.com/textures",
    },
    "physicallybased": {
        "module": physicallybased,
        "url": "https://physicallybased.info/",
    },
}

_B64_RE = re.compile(r"(data:[^;]+;base64,).{30,}")


class Material:
    """A loaded PBR material with Three.js MeshPhysicalMaterial properties."""

    __slots__ = ("id", "name", "source", "properties")

    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.source: str = data["source"]
        self.properties: dict = data["properties"]

    def to_dict(self) -> dict:
        """Return the full material as a plain dict (including complete base64 textures)."""
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "properties": self.properties,
        }

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string. Keyword args are passed to ``json.dumps``."""
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    def __repr__(self) -> str:
        lines = [f"Material(name={self.name!r}, source={self.source!r})"]
        for key, prop in self.properties.items():
            parts = []
            if "value" in prop:
                parts.append(f"value={prop['value']}")
            if "texture" in prop:
                short = _B64_RE.sub(r"\g<1>...", prop["texture"])
                parts.append(f"texture={short!r}")
            lines.append(f"  {key}: {', '.join(parts)}")
        return "\n".join(lines)

    def __getitem__(self, key: str):
        return self.to_dict()[key]

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()



def list_sources() -> dict:
    """Print available material sources with clickable URLs.

    Returns a dict of ``{source_name: url}``.
    """
    info = {}
    width = max(len(name) for name in _SOURCES)
    print("Material sources:")
    for name, src in _SOURCES.items():
        url = src["url"]
        print(f"  {name:<{width}}  {url}")
        info[name] = url
    return info


def _cache_path(source: str, name: str, actual_res: str | None) -> Path:
    """Build the cache file path for a material."""
    safe_name = name.lower().replace(" ", "_")
    if actual_res:
        safe_res = actual_res.lower().replace(" ", "_")
        filename = f"{source}_{safe_name}_{safe_res}.json"
    else:
        filename = f"{source}_{safe_name}.json"
    return CACHE_DIR / filename


def load_material(
    source: str, name: str, resolution: str = "1K"
) -> Material:
    """Download, convert, and cache a material as Three.js MeshPhysicalMaterial JSON.

    Parameters
    ----------
    source : str
        One of ``"ambientcg"``, ``"gpuopen"``, ``"polyhaven"``, ``"physicallybased"``.
    name : str
        Material name/ID as shown on the source website.
    resolution : str
        Normalized resolution: ``"1K"``, ``"2K"``, ``"4K"``, ``"8K"``
        (case-insensitive). Defaults to ``"1K"``. Ignored for ``physicallybased``.

    Returns
    -------
    Material
        Object with ``id``, ``name``, ``source``, ``properties`` attributes.
    """
    source = source.lower()
    if source not in _SOURCES:
        raise ValueError(
            f"Unknown source: '{source}'. Use one of: {list(_SOURCES)}"
        )

    src_mod = _SOURCES[source]["module"]

    # Map resolution (physicallybased ignores it)
    if src_mod.RESOLUTION_MAP:
        res_key = resolution.upper()
        actual_res = src_mod.RESOLUTION_MAP.get(res_key)
        if actual_res is None:
            available = list(src_mod.RESOLUTION_MAP.keys())
            raise ValueError(
                f"Resolution '{resolution}' not available for {source}. "
                f"Available: {available}"
            )
    else:
        actual_res = None

    # Check cache
    cache_path = _cache_path(source, name, actual_res)
    if cache_path.exists():
        return Material(json.loads(cache_path.read_text()))

    # PhysicallyBased: parametric — returns properties directly (no .mtlx)
    if source == "physicallybased":
        properties = src_mod.download(name)
    else:
        # Download to temp dir, run pipeline
        with tempfile.TemporaryDirectory() as tmp:
            mtlx_path = src_mod.download(name, actual_res, Path(tmp))
            properties, _ = _process_mtlx(mtlx_path)

    output = {
        "id": name,
        "name": name,
        "source": source,
        "properties": properties,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(output, indent=2))
    return Material(output)
