"""Public API: PbrProperties dataclass with all factory and instance methods."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import overload

from pygltflib import GLTF2

from threejs_materials.convert import (
    _process_mtlx,
    extract_materials,
    load_document_with_stdlib,
)
from threejs_materials.gltf import (
    _from_gltf,
    to_gltf as _to_gltf,
    save_gltf as _save_gltf,
)
from threejs_materials.models import PbrMaps, PbrValues
from threejs_materials.sources import ambientcg, gpuopen, polyhaven
from threejs_materials.utils import (
    _abbreviate_textures,
    _is_data_uri,
    _resolve_to_data_uri,
    _linear_average_texture,
    _linear_to_srgb,
    _normalize_color,
    _normalize_srgb_color,
    _srgb_to_linear,
)

# Color-typed override kwargs split by storage convention.
# ``color`` is sRGB-stored (matches three-cad-viewer's setRGB(SRGBColorSpace));
# the others are linear-stored (glTF *Factor spec; Three.js bare-color).
_LINEAR_COLOR_OVERRIDE_KEYS = (
    "emissive",
    "sheen_color",
    "specular_color",
    "attenuation_color",
)


def _normalize_color_overrides(overrides: dict) -> dict:
    """Return *overrides* with color values normalized to per-field convention.

    - ``color`` → sRGB byte ratios; alpha component lifts to ``opacity``
      (explicit ``opacity`` in *overrides* wins).
    - ``emissive`` / ``sheen_color`` / ``specular_color`` /
      ``attenuation_color`` → linear RGB.

    Mutates a copy; the input dict is left untouched.
    """
    out = dict(overrides)
    color_val = out.get("color")
    if color_val is not None:
        rgb, alpha = _normalize_srgb_color(color_val)
        out["color"] = list(rgb)
        if alpha is not None and out.get("opacity") is None:
            out["opacity"] = alpha
    for key in _LINEAR_COLOR_OVERRIDE_KEYS:
        val = out.get(key)
        if val is None:
            continue
        rgb, _ = _normalize_color(val)
        out[key] = list(rgb)
    return out

log = logging.getLogger(__name__)


def _hash_override(parent_id: str, overrides: dict) -> str:
    """Compute a stable 8-hex-char fingerprint for an override call.

    The hash input is ``(parent_id, overrides)`` so chained overrides
    naturally cascade: the resulting id is unique across the chain history
    even when the same override kwargs are applied at different points in
    a chain. Deterministic across Python sessions (unlike ``hash()``).
    """
    payload = json.dumps(
        [parent_id, sorted(overrides.items())],
        sort_keys=True,
        default=list,
    ).encode()
    return hashlib.blake2b(payload, digest_size=4).hexdigest()


def _dump_nested(obj, lines, indent=2):
    """Recursively format a nested dict/list for dump output."""
    prefix = " " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith("data:"):
                lines.append(f"{prefix}{k}: 'data:...;base64,...'")
            elif isinstance(v, dict):
                lines.append(f"{prefix}{k}:")
                _dump_nested(v, lines, indent + 2)
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                lines.append(f"{prefix}{k}:")
                for i, item in enumerate(v):
                    lines.append(f"{prefix}  [{i}]:")
                    _dump_nested(item, lines, indent + 4)
            else:
                lines.append(f"{prefix}{k}: {v}")


@dataclass
class PbrProperties:
    """A PBR material with metadata, scalar values, and texture maps."""

    id: str
    name: str
    source: str
    url: str
    license: str
    values: PbrValues = field(default_factory=PbrValues)
    maps: PbrMaps = field(default_factory=PbrMaps)
    texture_repeat: tuple | None = None
    texture_rotation: float | None = None
    """Texture rotation in **degrees**, counterclockwise. ``None`` means no rotation."""
    normalize_uvs: bool = True
    maps_dir: Path | None = field(default=None, repr=False)

    # -------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> PbrProperties:
        """Build from a raw data dict (as stored in cache JSON or returned by loaders).

        **No normalization** is applied — callers populating ``data`` directly
        are responsible for matching the per-field color-space convention
        documented on :class:`PbrValues` (``values.color`` sRGB; ``emissive``,
        ``sheen_color``, ``specular_color``, ``attenuation_color`` linear).
        """
        td = data.get("maps_dir")
        return cls(
            id=data["id"],
            name=data["name"],
            source=data["source"],
            url=data.get("url", ""),
            license=data.get("license", ""),
            values=PbrValues.from_dict(data.get("values", {})),
            maps=PbrMaps.from_dict(data.get("textures", {})),
            texture_repeat=data.get("texture_repeat"),
            texture_rotation=data.get("texture_rotation"),
            normalize_uvs=data.get("normalize_uvs", True),
            maps_dir=Path(td) if td is not None else None,
        )

    @classmethod
    def from_pymat(
        cls,
        pbr: dict,
        name: str = "Material",
        id: str = "Material",
        source: str = "unknown",
        normalize_uvs: bool = True,
        texture_scale: tuple[float, float] = (1, 1),
        overrides: dict | None = None,
    ) -> PbrProperties:
        """Build a PbrProperties from a Three.js-style PBR dict.

        ``pbr["color"]`` accepts an int (hex), CSS hex string, CSS name,
        or numeric 3/4-tuple. Numeric tuples are interpreted as **sRGB
        byte ratios** (matching build123d's ``Color`` class output and the
        downstream ``setRGB(SRGBColorSpace)`` consumption in
        three-cad-viewer). A 4-tuple alpha lifts to ``pbr["opacity"]``
        unless that key is also present (which wins).
        """
        if texture_scale == 0:
            raise ValueError("texture_scale needs to be > 0")

        color = pbr.get("color")
        if isinstance(color, int):
            color = f"#{color:06x}"

        color_alpha = None
        if color is not None:
            # values.color is sRGB-stored (matches three-cad-viewer's
            # setRGB(SRGBColorSpace) consumption). build123d emits sRGB byte
            # ratios; hex/named strings are sRGB by definition.
            rgb, color_alpha = _normalize_srgb_color(color)
            color = list(rgb)

        opacity = pbr.get("opacity")
        if opacity is None and color_alpha is not None:
            opacity = color_alpha

        values = {
            "metalness": pbr.get("metalness"),
            "roughness": pbr.get("roughness"),
            "color": color,
            "ior": pbr.get("ior"),
            "transmission": pbr.get("transmission"),
            "clearcoat": pbr.get("clearcoat"),
            "emissive": pbr.get("emissive"),
            "opacity": opacity,
            "dispersion": pbr.get("dispersion"),
            "specular_color": pbr.get("specular_color"),
            "specular_intensity": pbr.get("specular_intensity"),
        }

        if overrides:
            ovr = dict(overrides)
            if "color" in ovr and isinstance(ovr["color"], int):
                ovr["color"] = f"#{ovr['color']:06x}"
            values.update(_normalize_color_overrides(ovr))

        new_dict = {
            "name": name,
            "id": id,
            "source": source,
            "texture_repeat": [1 / s for s in texture_scale],
            "normalize_uvs": normalize_uvs,
            "values": values,
            "textures": {
                "color": pbr.get("map"),
                "normal": pbr.get("normalMap"),
                "roughness": pbr.get("roughnessMap"),
                "metalness": pbr.get("metalnessMap"),
                "ao": pbr.get("aoMap"),
                "displacement": pbr.get("displacementMap"),
                "emissive": pbr.get("emissiveMap"),
                "opacity": pbr.get("opacityMap"),
            },
        }
        return cls.from_dict(new_dict)

    @overload
    @classmethod
    def from_gltf(cls, gltf: GLTF2, index: None = None) -> dict[str, PbrProperties]: ...
    @overload
    @classmethod
    def from_gltf(cls, gltf: GLTF2, index: int) -> PbrProperties: ...

    @classmethod
    def from_gltf(
        cls,
        gltf: GLTF2,
        index: int | None = None,
    ) -> dict[str, PbrProperties] | PbrProperties:
        """Import materials from a ``pygltflib.GLTF2`` object.

        When *index* is ``None`` (default), returns a dict mapping
        material names to PbrProperties objects.  When *index* is given,
        returns a single PbrProperties directly.
        """
        result = _from_gltf(gltf, index=index)
        if isinstance(result, dict) and not any(k in result for k in ("id", "name")):
            return {name: cls.from_dict(data) for name, data in result.items()}
        return cls.from_dict(result)

    @overload
    @classmethod
    def load_gltf(cls, gltf_file: str, index: None = None) -> dict[str, PbrProperties]: ...
    @overload
    @classmethod
    def load_gltf(cls, gltf_file: str, index: int) -> PbrProperties: ...

    @classmethod
    def load_gltf(
        cls, gltf_file: str, index: int | None = None
    ) -> dict[str, PbrProperties] | PbrProperties:
        """Import materials from a ``.gltf`` or ``.glb`` file on disk."""
        gltf_path = Path(gltf_file).resolve()
        if not gltf_path.exists():
            raise FileNotFoundError(f"File not found: {gltf_path}")
        return cls.from_gltf(GLTF2.load(str(gltf_path)), index=index)

    @classmethod
    def from_mtlx(cls, mtlx_file: str, resolution: str = "1K") -> PbrProperties:
        """Convert a local .mtlx file to PbrProperties.

        ``resolution`` controls the baker's output texture dimensions.
        Accepts ``"1K"`` (default), ``"2K"``, ``"4K"``, or ``"8K"``.
        """
        mtlx_path = Path(mtlx_file).resolve()
        if not mtlx_path.exists():
            raise FileNotFoundError(f"File not found: {mtlx_path}")

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
            properties, _, tex_dir = _process_mtlx(mtlx_path, resolution=resolution)
        finally:
            baked_mtlx.unlink(missing_ok=True)

        name = mtlx_path.stem
        return cls.from_dict({
            "id": name,
            "name": name,
            "source": "local",
            "url": "",
            "license": "",
            "properties": properties,
            "maps_dir": str(tex_dir),
        })

    @classmethod
    def from_gpuopen(cls, name: str, resolution: str = "1K") -> PbrProperties:
        """Download, convert, and cache a GPUOpen material."""
        from threejs_materials.sources import _SOURCE_LOADERS

        return cls.from_dict(_SOURCE_LOADERS["gpuopen"].load(name, resolution))

    @classmethod
    def from_ambientcg(cls, name: str, resolution: str = "1K") -> PbrProperties:
        """Download, convert, and cache an ambientCG material."""
        from threejs_materials.sources import _SOURCE_LOADERS

        return cls.from_dict(_SOURCE_LOADERS["ambientcg"].load(name, resolution))

    @classmethod
    def from_polyhaven(cls, name: str, resolution: str = "1K") -> PbrProperties:
        """Download, convert, and cache a PolyHaven material."""
        from threejs_materials.sources import _SOURCE_LOADERS

        return cls.from_dict(_SOURCE_LOADERS["polyhaven"].load(name, resolution))

    @classmethod
    def download_gpuopen(cls, name: str, dest: str = ".", resolution: str = "1K") -> None:
        """Download and unzip a GPUOpen MaterialX (.mtlx + textures), hierarchy
        unchanged, into ``<dest>/<normalized_name>/``."""
        gpuopen.download(name, resolution, Path(dest))

    @classmethod
    def download_ambientcg(cls, name: str, dest: str = ".", resolution: str = "1K") -> None:
        """Download and unzip an ambientCG MaterialX (.mtlx + textures), hierarchy
        unchanged, into ``<dest>/<normalized_name>/``."""
        ambientcg.download(name, resolution, Path(dest))

    @classmethod
    def download_polyhaven(cls, name: str, dest: str = ".", resolution: str = "1K") -> None:
        """Download a PolyHaven MaterialX (.mtlx + textures) into
        ``<dest>/<normalized_name>/``, preserving the .mtlx's include paths."""
        polyhaven.download(name, resolution, Path(dest))

    @classmethod
    def from_physicallybased(cls, name: str, resolution: str = "1K") -> PbrProperties:
        """Download, convert, and cache a PhysicallyBased material."""
        from threejs_materials.sources import _SOURCE_LOADERS

        return cls.from_dict(_SOURCE_LOADERS["physicallybased"].load(name, resolution))

    @classmethod
    def create(
        cls,
        id: str,
        *,
        color=None,
        metalness: float | None = None,
        roughness: float | None = None,
        ior: float | None = None,
        transmission: float | None = None,
        opacity: float | None = None,
        transparent: bool | None = None,
        alpha_test: float | None = None,
        emissive: tuple | list | None = None,
        emissive_intensity: float | None = None,
        clearcoat: float | None = None,
        clearcoat_roughness: float | None = None,
        sheen: float | None = None,
        sheen_color: tuple | list | None = None,
        sheen_roughness: float | None = None,
        anisotropy: float | None = None,
        anisotropy_rotation: float | None = None,
        specular_intensity: float | None = None,
        specular_color: tuple | list | None = None,
        attenuation_color: tuple | list | None = None,
        attenuation_distance: float | None = None,
        thickness: float | None = None,
        iridescence: float | None = None,
        iridescence_ior: float | None = None,
        iridescence_thickness_range: tuple | list | None = None,
        dispersion: float | None = None,
        normal_scale: tuple | list | None = None,
        displacement_scale: float | None = None,
        side: int | None = None,
        # --- Texture maps ---
        color_map: str | None = None,
        metalness_map: str | None = None,
        roughness_map: str | None = None,
        normal_map: str | None = None,
        emissive_map: str | None = None,
        ao_map: str | None = None,
        opacity_map: str | None = None,
        clearcoat_map: str | None = None,
        clearcoat_roughness_map: str | None = None,
        clearcoat_normal_map: str | None = None,
        transmission_map: str | None = None,
        sheen_color_map: str | None = None,
        sheen_roughness_map: str | None = None,
        anisotropy_map: str | None = None,
        iridescence_map: str | None = None,
        specular_intensity_map: str | None = None,
        specular_color_map: str | None = None,
        thickness_map: str | None = None,
        displacement_map: str | None = None,
    ) -> PbrProperties:
        """Create PbrProperties from explicit PBR values and texture paths.

        This is a pure passthrough wrapper: every kwarg defaults to ``None``
        and is emitted to the resulting ``PbrValues`` / ``PbrMaps`` only when
        the caller explicitly provides it. No fallbacks, no auto-enables, no
        silent multiplications. When both a scalar and its paired map are
        provided, Three.js / glTF multiply them per spec.

        Color-space convention (see :class:`PbrValues`):

        - ``color`` is sRGB-stored; numeric tuples are interpreted as sRGB.
          A 4-tuple (or ``#rrggbbaa``) lifts the alpha into ``opacity``
          unless ``opacity=`` is passed explicitly.
        - ``emissive`` / ``sheen_color`` / ``specular_color`` /
          ``attenuation_color`` are linear-stored; numeric tuples are
          interpreted as linear. Strings are sRGB and get gamma-decoded.

        If a caller needs Three.js's own defaults (e.g. white color when a
        color_map is given without an explicit color), simply omit the
        scalar — the value serializes as absent and Three.js uses its
        built-in default at render time.
        """
        texture_dirs: list[Path] = []

        def _resolve_texture(tex: str | None) -> str | None:
            if tex is None:
                return None
            if tex.startswith("data:"):
                return tex
            p = Path(tex).resolve()
            if p.exists():
                texture_dirs.append(p.parent)
                return p.name
            raise FileNotFoundError(f"Texture file not found: {tex}")

        values: dict = {}
        if color is not None:
            # color → sRGB-stored (Three.js setRGB(SRGBColorSpace))
            rgb, color_alpha = _normalize_srgb_color(color)
            values["color"] = list(rgb)
            if color_alpha is not None and opacity is None:
                opacity = color_alpha
        if metalness is not None:
            values["metalness"] = metalness
        if roughness is not None:
            values["roughness"] = roughness
        if ior is not None:
            values["ior"] = ior
        if transmission is not None:
            values["transmission"] = transmission
        if opacity is not None:
            values["opacity"] = opacity
        if transparent is not None:
            values["transparent"] = transparent
        if alpha_test is not None:
            values["alphaTest"] = alpha_test
        if emissive is not None:
            values["emissive"] = list(_normalize_color(emissive)[0])
        if emissive_intensity is not None:
            values["emissiveIntensity"] = emissive_intensity
        if clearcoat is not None:
            values["clearcoat"] = clearcoat
        if clearcoat_roughness is not None:
            values["clearcoatRoughness"] = clearcoat_roughness
        if sheen is not None:
            values["sheen"] = sheen
        if sheen_color is not None:
            values["sheenColor"] = list(_normalize_color(sheen_color)[0])
        if sheen_roughness is not None:
            values["sheenRoughness"] = sheen_roughness
        if anisotropy is not None:
            values["anisotropy"] = anisotropy
        if anisotropy_rotation is not None:
            values["anisotropyRotation"] = anisotropy_rotation
        if specular_intensity is not None:
            values["specularIntensity"] = specular_intensity
        if specular_color is not None:
            values["specularColor"] = list(_normalize_color(specular_color)[0])
        if attenuation_color is not None:
            values["attenuationColor"] = list(_normalize_color(attenuation_color)[0])
        if attenuation_distance is not None:
            values["attenuationDistance"] = attenuation_distance
        if thickness is not None:
            values["thickness"] = thickness
        if iridescence is not None:
            values["iridescence"] = iridescence
        if iridescence_ior is not None:
            values["iridescenceIOR"] = iridescence_ior
        if iridescence_thickness_range is not None:
            values["iridescenceThicknessRange"] = list(iridescence_thickness_range)
        if dispersion is not None:
            values["dispersion"] = dispersion
        if normal_scale is not None:
            values["normalScale"] = list(normal_scale)
        if displacement_scale is not None:
            values["displacementScale"] = displacement_scale
        if side is not None:
            values["side"] = side

        tex_inputs = {
            "color": color_map,
            "metalness": metalness_map,
            "roughness": roughness_map,
            "normal": normal_map,
            "emissive": emissive_map,
            "ao": ao_map,
            "opacity": opacity_map,
            "clearcoat": clearcoat_map,
            "clearcoat_roughness": clearcoat_roughness_map,
            "clearcoat_normal": clearcoat_normal_map,
            "transmission": transmission_map,
            "sheen_color": sheen_color_map,
            "sheen_roughness": sheen_roughness_map,
            "anisotropy": anisotropy_map,
            "iridescence": iridescence_map,
            "specular_intensity": specular_intensity_map,
            "specular_color": specular_color_map,
            "thickness": thickness_map,
            "displacement": displacement_map,
        }
        textures: dict = {}
        for field_name, tex_path in tex_inputs.items():
            uri = _resolve_texture(tex_path)
            if uri:
                textures[field_name] = uri

        maps_dir = None
        if texture_dirs:
            common = texture_dirs[0]
            if not all(d == common for d in texture_dirs):
                raise ValueError("All texture files must be in the same directory")
            maps_dir = common

        return cls.from_dict({
            "id": id,
            "name": id,
            "source": "custom",
            "url": "",
            "license": "",
            "values": values,
            "textures": textures,
            "maps_dir": str(maps_dir) if maps_dir is not None else None,
        })

    # -------------------------------------------------------------------
    # Transforms
    # -------------------------------------------------------------------

    def override(
        self,
        *,
        color=None,
        roughness=None,
        metalness=None,
        ior=None,
        transmission=None,
        opacity=None,
        transparent=None,
        alpha_test=None,
        clearcoat=None,
        clearcoat_roughness=None,
        sheen=None,
        sheen_color=None,
        sheen_roughness=None,
        anisotropy=None,
        anisotropy_rotation=None,
        specular_intensity=None,
        specular_color=None,
        emissive=None,
        emissive_intensity=None,
        attenuation_color=None,
        attenuation_distance=None,
        thickness=None,
        iridescence=None,
        iridescence_ior=None,
        iridescence_thickness_range=None,
        dispersion=None,
        normal_scale=None,
        displacement_scale=None,
        side=None,
    ) -> PbrProperties:
        """Return a new PbrProperties with value overrides.

        Color-space convention (per :class:`PbrValues`):

        - ``color`` accepts sRGB hex (``"#rrggbb"`` / ``"#rrggbbaa"``), CSS
          names, or sRGB-by-convention numeric tuples. Stored sRGB.
          A 4th element / ``aa`` byte lifts into ``opacity`` unless
          ``opacity=`` is also passed (which wins).
        - ``emissive`` / ``sheen_color`` / ``specular_color`` /
          ``attenuation_color`` accept the same forms but are interpreted
          as **linear** for numeric tuples (matches glTF *Factor spec and
          Three.js's bare ``new THREE.Color`` constructor). Strings are
          still sRGB and get gamma-decoded.
        """
        overrides = {
            k: v
            for k, v in {
                "color": color,
                "roughness": roughness,
                "metalness": metalness,
                "ior": ior,
                "transmission": transmission,
                "opacity": opacity,
                "transparent": transparent,
                "alpha_test": alpha_test,
                "clearcoat": clearcoat,
                "clearcoat_roughness": clearcoat_roughness,
                "sheen": sheen,
                "sheen_color": sheen_color,
                "sheen_roughness": sheen_roughness,
                "anisotropy": anisotropy,
                "anisotropy_rotation": anisotropy_rotation,
                "specular_intensity": specular_intensity,
                "specular_color": specular_color,
                "emissive": emissive,
                "emissive_intensity": emissive_intensity,
                "attenuation_color": attenuation_color,
                "attenuation_distance": attenuation_distance,
                "thickness": thickness,
                "iridescence": iridescence,
                "iridescence_ior": iridescence_ior,
                "iridescence_thickness_range": iridescence_thickness_range,
                "dispersion": dispersion,
                "normal_scale": normal_scale,
                "displacement_scale": displacement_scale,
                "side": side,
            }.items()
            if v is not None
        }
        overrides = _normalize_color_overrides(overrides)
        new_values = copy.deepcopy(self.values)
        new_maps = copy.deepcopy(self.maps)
        for key, value in overrides.items():
            if isinstance(value, tuple):
                value = list(value)
            setattr(new_values, key, value)
        # Unique variant id = "<name>_<8-hex>".  The hash input includes
        # the parent id, so chained overrides cascade into distinct hashes
        # without the suffix accumulating.  A no-op override (no kwargs
        # passed) leaves the id untouched.
        new_id = (
            f"{self.name}_{_hash_override(self.id, overrides)}"
            if overrides
            else self.id
        )
        return PbrProperties(
            id=new_id,
            name=self.name,
            source=self.source,
            url=self.url,
            license=self.license,
            values=new_values,
            maps=new_maps,
            texture_repeat=self.texture_repeat,
            texture_rotation=self.texture_rotation,
            normalize_uvs=self.normalize_uvs,
            maps_dir=self.maps_dir,
        )

    def scale(
        self,
        u: float = 1,
        v: float = 1,
        fixed: bool = True,
        rotation: float = 0.0,
    ) -> PbrProperties:
        """Return a new PbrProperties with the texture UV transform replaced.

        ``scale(2, 2)`` makes the texture appear 2x larger, which
        corresponds to ``textureRepeat = (0.5, 0.5)`` in Three.js.

        ``rotation`` is in **degrees**, counterclockwise. Pivot is the
        texture center (0.5, 0.5) on the viewer side.

        When ``fixed=False``, raw (non-normalized) UVs are used, so texture
        size depends on object geometry and matches glTF/glb export.

        Each call replaces the full transform — to combine scale and
        rotation, pass them in the same call: ``mat.scale(2, 2, rotation=90)``.
        """
        scale_kwargs = {"u": u, "v": v, "fixed": fixed, "rotation": rotation}
        new_id = f"{self.name}_{_hash_override(self.id, scale_kwargs)}"
        return PbrProperties(
            id=new_id,
            name=self.name,
            source=self.source,
            url=self.url,
            license=self.license,
            values=copy.deepcopy(self.values),
            maps=copy.deepcopy(self.maps),
            texture_repeat=(1.0 / u, 1.0 / v),
            texture_rotation=rotation if rotation else None,
            normalize_uvs=fixed,
            maps_dir=self.maps_dir,
        )

    def with_maps(
        self,
        other: PbrProperties,
        *,
        only: tuple[str, ...] = ("normal", "roughness"),
    ) -> PbrProperties:
        """Return a copy of self (scalar values kept) that adopts named texture
        maps from *other*.

        Grafts a surface texture onto a scalar base — e.g. a brushed-metal
        normal/roughness set onto a solid metal's PBR values. *only* names the
        :class:`PbrMaps` fields to pull. self must not already carry file-based
        maps in a directory other than *other*'s.
        """
        new_maps = copy.deepcopy(self.maps)
        picked: dict[str, str] = {}
        for name in only:
            ref = getattr(other.maps, name, None)
            if ref is None:
                raise ValueError(f"{other.id!r} has no {name!r} map to adopt")
            setattr(new_maps, name, ref)
            picked[name] = ref

        other_needs_dir = any(not _is_data_uri(r) for r in picked.values())
        self_file_maps = any(not _is_data_uri(r) for r in self.maps.to_dict().values())
        if other_needs_dir and self_file_maps and self.maps_dir != other.maps_dir:
            raise ValueError("cannot combine file-based maps from two directories")
        maps_dir = other.maps_dir if other_needs_dir else self.maps_dir

        return PbrProperties(
            id=self.id,
            name=self.name,
            source=self.source,
            url=self.url,
            license=self.license,
            values=copy.deepcopy(self.values),
            maps=new_maps,
            texture_repeat=self.texture_repeat,
            texture_rotation=self.texture_rotation,
            normalize_uvs=self.normalize_uvs,
            maps_dir=maps_dir,
        )

    def strip_maps(self) -> PbrProperties:
        """Return a copy with all texture maps removed (scalar values kept)."""
        return PbrProperties(
            id=self.id,
            name=self.name,
            source=self.source,
            url=self.url,
            license=self.license,
            values=copy.deepcopy(self.values),
            maps=PbrMaps(),
            texture_repeat=self.texture_repeat,
            texture_rotation=self.texture_rotation,
            normalize_uvs=self.normalize_uvs,
            maps_dir=None,
        )

    # -------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return the material as a plain dict with base64 data-URI textures."""
        values_d = self.values.to_dict()
        textures_d = self.maps.to_dict()
        if self.maps_dir:
            textures_d = {
                k: v if _is_data_uri(v) else _resolve_to_data_uri(v, self.maps_dir)
                for k, v in textures_d.items()
            }
        d: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "url": self.url,
            "license": self.license,
            "values": values_d,
            "textures": textures_d,
        }
        if self.texture_repeat is not None:
            d["textureRepeat"] = list(self.texture_repeat)
        if self.texture_rotation:
            # Emit radians for Three.js consumption (texture.rotation)
            d["textureRotation"] = math.radians(self.texture_rotation)
        if not self.normalize_uvs:
            d["normalizeUvs"] = False
        return d

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string."""
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    def to_gltf(self) -> GLTF2:
        """Convert to a ``pygltflib.GLTF2`` document."""
        return _to_gltf(self)

    def save_gltf(self, path: str | Path, *, overwrite: bool = False) -> None:
        """Save the material as a ``.gltf`` or ``.glb`` file."""
        _save_gltf(self, path, overwrite=overwrite)

    # -------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------

    def dump(self, gltf: bool = False, json_format: bool = False) -> str:
        """Return a human-readable summary of the material properties."""
        if json_format:
            if gltf:
                data = json.loads(self.to_gltf().to_json())
            else:
                data = self.to_dict()
            return json.dumps(_abbreviate_textures(data), indent=2)

        if gltf:
            lines = [repr(self)]
            data = _abbreviate_textures(json.loads(self.to_gltf().to_json()))
            _dump_nested(data, lines, indent=2)
            return "\n".join(lines)

        lines = [
            f"PbrProperties(name={self.name!r}, source={self.source!r}, "
            f"license={self.license!r})",
            f"  values:  {self.values!r}",
            f"  maps:    {self.maps!r}",
        ]
        if self.texture_repeat is not None:
            lines.append(f"  texture_repeat: {self.texture_repeat}")
        if not self.normalize_uvs:
            lines.append("  normalize_uvs: False")
        if self.maps_dir is not None and self.maps.to_dict():
            lines.append(f"  maps_dir: {self.maps_dir}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return self.dump()

    # -------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------

    def interpolate_color(self, override_color=None) -> tuple[float, float, float, float]:
        """Estimate a representative sRGB color + alpha for CAD mode display.

        When ``override_color`` is given, it replaces ``values.color`` for the
        preview — equivalent to what ``mat.override(color=override_color)``
        would render, without materializing a new PbrProperties. Multiplied
        in linear space against the color texture average if one exists.
        """
        color_val = self.values.color
        color_tex = self.maps.color

        def _tex_avg_linear():
            if self.maps_dir is not None:
                return _linear_average_texture(ref=color_tex, texture_dir=self.maps_dir)
            return _linear_average_texture(texture=color_tex)

        # override_color arg wins over values.color; both are sRGB tints
        # against the texture (if any).
        tint_srgb = None
        if override_color is not None:
            tint_srgb, _ = _normalize_srgb_color(override_color)
        elif isinstance(color_val, str):
            tint_srgb, _ = _normalize_srgb_color(color_val)
        elif isinstance(color_val, list):
            tint_srgb = tuple(color_val[:3])

        if color_tex is not None:
            lr, lg, lb = _tex_avg_linear()
            if tint_srgb is not None:
                tint_lin = [_srgb_to_linear(c) for c in tint_srgb]
                mr, mg, mb = lr * tint_lin[0], lg * tint_lin[1], lb * tint_lin[2]
                # Rescale to texture luminance — approximates the lighting +
                # tone mapping contribution; without it the swatch reads dim.
                y_tex = 0.2126 * lr + 0.7152 * lg + 0.0722 * lb
                y_mul = 0.2126 * mr + 0.7152 * mg + 0.0722 * mb
                if y_mul > 1e-6:
                    s = min(y_tex / y_mul, 8.0)
                    mr, mg, mb = mr * s, mg * s, mb * s
                sr = _linear_to_srgb(min(mr, 1.0))
                sg = _linear_to_srgb(min(mg, 1.0))
                sb = _linear_to_srgb(min(mb, 1.0))
            else:
                sr, sg, sb = _linear_to_srgb(lr), _linear_to_srgb(lg), _linear_to_srgb(lb)
        elif tint_srgb is not None:
            sr, sg, sb = tint_srgb
        else:
            sr, sg, sb = 0.5, 0.5, 0.5

        alpha = 1.0
        opacity_val = self.values.opacity
        if isinstance(opacity_val, (int, float)) and opacity_val < 1.0:
            alpha = float(opacity_val)
        else:
            transmission_val = self.values.transmission
            if isinstance(transmission_val, (int, float)) and transmission_val > 0:
                alpha = max(0.15, 1.0 - transmission_val * 0.7)

        return (round(sr, 4), round(sg, 4), round(sb, 4), round(alpha, 4))
