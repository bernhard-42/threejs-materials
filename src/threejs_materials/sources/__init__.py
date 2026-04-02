from __future__ import annotations

import base64
import json
import shutil
import tempfile
from pathlib import Path
from threejs_materials.convert import _process_mtlx
from threejs_materials.sources import ambientcg, gpuopen, polyhaven, physicallybased
from threejs_materials.sources.common import SourceResult
from threejs_materials.utils import _is_data_uri

CACHE_DIR = Path.home() / ".materialx-cache"

_SOURCE_MODULES = {
    "ambientcg": ambientcg,
    "gpuopen": gpuopen,
    "polyhaven": polyhaven,
    "physicallybased": physicallybased,
}


def _cache_path(source: str, name: str, resolution: str | None) -> Path:
    """Build the cache file path for a material."""
    safe_name = name.lower().replace(" ", "_")
    if resolution:
        safe_res = resolution.lower().replace(" ", "_")
        filename = f"{source}_{safe_name}_{safe_res}.json"
    else:
        filename = f"{source}_{safe_name}.json"
    return CACHE_DIR / filename


def _collect_textures(
    properties: dict, tex_dir: Path | None, cache_tex_dir: Path
) -> None:
    """Copy texture files into *cache_tex_dir* and rewrite paths in *properties*.

    Texture references in *properties* are either file paths relative to
    *tex_dir* (new format from ``to_threejs_physical``) or base64 data URIs
    (old format / non-baked sources).  File-path textures are copied to
    *cache_tex_dir* and the references updated to just the filename.
    Data-URI textures are decoded and written as files.
    """
    has_textures = False
    for prop_name, prop in properties.items():
        if isinstance(prop, dict) and "texture" in prop:
            tex_ref = prop["texture"]
            if _is_data_uri(tex_ref):
                # Decode data URI and write to file
                has_textures = True
                if not cache_tex_dir.exists():
                    cache_tex_dir.mkdir(parents=True)
                # Determine extension from MIME type
                header, b64 = tex_ref.split(",", 1)
                mime = header.split(":")[1].split(";")[0]
                ext = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                }.get(mime, ".png")
                fname = prop_name + ext
                dst = cache_tex_dir / fname
                dst.write_bytes(base64.b64decode(b64))
                prop["texture"] = fname
            elif tex_dir is not None:
                # File path relative to tex_dir — copy to cache
                src = tex_dir / tex_ref
                if src.exists():
                    has_textures = True
                    if not cache_tex_dir.exists():
                        cache_tex_dir.mkdir(parents=True)
                    fname = prop_name + src.suffix
                    dst = cache_tex_dir / fname
                    shutil.copy2(src, dst)
                    prop["texture"] = fname

    if not has_textures and cache_tex_dir.exists():
        # Clean up empty directory
        shutil.rmtree(cache_tex_dir, ignore_errors=True)


