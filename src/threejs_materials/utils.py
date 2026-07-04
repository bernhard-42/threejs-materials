"""Shared utilities for optional dependencies, data URIs, and image helpers."""

import base64
import io
import mimetypes
import sys
from pathlib import Path
from types import ModuleType

from PIL import Image as PILImage
from PIL import ImageColor


# ---------------------------------------------------------------------------
# Optional dependencies. MaterialX (baking/conversion) and OpenEXR (Polyhaven
# full-precision inputs) are both optional — the bundled materials and all
# load/serialize paths work without them. Conversion from a source requires
# MaterialX:  pip install threejs-materials[materialx]
# ---------------------------------------------------------------------------

OpenEXR: ModuleType | None = None
Imath: ModuleType | None = None
try:
    import OpenEXR as _OpenEXR
    import Imath as _Imath

    OpenEXR = _OpenEXR
    Imath = _Imath
except ImportError:
    pass


_MATERIALX_INSTALL_MSG = (
    "MaterialX is required to convert materials from a source, but it is not "
    "installed. Install it with:  pip install threejs-materials[materialx]"
)


def ensure_materialx() -> ModuleType:
    """Import and return the MaterialX module (render submodules registered).

    MaterialX is an optional dependency. Called at the top of every function
    that bakes/extracts. Raises ImportError with an install hint when absent.
    """
    try:
        import MaterialX as mx
        from MaterialX import PyMaterialXRender, PyMaterialXRenderGlsl  # noqa: F401
    except ImportError as e:
        raise ImportError(_MATERIALX_INSTALL_MSG) from e
    if sys.platform == "darwin":
        try:
            from MaterialX import PyMaterialXRenderMsl  # noqa: F401
        except ImportError:
            pass
    return mx


# ---------------------------------------------------------------------------
# Data URI helpers
# ---------------------------------------------------------------------------


def _is_data_uri(s: str) -> bool:
    """Return True if *s* is a base64 data URI."""
    return s.startswith("data:")


