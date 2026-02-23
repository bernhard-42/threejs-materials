"""Public API: load materials on demand with local JSON caching."""

import json
import logging
import re
import tempfile
from enum import Enum
from pathlib import Path

from materialx_db.convert import _process_mtlx, extract_materials, load_document_with_stdlib
from materialx_db.sources import ambientcg, gpuopen, polyhaven, physicallybased

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".materialx-cache"


class MaterialSource(Enum):
    """Available material sources."""

    ambientCG = "ambientcg"
    GPUOpen = "gpuopen"
    PolyHaven = "polyhaven"
    PhysicallyBased = "physicallybased"


_SOURCES = {
    MaterialSource.ambientCG: {
        "module": ambientcg,
        "url": "https://ambientcg.com/list?type=material",
    },
    MaterialSource.GPUOpen: {
        "module": gpuopen,
        "url": "https://matlib.gpuopen.com/main/materials/all",
    },
    MaterialSource.PolyHaven: {
        "module": polyhaven,
        "url": "https://polyhaven.com/textures",
    },
    MaterialSource.PhysicallyBased: {
        "module": physicallybased,
        "url": "https://physicallybased.info/",
    },
}

_B64_RE = re.compile(r"(data:[^;]+;base64,).{30,}")


def _resolve_source(source: MaterialSource | str) -> MaterialSource:
    """Accept a MaterialSource enum or a string and return the enum member."""
    if isinstance(source, MaterialSource):
        return source
    val = source.lower()
    for member in MaterialSource:
        if member.value == val or member.name.lower() == val:
            return member
    raise ValueError(
        f"Unknown source: '{source}'. Use one of: "
        f"{[m.name for m in MaterialSource]}"
    )


def _cache_path(source: str, name: str, actual_res: str | None) -> Path:
    """Build the cache file path for a material."""
    safe_name = name.lower().replace(" ", "_")
    if actual_res:
        safe_res = actual_res.lower().replace(" ", "_")
        filename = f"{source}_{safe_name}_{safe_res}.json"
    else:
        filename = f"{source}_{safe_name}.json"
    return CACHE_DIR / filename


class _SourceLoader:
    """Proxy providing .load() for a specific material source."""

    def __init__(self, source: MaterialSource, attr_name: str):
        self._source = source
        self._attr_name = attr_name

    def load(self, name: str, resolution: str = "1K") -> "Material":
        return Material._load(self._source, name, resolution)

    def __repr__(self):
        return f"Material.{self._attr_name}"