class _SourceLoader:
    """Download, bake, and cache materials from an online source."""

    def __init__(self, source_name: str):
        self._source = source_name

    @property
    def _module(self):
        return _SOURCE_MODULES[self._source]

    def load(self, name: str, resolution: str = "1K") -> dict:
        """Download, convert, cache, and return raw material data dict.

        Parameters
        ----------
        name : str
            Material name/ID as shown on the source website.
        resolution : str
            ``"1K"``, ``"2K"``, ``"4K"``, or ``"8K"`` (case-insensitive).
            Defaults to ``"1K"``. Ignored for PhysicallyBased.
        """
        label = f"{self._source} / {name}"
        res_key = resolution.upper()

        cache_file = _cache_path(self._source, name, res_key)
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            # Resolve relative _texture_dir against JSON location
            td = data.get("_texture_dir")
            if td is not None:
                data["_texture_dir"] = str((cache_file.parent / td).resolve())
            print(f"{label}: loading from cache — License: {data.get('license', '')}")
            return data

        print(f"{label}: downloading ...", end=" ", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            result = self._module.fetch(name, res_key, Path(tmp))
            if result.mtlx_path:
                print("baking ...", end=" ", flush=True)
                properties, _, tex_dir = _process_mtlx(result.mtlx_path)
            else:
                properties = result.properties
                tex_dir = None
            for key, v in result.overrides.items():
                if key in properties:
                    properties[key]["value"] = v

            # Copy texture files to persistent cache directory
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_tex_dir = cache_file.with_suffix("")  # strip .json
            _collect_textures(properties, tex_dir, cache_tex_dir)

        output = {
            "id": name,
            "name": name,
            "source": self._source,
            "url": result.url,
            "license": result.license,
            "properties": properties,
        }
        # Store relative path in JSON, absolute in runtime Material
        if cache_tex_dir.exists():
            output["_texture_dir"] = cache_tex_dir.name  # relative for JSON

        cache_file.write_text(json.dumps(output, indent=2))
        print(f"saving ... done — License: {result.license}")

        # Use absolute path for the runtime object
        if cache_tex_dir.exists():
            output["_texture_dir"] = str(cache_tex_dir)
        return output

    def __repr__(self):
        return f"_SourceLoader({self._source!r})"


ambientcg_loader = _SourceLoader("ambientcg")
gpuopen_loader = _SourceLoader("gpuopen")
polyhaven_loader = _SourceLoader("polyhaven")
physicallybased_loader = _SourceLoader("physicallybased")

_ALL_LOADERS = [
    ambientcg_loader,
    gpuopen_loader,
    polyhaven_loader,
    physicallybased_loader,
]


def _load_gpuopen(name: str, resolution: str = "1K") -> dict:
    return gpuopen_loader.load(name, resolution)


def _load_ambientcg(name: str, resolution: str = "1K") -> dict:
    return ambientcg_loader.load(name, resolution)


def _load_polyhaven(name: str, resolution: str = "1K") -> dict:
    return polyhaven_loader.load(name, resolution)


def _load_physicallybased(name: str, resolution: str = "1K") -> dict:
    return physicallybased_loader.load(name, resolution)


def list_sources() -> None:
    """Print available material sources with clickable URLs."""
    width = max(len(l._source) for l in _ALL_LOADERS)
    print("Material sources:")
    for loader in _ALL_LOADERS:
        label = f"load_{loader._source}"
        url = loader._module.BROWSE_URL
        print(f"  {label:<{width + 6}}  {url}")


def list_cache() -> list[tuple[str, str]]:
    """List cached materials.

    Returns a sorted list of ``(source, name)`` tuples.

    Example::

        list_cache()
        # [('ambientcg', 'Metal 009'), ('gpuopen', 'Car Paint'), ...]
    """
    if not CACHE_DIR.exists():
        return []
    result = []
    for f in sorted(CACHE_DIR.iterdir()):
        if not f.is_file() or f.suffix != ".json":
            continue
        data = json.loads(f.read_text())
        source = data.get("source", "?")
        name = data.get("name", f.stem)
        result.append((source, name))
    return result


def clear_cache(name: str | None = None, source: str | None = None) -> int:
    """Delete cached material files.

    Parameters
    ----------
    name : str, optional
        Only clear caches whose filename contains this name (case-insensitive).
    source : str, optional
        Only clear caches whose filename starts with this source prefix.

    Returns
    -------
    int
        Number of files deleted.
    """
    if not CACHE_DIR.exists():
        return 0
    if name is None and source is None:
        count = sum(1 for f in CACHE_DIR.iterdir() if f.is_file())
        shutil.rmtree(CACHE_DIR)
        return count
    count = 0
    for f in list(CACHE_DIR.iterdir()):
        if not f.is_file() or f.suffix != ".json":
            continue
        fname = f.name.lower()
        if source and not fname.startswith(source.lower() + "_"):
            continue
        if name and name.lower().replace(" ", "_") not in fname:
            continue
        f.unlink()
        # Also remove companion texture directory
        tex_dir = f.with_suffix("")
        if tex_dir.is_dir():
            shutil.rmtree(tex_dir)
        count += 1
    return count
