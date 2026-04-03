"""Public API: PbrProperties dataclass with all factory and instance methods."""

from __future__ import annotations

import copy
import json
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

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
from threejs_materials.utils import (
    ensure_materialx,
    _abbreviate_textures,
    _is_data_uri,
    _resolve_to_data_uri,
    _linear_to_srgb,
    _average_texture_linear,
    _parse_color_string,
)

log = logging.getLogger(__name__)


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
    normalize_uvs: bool = True
    maps_dir: Path | None = field(default=None, repr=False)

    # -------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> PbrProperties:
        """Build from a raw data dict (as stored in cache JSON or returned by loaders)."""
        td = data.get("maps_dir")
        return cls(
            id=data["id"],
            name=data["name"],
            source=data["source"],
            url=data["url"],
            license=data["license"],
            values=PbrValues.from_dict(data.get("values", {})),
            maps=PbrMaps.from_dict(data.get("textures", {})),
            texture_repeat=data.get("texture_repeat"),
            normalize_uvs=data.get("normalize_uvs", True),
            maps_dir=Path(td) if td is not None else None,
        )

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
    def from_mtlx(cls, mtlx_file: str) -> PbrProperties:
        """Convert a local .mtlx file to PbrProperties."""
        ensure_materialx()
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
            properties, _, tex_dir = _process_mtlx(mtlx_path)
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
    def from_physicallybased(cls, name: str, resolution: str = "1K") -> PbrProperties:
        """Download, convert, and cache a PhysicallyBased material."""
        from threejs_materials.sources import _SOURCE_LOADERS
        return cls.from_dict(_SOURCE_LOADERS["physicallybased"].load(name, resolution))

    @classmethod
    def create(
        cls,
        id: str,
        *,
        color=(0.8, 0.8, 0.8),
        metalness: float = 0.0,
        roughness: float = 0.5,
        ior: float = 1.5,
        transmission: float = 0.0,
        opacity: float = 1.0,
        transparent: bool = False,
        alpha_test: float | None = None,
        emissive: tuple | list | None = None,
        emissive_intensity: float | None = None,
        clearcoat: float = 0.0,
        clearcoat_roughness: float = 0.0,
        sheen: float = 0.0,
        sheen_color: tuple | list | None = None,
        sheen_roughness: float = 0.0,
        anisotropy: float = 0.0,
        anisotropy_rotation: float = 0.0,
        specular_intensity: float = 1.0,
        specular_color: tuple | list | None = None,
        attenuation_color: tuple | list | None = None,
        attenuation_distance: float | None = None,
        thickness: float = 0.0,
        iridescence: float = 0.0,
        iridescence_ior: float = 1.3,
        iridescence_thickness_range: tuple | list | None = None,
        dispersion: float = 0.0,
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
        """Create PbrProperties from explicit PBR values and texture paths."""
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

        if isinstance(color, str):
            color_val = list(_parse_color_string(color))
        else:
            color_val = list(color)[:3]

        values = PbrValues(
            color=color_val, metalness=metalness, roughness=roughness, ior=ior,
        )
        if transmission > 0:
            values.transmission = transmission
        if opacity < 1.0:
            values.opacity = opacity
        if transparent:
            values.transparent = True
        if alpha_test is not None:
            values.alpha_test = alpha_test
        if emissive is not None:
            values.emissive = list(emissive[:3])
        if emissive_intensity is not None:
            values.emissive_intensity = emissive_intensity
        if clearcoat > 0:
            values.clearcoat = clearcoat
            values.clearcoat_roughness = clearcoat_roughness
        if sheen > 0:
            values.sheen = sheen
            if sheen_color is not None:
                values.sheen_color = list(sheen_color[:3])
            values.sheen_roughness = sheen_roughness
        if anisotropy > 0:
            values.anisotropy = anisotropy
            values.anisotropy_rotation = anisotropy_rotation
        if specular_intensity != 1.0:
            values.specular_intensity = specular_intensity
        if specular_color is not None:
            values.specular_color = list(specular_color[:3])
        if attenuation_color is not None:
            values.attenuation_color = list(attenuation_color[:3])
        if attenuation_distance is not None:
            values.attenuation_distance = attenuation_distance
        if thickness > 0:
            values.thickness = thickness
        if iridescence > 0:
            values.iridescence = iridescence
            values.iridescence_ior = iridescence_ior
            if iridescence_thickness_range is not None:
                values.iridescence_thickness_range = list(iridescence_thickness_range)
        if dispersion > 0:
            values.dispersion = dispersion
        if normal_scale is not None:
            values.normal_scale = list(normal_scale)
        if displacement_scale is not None:
            values.displacement_scale = displacement_scale
        if side is not None:
            values.side = side

        tex_inputs = {
            "color": color_map, "metalness": metalness_map,
            "roughness": roughness_map, "normal": normal_map,
            "emissive": emissive_map, "ao": ao_map, "opacity": opacity_map,
            "clearcoat": clearcoat_map,
            "clearcoat_roughness": clearcoat_roughness_map,
            "clearcoat_normal": clearcoat_normal_map,
            "transmission": transmission_map, "sheen_color": sheen_color_map,
            "sheen_roughness": sheen_roughness_map,
            "anisotropy": anisotropy_map, "iridescence": iridescence_map,
            "specular_intensity": specular_intensity_map,
            "specular_color": specular_color_map,
            "thickness": thickness_map, "displacement": displacement_map,
        }
        maps = PbrMaps()
        for field_name, tex_path in tex_inputs.items():
            uri = _resolve_texture(tex_path)
            if uri:
                setattr(maps, field_name, uri)
                if field_name == "color":
                    values.color = [1.0, 1.0, 1.0]
                elif field_name == "metalness":
                    values.metalness = 1.0
                elif field_name == "roughness":
                    values.roughness = 1.0

        maps_dir = None
        if texture_dirs:
            common = texture_dirs[0]
            if not all(d == common for d in texture_dirs):
                raise ValueError("All texture files must be in the same directory")
            maps_dir = common

        return cls(
            id=id, name=id, source="custom", url="", license="",
            values=values, maps=maps, maps_dir=maps_dir,
        )

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
        clearcoat=None,
        clearcoat_roughness=None,
        sheen=None,
        sheen_color=None,
        sheen_roughness=None,
        anisotropy=None,
        anisotropy_rotation=None,
        specular_intensity=None,
        emissive=None,
        emissive_intensity=None,
        attenuation_color=None,
        attenuation_distance=None,
        thickness=None,
        iridescence=None,
    ) -> PbrProperties:
        """Return a new PbrProperties with value overrides."""
        overrides = {
            k: v
            for k, v in {
                "color": color, "roughness": roughness, "metalness": metalness,
                "ior": ior, "transmission": transmission, "opacity": opacity,
                "clearcoat": clearcoat, "clearcoat_roughness": clearcoat_roughness,
                "sheen": sheen, "sheen_color": sheen_color,
                "sheen_roughness": sheen_roughness, "anisotropy": anisotropy,
                "anisotropy_rotation": anisotropy_rotation,
                "specular_intensity": specular_intensity,
                "emissive": emissive, "emissive_intensity": emissive_intensity,
                "attenuation_color": attenuation_color,
                "attenuation_distance": attenuation_distance,
                "thickness": thickness, "iridescence": iridescence,
            }.items()
            if v is not None
        }
        new_values = copy.deepcopy(self.values)
        new_maps = copy.deepcopy(self.maps)
        for key, value in overrides.items():
            if isinstance(value, tuple):
                value = list(value)
            if key == "color" and new_maps.color is not None:
                new_maps.color = None
                warnings.warn(
                    "color override: existing color texture removed and "
                    "replaced by solid color value",
                    stacklevel=2,
                )
            setattr(new_values, key, value)
        return PbrProperties(
            id=self.id, name=self.name, source=self.source,
            url=self.url, license=self.license,
            values=new_values, maps=new_maps,
            texture_repeat=self.texture_repeat, normalize_uvs=self.normalize_uvs,
            maps_dir=self.maps_dir,
        )

    def scale(self, u: float = 1, v: float = 1, fixed: bool = True) -> PbrProperties:
        """Return a new PbrProperties with texture scale applied.

        ``scale(2, 2)`` makes the texture appear 2x larger, which
        corresponds to ``textureRepeat = (0.5, 0.5)`` in Three.js.

        When ``fixed=False``, raw (non-normalized) UVs are used, so texture
        size depends on object geometry and matches glTF/glb export.
        """
        return PbrProperties(
            id=self.id, name=self.name, source=self.source,
            url=self.url, license=self.license,
            values=copy.deepcopy(self.values), maps=copy.deepcopy(self.maps),
            texture_repeat=(1.0 / u, 1.0 / v), normalize_uvs=fixed,
            maps_dir=self.maps_dir,
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
        d = {
            "id": self.id, "name": self.name, "source": self.source,
            "url": self.url, "license": self.license,
            "values": values_d, "textures": textures_d,
        }
        if self.texture_repeat is not None:
            d["textureRepeat"] = list(self.texture_repeat)
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

    def interpolate_color(self) -> tuple[float, float, float, float]:
        """Estimate a representative sRGB color + alpha for CAD mode display."""
        color_val = self.values.color
        color_tex = self.maps.color

        if isinstance(color_val, str):
            r, g, b = _parse_color_string(color_val)
        elif color_tex is not None:
            tr, tg, tb = _average_texture_linear(color_tex, self.maps_dir)
            if isinstance(color_val, list):
                r, g, b = color_val[0] * tr, color_val[1] * tg, color_val[2] * tb
            else:
                r, g, b = tr, tg, tb
        elif isinstance(color_val, list):
            r, g, b = color_val[:3]
        else:
            r, g, b = 0.5, 0.5, 0.5

        sr, sg, sb = _linear_to_srgb(r), _linear_to_srgb(g), _linear_to_srgb(b)

        alpha = 1.0
        opacity_val = self.values.opacity
        if isinstance(opacity_val, (int, float)) and opacity_val < 1.0:
            alpha = float(opacity_val)
        else:
            transmission_val = self.values.transmission
            if isinstance(transmission_val, (int, float)) and transmission_val > 0:
                alpha = max(0.15, 1.0 - transmission_val * 0.7)

        return (round(sr, 4), round(sg, 4), round(sb, 4), round(alpha, 4))
