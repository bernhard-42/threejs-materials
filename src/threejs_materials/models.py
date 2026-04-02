"""Core data models: PbrValues, PbrMaps, PbrProperties."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from pathlib import Path

# ---------------------------------------------------------------------------
# camelCase ↔ snake_case name mappings
# ---------------------------------------------------------------------------

# Only entries where the names differ; single-word names are identity-mapped.
_CAMEL_TO_SNAKE = {
    "normalScale": "normal_scale",
    "emissiveIntensity": "emissive_intensity",
    "alphaTest": "alpha_test",
    "clearcoatRoughness": "clearcoat_roughness",
    "sheenColor": "sheen_color",
    "sheenRoughness": "sheen_roughness",
    "anisotropyRotation": "anisotropy_rotation",
    "specularIntensity": "specular_intensity",
    "specularColor": "specular_color",
    "attenuationColor": "attenuation_color",
    "attenuationDistance": "attenuation_distance",
    "iridescenceIOR": "iridescence_ior",
    "iridescenceThicknessRange": "iridescence_thickness_range",
    "displacementScale": "displacement_scale",
    # maps-only
    "clearcoatNormal": "clearcoat_normal",
    "metallicRoughness": "metallic_roughness",
}
_SNAKE_TO_CAMEL = {v: k for k, v in _CAMEL_TO_SNAKE.items()}


def _to_snake(name: str) -> str:
    return _CAMEL_TO_SNAKE.get(name, name)


def _to_camel(name: str) -> str:
    return _SNAKE_TO_CAMEL.get(name, name)


# ---------------------------------------------------------------------------
# PbrValues / PbrMaps
# ---------------------------------------------------------------------------


def _compact_repr(obj) -> str:
    """Repr showing only non-None fields, with data URIs abbreviated."""
    cls = type(obj)
    parts = []
    for f in fields(cls):
        val = getattr(obj, f.name)
        if val is not None:
            if isinstance(val, str) and val.startswith("data:"):
                parts.append(f"{f.name}='data:...;base64,...'")
            else:
                parts.append(f"{f.name}={val!r}")
    return f"{cls.__name__}({', '.join(parts)})"


@dataclass
class PbrValues:
    """Scalar PBR property values (Three.js MeshPhysicalMaterial)."""

    color: list | None = None
    metalness: float | None = None
    roughness: float | None = None
    ior: float | None = None
    normal_scale: list | None = None
    emissive: list | None = None
    emissive_intensity: float | None = None
    transmission: float | None = None
    opacity: float | None = None
    transparent: bool | None = None
    alpha_test: float | None = None
    clearcoat: float | None = None
    clearcoat_roughness: float | None = None
    sheen: float | None = None
    sheen_color: list | None = None
    sheen_roughness: float | None = None
    anisotropy: float | None = None
    anisotropy_rotation: float | None = None
    specular_intensity: float | None = None
    specular_color: list | None = None
    attenuation_color: list | None = None
    attenuation_distance: float | None = None
    thickness: float | None = None
    iridescence: float | None = None
    iridescence_ior: float | None = None
    iridescence_thickness_range: list | None = None
    dispersion: float | None = None
    displacement_scale: float | None = None
    side: int | None = None

    def __repr__(self) -> str:
        return _compact_repr(self)

    def to_dict(self) -> dict:
        """Return non-None values as ``{camelCase_key: value}``."""
        return {
            _to_camel(f.name): getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }

    @classmethod
    def from_dict(cls, d: dict) -> PbrValues:
        """Build from a ``{camelCase_key: value}`` dict."""
        valid = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in d.items():
            snake = _to_snake(k)
            if snake in valid:
                kwargs[snake] = v
        return cls(**kwargs)


@dataclass
class PbrMaps:
    """Texture map references (file path or data URI)."""

    color: str | None = None
    metalness: str | None = None
    roughness: str | None = None
    normal: str | None = None
    emissive: str | None = None
    ao: str | None = None
    opacity: str | None = None
    clearcoat: str | None = None
    clearcoat_roughness: str | None = None
    clearcoat_normal: str | None = None
    transmission: str | None = None
    sheen_color: str | None = None
    sheen_roughness: str | None = None
    anisotropy: str | None = None
    iridescence: str | None = None
    specular_intensity: str | None = None
    specular_color: str | None = None
    thickness: str | None = None
    displacement: str | None = None
    metallic_roughness: str | None = None

    def __repr__(self) -> str:
        return _compact_repr(self)

    def to_dict(self) -> dict:
        """Return non-None maps as ``{camelCase_key: texture_ref}``."""
        return {
            _to_camel(f.name): getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }

    @classmethod
    def from_dict(cls, d: dict) -> PbrMaps:
        """Build from a ``{camelCase_key: texture_ref}`` dict."""
        valid = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in d.items():
            snake = _to_snake(k)
            if snake in valid:
                kwargs[snake] = v
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# PbrProperties
# ---------------------------------------------------------------------------


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
    maps_dir: Path | None = field(default=None, repr=False)

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
            maps_dir=Path(td) if td is not None else None,
        )