class Material:
    """A loaded PBR material with Three.js MeshPhysicalMaterial properties."""

    __slots__ = ("id", "name", "source", "url", "license", "properties")

    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.source: str = data["source"]
        self.url: str = data["url"]
        self.license: str = data["license"]
        self.properties: dict = data["properties"]

    @classmethod
    def _load(
        cls, source: MaterialSource | str, name: str, resolution: str = "1K"
    ) -> "Material":
        """Download, convert, and cache a material as Three.js MeshPhysicalMaterial JSON.

        Parameters
        ----------
        source : MaterialSource | str
            A ``MaterialSource`` enum member or its name/value as a string.
        name : str
            Material name/ID as shown on the source website.
        resolution : str
            Normalized resolution: ``"1K"``, ``"2K"``, ``"4K"``, ``"8K"``
            (case-insensitive). Defaults to ``"1K"``. Ignored for ``PhysicallyBased``.

        Returns
        -------
        Material
            Object with ``id``, ``name``, ``source``, ``url``, ``license``,
            ``properties`` attributes.
        """
        src_enum = _resolve_source(source)
        source_val = src_enum.value
        src_info = _SOURCES[src_enum]
        src_mod = src_info["module"]

        # Map resolution (physicallybased ignores it)
        if src_mod.RESOLUTION_MAP:
            res_key = resolution.upper()
            actual_res = src_mod.RESOLUTION_MAP.get(res_key)
            if actual_res is None:
                available = list(src_mod.RESOLUTION_MAP.keys())
                raise ValueError(
                    f"Resolution '{resolution}' not available for {src_enum.name}. "
                    f"Available: {available}"
                )
        else:
            actual_res = None

        label = f"{src_enum.name} / {name}"

        # Check cache
        cache_file = _cache_path(source_val, name, actual_res)
        if cache_file.exists():
            mat = cls(json.loads(cache_file.read_text()))
            print(f"{label}: loading from cache — License: {mat.license}")
            return mat

        # Download and convert
        print(f"{label}: downloading ...", end=" ", flush=True)
        if src_enum == MaterialSource.GPUOpen:
            with tempfile.TemporaryDirectory() as tmp:
                mtlx_path, mat_license, mat_url = src_mod.download(
                    name, actual_res, Path(tmp)
                )
                print("baking ...", end=" ", flush=True)
                properties, _ = _process_mtlx(mtlx_path)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                overrides = {}
                if src_mod.RESOLUTION_MAP:
                    mtlx_path = src_mod.download(name, actual_res, Path(tmp))
                else:
                    mtlx_path, overrides = src_mod.download(name, Path(tmp))
                print("baking ...", end=" ", flush=True)
                properties, _ = _process_mtlx(mtlx_path)
                # Apply property overrides (e.g. thin-film thickness range)
                for key, val in overrides.items():
                    if key in properties:
                        properties[key]["value"] = val
            mat_license = src_mod.LICENSE
            mat_url = src_mod.material_url(name)

        output = {
            "id": name,
            "name": name,
            "source": source_val,
            "url": mat_url,
            "license": mat_license,
            "properties": properties,
        }

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(output, indent=2))
        print(f"saving ... done — License: {mat_license}")

        return cls(output)

    @classmethod
    def list_sources(cls) -> None:
        """Print available material sources with clickable URLs."""
        width = max(len(src.name) for src in MaterialSource)
        print("Material sources:")
        for src in MaterialSource:
            url = _SOURCES[src]["url"]
            label = f"Material.{src.name}"
            print(f"  {label:<{width + 10}}  {url}")

    @classmethod
    def from_mtlx(cls, mtlx_file: str) -> "Material":
        """Convert a local .mtlx file to a Material.

        Texture paths in the .mtlx are resolved relative to the file's location.
        If the material references textures that don't exist on disk, a
        ``FileNotFoundError`` is raised.
        """
        mtlx_path = Path(mtlx_file).resolve()
        if not mtlx_path.exists():
            raise FileNotFoundError(f"File not found: {mtlx_path}")

        # Validate that referenced texture files exist
        doc, _ = load_document_with_stdlib(mtlx_path)
        orig_mats = extract_materials(doc)
        if orig_mats:
            base_dir = mtlx_path.parent
            missing = [
                tex_info["file"]
                for mat in orig_mats
                for tex_info in mat["textures"].values()
                if tex_info.get("file") and not (base_dir / tex_info["file"]).exists()
            ]
            if missing:
                raise FileNotFoundError(
                    f"Textures not found (relative to {base_dir}): {', '.join(missing)}"
                )

        baked_mtlx = mtlx_path.parent / "material.baked.mtlx"
        try:
            properties, _ = _process_mtlx(mtlx_path)
        finally:
            baked_mtlx.unlink(missing_ok=True)

        name = mtlx_path.stem
        return cls({
            "id": name,
            "name": name,
            "source": "local",
            "url": "",
            "license": "",
            "properties": properties,
        })

    def to_dict(self) -> dict:
        """Return the full material as a plain dict (including complete base64 textures)."""
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "url": self.url,
            "license": self.license,
            "properties": self.properties,
        }

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string. Keyword args are passed to ``json.dumps``."""
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    def __repr__(self) -> str:
        lines = [
            f"Material(name={self.name!r}, source={self.source!r}, "
            f"license={self.license!r})"
        ]
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


# Source accessors — set after class body since they reference Material
Material.ambientcg = _SourceLoader(MaterialSource.ambientCG, "ambientcg")
Material.gpuopen = _SourceLoader(MaterialSource.GPUOpen, "gpuopen")
Material.polyhaven = _SourceLoader(MaterialSource.PolyHaven, "polyhaven")
Material.physicallybased = _SourceLoader(MaterialSource.PhysicallyBased, "physicallybased")
