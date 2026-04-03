"""Shared utilities for lazy dependency loading, data URIs, and image helpers."""

import base64
import io
import mimetypes
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageColor


# ---------------------------------------------------------------------------
# MaterialX lazy loading
# ---------------------------------------------------------------------------

_COMPILE_NOTE = """\
Note: For the latest Python, the installer tries to compile these packages. \
This might not be possible under Windows if no compiler is installed."""

_MATERIALX_INSTALL_MSG = f"""\
materialx is not installed. Use
- "pip install threejs-materials[materialx]"
or install it directly
- "pip install materialx"
{_COMPILE_NOTE}"""

_OPENEXR_INSTALL_MSG = f"""\
openexr is not installed. Use
- "pip install threejs-materials[materialx]"
or install it directly
- "pip install openexr"
{_COMPILE_NOTE}"""


def ensure_materialx():
    """Import and return the MaterialX module, raising ImportError with install instructions.

    Also imports the render submodules (PyMaterialXRender, PyMaterialXRenderGlsl,
    PyMaterialXRenderMsl) so they are accessible as ``mx.PyMaterialXRender``, etc.
    """
    try:
        import MaterialX
        from MaterialX import PyMaterialXRender  # noqa: F401
        from MaterialX import PyMaterialXRenderGlsl  # noqa: F401
    except ImportError as e:
        raise ImportError(_MATERIALX_INSTALL_MSG) from e

    from sys import platform

    if platform == "darwin":
        try:
            from MaterialX import PyMaterialXRenderMsl  # noqa: F401
        except ImportError:
            pass

    return MaterialX


def ensure_openexr():
    """Import and return (OpenEXR, Imath), raising ImportError with install instructions.

    Imath is bundled with the openexr package — no separate install needed.
    """
    try:
        import OpenEXR
        import Imath
    except ImportError as e:
        raise ImportError(_OPENEXR_INSTALL_MSG) from e
    return OpenEXR, Imath


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
    img = PILImage.open(file_path)
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
    if _is_data_uri(ref):
        _, b64 = ref.split(",", 1)
        img = PILImage.open(io.BytesIO(base64.b64decode(b64)))
    elif texture_dir is not None:
        img = PILImage.open(texture_dir / ref)
    else:
        img = PILImage.open(ref)
    if img.mode in ("1", "P"):
        img = img.convert("L") if img.getbands() == ("1",) or len(img.getbands()) == 1 else img.convert("RGB")
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


def _average_texture_linear(
    ref: str, texture_dir: Path | None = None
) -> tuple[float, float, float]:
    """Return the average color of a texture in linear RGB."""
    img = _open_texture_image(ref, texture_dir).convert("RGB")
    avg = img.resize((1, 1), PILImage.LANCZOS).getpixel((0, 0))
    r, g, b = (_srgb_to_linear(c / 255.0) for c in avg[:3])
    return (r, g, b)


def _parse_color_string(color: str) -> tuple[float, float, float]:
    """Parse a CSS color name or hex string to linear RGB (0-1).

    Supports ``#rgb``, ``#rrggbb``, and CSS named colors (same set as Three.js).
    """
    r, g, b = ImageColor.getrgb(color)
    return (
        _srgb_to_linear(r / 255.0),
        _srgb_to_linear(g / 255.0),
        _srgb_to_linear(b / 255.0),
    )