def _abbreviate_textures(obj):
    """Deep-copy a dict, replacing base64 data URIs with a short placeholder."""
    if isinstance(obj, dict):
        return {k: _abbreviate_textures(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_abbreviate_textures(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("data:"):
        return "data:image/png;base64,..."
    return obj


def _resolve_to_data_uri(texture_ref: str, texture_dir: Path) -> str:
    """Resolve a texture reference to a base64 data URI.

    If *texture_ref* is already a data URI it is returned unchanged.
    Otherwise it is treated as a filename relative to *texture_dir*
    and the file is read and base64-encoded.  1-bit images are
    converted to 8-bit before encoding.
    """
    if _is_data_uri(texture_ref):
        return texture_ref
    file_path = texture_dir / texture_ref
    # Check for 1-bit/palette images that need conversion
    img: PILImage.Image = PILImage.open(file_path)
    if img.mode in ("1", "P"):
        img = img.convert("L") if len(img.getbands()) == 1 else img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime is None:
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }.get(file_path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _open_texture_image(ref: str, texture_dir: Path | None = None):
    """Open a texture as a PIL Image from a data URI or file path.

    1-bit and palette images are converted to L or RGB so that pixel
    values are proper 0-255 uint8 (a 1-bit True would otherwise become
    1 instead of 255 in numpy arrays).
    """
    img: PILImage.Image
    if _is_data_uri(ref):
        _, b64 = ref.split(",", 1)
        img = PILImage.open(io.BytesIO(base64.b64decode(b64)))
    elif texture_dir is not None:
        img = PILImage.open(texture_dir / ref)
    else:
        img = PILImage.open(ref)
    if img.mode in ("1", "P"):
        img = img.convert("L") if len(img.getbands()) == 1 else img.convert("RGB")
    return img


def _has_real_alpha(ref: str, texture_dir: Path | None = None) -> bool:
    """Check if a texture has any non-opaque alpha pixels."""
    img = _open_texture_image(ref, texture_dir)
    if img.mode != "RGBA":
        return False
    alpha_min, _ = img.getchannel("A").getextrema()
    return alpha_min < 255


# ---------------------------------------------------------------------------
# Color-space helpers
# ---------------------------------------------------------------------------


def _linear_to_srgb(c: float) -> float:
    """Convert a single linear RGB component to sRGB (0-1)."""
    c = max(0.0, min(1.0, c))
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _srgb_to_linear(c: float) -> float:
    """Convert a single sRGB component to linear RGB (0-1)."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_average_texture(
    ref: str | None = None,
    texture_dir: Path | None = None,
    texture: str | bytes | None = None,
    as_linear_srgb: bool = True,
) -> tuple[float, float, float]:
    """Return the average color of a texture in linear RGB."""
    if texture is not None:
        if isinstance(texture, str):
            # data URI — split off header like "image/png;base64,"
            _, b64data = texture.split(",", 1)
            img_bytes = base64.b64decode(b64data)
            img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
        else:
            img = PILImage.open(io.BytesIO(texture)).convert("RGB")
    elif ref is not None and texture_dir is not None:
        img = _open_texture_image(ref, texture_dir).convert("RGB")
    else:
        raise TypeError("Either texture or ref, texture_dir need to be set")

    px = img.resize((1, 1), PILImage.Resampling.LANCZOS).getpixel((0, 0))
    assert isinstance(px, tuple)
    r, g, b = px[:3]
    if as_linear_srgb:
        return (_srgb_to_linear(r / 255.0), _srgb_to_linear(g / 255.0), _srgb_to_linear(b / 255.0))
    return (r / 255.0, g / 255.0, b / 255.0)


def texture_average_color(
    texture: bytes, as_linear_srgb: bool = True
) -> tuple[float, float, float]:
    return _linear_average_texture(texture=texture, as_linear_srgb=as_linear_srgb)


def _parse_color_string(
    color: str, as_linear: bool = True
) -> tuple[float, float, float]:
    """Parse a CSS color name or hex string to an RGB tuple in [0, 1].

    Supports ``#rgb``, ``#rrggbb``, and CSS named colors (same set as Three.js).

    When ``as_linear`` is True (default), the bytes are gamma-decoded from
    sRGB to linear — correct for Three.js ``MeshPhysicalMaterial.color`` and
    any PBR channel the renderer expects in linear space.

    When ``as_linear`` is False, the bytes are returned as raw ``value/255``
    ratios with no gamma curve applied — suitable for display-space uses
    where no linearization should happen.
    """
    r, g, b = ImageColor.getrgb(color)[:3]
    if as_linear:
        return (
            _srgb_to_linear(r / 255.0),
            _srgb_to_linear(g / 255.0),
            _srgb_to_linear(b / 255.0),
        )
    return (r / 255.0, g / 255.0, b / 255.0)


def _normalize_color(c) -> tuple[tuple[float, float, float], float | None]:
    """Normalize a permissive color value to **linear** ``((r, g, b), alpha | None)``.

    Used for color fields that Three.js consumes in linear space — namely
    ``emissive``, ``sheen_color``, ``specular_color``, ``attenuation_color``
    — matching the glTF *Factor spec and Three.js's bare-constructor
    ``new THREE.Color(r, g, b)`` semantics (working color space = linear).

    For ``color`` (which Three.js reads via ``setRGB(SRGBColorSpace)`` and
    therefore expects sRGB byte ratios), use ``_normalize_srgb_color``.

    Accepted inputs:
    - ``"#rrggbb"`` / ``"#rgb"`` / CSS name (sRGB) → linear RGB, alpha=None
    - ``"#rrggbbaa"`` (sRGB + alpha)               → linear RGB, alpha
    - ``(r, g, b)`` floats in [0, 1] (linear)      → linear RGB, alpha=None
    - ``(r, g, b, a)`` floats in [0, 1] (linear)   → linear RGB, alpha

    String inputs are sRGB-decoded; numeric tuples/lists are returned
    untouched (already linear by convention).
    """
    if isinstance(c, str):
        s = c.strip()
        if s.startswith("#") and len(s) == 9:
            r, g, b = ImageColor.getrgb(s[:7])[:3]
            a = int(s[7:9], 16) / 255.0
            return (
                (
                    _srgb_to_linear(r / 255.0),
                    _srgb_to_linear(g / 255.0),
                    _srgb_to_linear(b / 255.0),
                ),
                a,
            )
        return (_parse_color_string(s, as_linear=True), None)
    if isinstance(c, (tuple, list)):
        if len(c) == 3:
            return ((float(c[0]), float(c[1]), float(c[2])), None)
        if len(c) == 4:
            return (
                (float(c[0]), float(c[1]), float(c[2])),
                float(c[3]),
            )
        raise ValueError(
            f"Color tuple/list must have 3 or 4 elements, got {len(c)}"
        )
    raise TypeError(f"Unsupported color type: {type(c).__name__}")


def _normalize_srgb_color(c) -> tuple[tuple[float, float, float], float | None]:
    """Normalize a permissive color value to **sRGB** ``((r, g, b), alpha | None)``.

    Used for the ``color`` field that Three.js consumes via
    ``setRGB(r, g, b, SRGBColorSpace)`` — the viewer linearizes internally.
    Storing sRGB byte ratios in ``values.color`` lets a numeric input like
    ``(0.5, 0.5, 0.5)`` mean "perceptual midgray" rather than "linear midgray."

    For color fields Three.js consumes as linear (emissive, sheen_color,
    specular_color, attenuation_color), use ``_normalize_color``.

    Accepted inputs:
    - ``"#rrggbb"`` / ``"#rgb"`` / CSS name (sRGB) → sRGB byte ratios, alpha=None
    - ``"#rrggbbaa"`` (sRGB + alpha)               → sRGB byte ratios, alpha
    - ``(r, g, b)`` floats in [0, 1] (sRGB)        → passthrough, alpha=None
    - ``(r, g, b, a)`` floats in [0, 1] (sRGB)     → passthrough, alpha
    """
    if isinstance(c, str):
        s = c.strip()
        if s.startswith("#") and len(s) == 9:
            r, g, b = ImageColor.getrgb(s[:7])[:3]
            a = int(s[7:9], 16) / 255.0
            return ((r / 255.0, g / 255.0, b / 255.0), a)
        return (_parse_color_string(s, as_linear=False), None)
    if isinstance(c, (tuple, list)):
        if len(c) == 3:
            return ((float(c[0]), float(c[1]), float(c[2])), None)
        if len(c) == 4:
            return (
                (float(c[0]), float(c[1]), float(c[2])),
                float(c[3]),
            )
        raise ValueError(
            f"Color tuple/list must have 3 or 4 elements, got {len(c)}"
        )
    raise TypeError(f"Unsupported color type: {type(c).__name__}")
