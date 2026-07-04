"""Core data models: PbrValues, PbrMaps, PbrProperties."""

from __future__ import annotations

from dataclasses import dataclass, fields

from threejs_materials.utils import _normalize_color, _normalize_srgb_color

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
    """Scalar PBR property values (Three.js MeshPhysicalMaterial).

    Color-space convention per field (matters for numeric storage):

    - ``color``: **sRGB byte ratios** in [0, 1]. Three.js renders via
      ``setRGB(r, g, b, SRGBColorSpace)`` (see three-cad-viewer
      ``material-factory.ts``); the viewer linearizes internally. glTF
      export converts to linear at the boundary (per spec).
    - ``emissive`` / ``sheen_color`` / ``specular_color`` /
      ``attenuation_color``: **linear RGB** in [0, 1]. Matches the glTF
      *Factor spec and Three.js's bare ``new THREE.Color(r, g, b)``
      working-space convention.

    ``from_dict`` is a passthrough — callers populating ``PbrValues``
    directly are responsible for matching the per-field convention.
    """

    color: list | None = None
    """sRGB byte ratios in [0, 1] — see class docstring."""
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
# PbrOverrides — typed config object for override()
# ---------------------------------------------------------------------------

Color = int | str | tuple[float, ...] | list[float]
"""Permissive color type accepted by override-style APIs.

- ``int``: packed hex RGB (e.g. ``0xff0000``), converted to ``"#rrggbb"``.
- ``str``: CSS hex string (``"#rrggbb"`` / ``"#rrggbbaa"`` for alpha) or
  named color (``"red"``). Always interpreted as sRGB.
- ``tuple`` / ``list`` of 3 floats in [0, 1].
- ``tuple`` / ``list`` of 4 floats in [0, 1] (RGB + alpha).

The numeric-tuple convention is **field-dependent**:

- For the ``color`` field, numeric tuples are interpreted as **sRGB** —
  matching Three.js's ``setRGB(SRGBColorSpace)`` consumption in
  three-cad-viewer. ``(0.5, 0.5, 0.5)`` means perceptual midgray.
- For ``emissive``, ``sheen_color``, ``specular_color``,
  ``attenuation_color``, numeric tuples are **linear** — matching Three.js's
  bare-constructor ``new THREE.Color(r, g, b)`` (working color space) and
  the glTF *Factor spec.

String inputs (always sRGB) are decoded to the field's storage space
automatically.

For ``PbrOverrides.color``, the alpha channel (4th element or hex AA) is
lifted into the separate ``opacity`` field; the explicit ``opacity=`` kwarg
takes precedence when both are supplied.
"""


@dataclass(frozen=True)
class PbrOverrides:
    """Typed config object mirroring ``PbrProperties.override()`` kwargs.

    Every field defaults to ``None`` (unset). Use ``.as_kwargs()`` to convert
    into a kwargs dict suitable for ``mat.override(**overrides.as_kwargs())``.
    Frozen so instances are hashable and safe to share across calls.

    Color inputs accept the permissive ``Color`` type. Per-field convention:

    - ``color`` is sRGB-stored after normalization (matches three-cad-viewer's
      ``setRGB(SRGBColorSpace)`` consumption). Any alpha component (4-tuple
      or ``#rrggbbaa``) is lifted into the separate ``opacity`` field —
      explicit ``opacity=`` wins when both are set.
    - ``emissive``, ``sheen_color``, ``specular_color``, ``attenuation_color``
      are linear-stored after normalization (glTF *Factor spec; Three.js
      bare ``new THREE.Color`` constructor).

    Excludes UV-transform parameters (``texture_scale``, ``fixed_size``) since
    those are not PBR material properties per the glTF spec — they live on
    ``TextureTransform`` instead and are applied via ``mat.scale(...)``.

    Type hints on the 5 color fields (``color``, ``emissive``, ``sheen_color``,
    ``specular_color``, ``attenuation_color``) reflect the **input** type
    accepted by the constructor — the permissive :data:`Color` alias.
    ``__post_init__`` normalizes each to a 3-tuple of floats (sRGB for
    ``color``, linear for the others); reads always observe the normalized
    form despite the wider annotation.

    **Extension point.** Subclasses can override the ``_color_to_tuple``
    static method to bridge a custom color type (e.g. build123d's
    ``Color``) to a 3- or 4-tuple of floats. The hook runs before the
    standard normalizer, so subclasses only need to handle the type
    coercion — sRGB-vs-linear handling and alpha lifting stay in the base.
    """
    color: Color | None = None
    roughness: float | None = None
    metalness: float | None = None
    ior: float | None = None
    transmission: float | None = None
    opacity: float | None = None
    transparent: bool | None = None
    alpha_test: float | None = None
    clearcoat: float | None = None
    clearcoat_roughness: float | None = None
    sheen: float | None = None
    sheen_color: Color | None = None
    sheen_roughness: float | None = None
    anisotropy: float | None = None
    anisotropy_rotation: float | None = None
    specular_intensity: float | None = None
    specular_color: Color | None = None
    emissive: Color | None = None
    emissive_intensity: float | None = None
    attenuation_color: Color | None = None
    attenuation_distance: float | None = None
    thickness: float | None = None
    iridescence: float | None = None
    iridescence_ior: float | None = None
    iridescence_thickness_range: tuple[float, float] | None = None
    dispersion: float | None = None
    normal_scale: tuple[float, float] | None = None
    displacement_scale: float | None = None

    @staticmethod
    def _color_to_tuple(c):
        """Subclass extension point: coerce a custom color type to a form
        the normalizer accepts (string / 3-tuple / 4-tuple / list).
        Default is passthrough; ``int`` is already handled in __post_init__."""
        return c

    def __post_init__(self):
        def _prep(c):
            if isinstance(c, int) and not isinstance(c, bool):
                c = f"#{c:06x}"
            return self._color_to_tuple(c)

        if self.color is not None:
            rgb, alpha = _normalize_srgb_color(_prep(self.color))
            object.__setattr__(self, "color", rgb)
            if alpha is not None and self.opacity is None:
                object.__setattr__(self, "opacity", alpha)
        for fname in ("emissive", "sheen_color", "specular_color", "attenuation_color"):
            val = getattr(self, fname)
            if val is not None:
                rgb, _ = _normalize_color(_prep(val))
                object.__setattr__(self, fname, rgb)

    def __repr__(self) -> str:
        return _compact_repr(self)

    def as_kwargs(self) -> dict:
        """Return non-None fields as a kwargs dict for ``mat.override(**...)``."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }


# ---------------------------------------------------------------------------
# TextureTransform — typed config object for scale()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextureTransform:
    """Typed config object mirroring ``PbrProperties.scale()`` arguments.

    Mirrors the scale + rotation components of glTF's ``KHR_texture_transform``
    plus the ``fixed=`` flag of ``scale()``. ``rotation`` is in degrees,
    counterclockwise. Use ``.as_kwargs()`` for ``mat.scale(**transform.as_kwargs())``.
    """
    scale: tuple[float, float] = (1.0, 1.0)
    rotation: float = 0.0
    fixed_size: bool = True

    def as_kwargs(self) -> dict:
        """Return fields as a kwargs dict for ``mat.scale(**...)``."""
        return {
            "u": self.scale[0],
            "v": self.scale[1],
            "rotation": self.rotation,
            "fixed": self.fixed_size,
        }


