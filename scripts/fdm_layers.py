"""Procedural FDM layer-line texture set — authoring input for build_bundle.py.

Each extruded layer is a horizontal tube of radius ``R = K * p/2`` stacked with
vertical period ``p`` (the layer height). Neighbouring tubes overlap and
intersect on the plane midway between their centres, so the exposed profile is
the arc ``z(d) = sqrt(R^2 - d^2)`` for ``|d| <= p/2``, where ``d`` is the signed
vertical distance from the bead centreline. Heights are millimetres on a float
field, so the normal map is consistent with the geometry rather than filtered
out of an image. Noise lattices wrap and the layer period divides the image
height, so every map tiles seamlessly in both axes.

Emits the three maps the bundle ships: ``color``, ``normal`` (OpenGL, +Y up),
``roughness``. The color map is neutral grayscale, meant to be tinted by the
material's ``color``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

SIZE = 1024
TILE_MM = 6.4          # physical width of one tile
LAYER_MM = 0.2         # layer height → TILE_MM / LAYER_MM layers per tile
K = 1.2                # bead radius / (layer/2): 1 = sharp valleys, higher = flatter
WOBBLE_MM = 0.012      # vertical meander of the bead centreline
LAYER_VAR = 0.15       # per-layer over/under-extrusion banding
GRAIN_MM = 0.0045      # surface micro-texture (drag marks scale from this at 1.5x)
# Zits / retraction blobs, off by default: pressure advance has largely removed
# them from modern prints, and being the only feature large enough to recognize
# they made the 6.4 mm tile repeat visible. Raise BLOBS to bring them back.
BLOBS = 0
BLOB_MM = 0.35
BLOB_H = 0.02
NORMAL_STRENGTH = 1.0
SEED = 7
TINT_BASE, TINT_SPAN = 0.78, 0.16     # crowns lighter than valleys
ROUGH_BASE, ROUGH_SPAN = 0.50, 0.30   # ironed crowns glossier than valleys


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _noise(h, w, gy, gx, rng):
    """Bilinear value noise on a wrapping lattice."""
    lat = rng.random((gy, gx))
    ys, xs = np.arange(h) * (gy / h), np.arange(w) * (gx / w)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    fy, fx = _fade(ys - y0)[:, None], _fade(xs - x0)[None, :]
    y0, x0 = y0 % gy, x0 % gx
    y1, x1 = (y0 + 1) % gy, (x0 + 1) % gx
    top = lat[np.ix_(y0, x0)] * (1 - fx) + lat[np.ix_(y0, x1)] * fx
    bot = lat[np.ix_(y1, x0)] * (1 - fx) + lat[np.ix_(y1, x1)] * fx
    return top * (1 - fy) + bot * fy


def _fbm(h, w, gy, gx, octaves, rng, gain=0.5):
    out, amp, norm = np.zeros((h, w)), 1.0, 0.0
    for i in range(octaves):
        out += amp * _noise(h, w, gy * 2**i, gx * 2**i, rng)
        norm += amp
        amp *= gain
    return out / norm


def _fbm_1d(w, g, rng, octaves=3, gain=0.5):
    out, amp, norm = np.zeros(w), 1.0, 0.0
    for i in range(octaves):
        gg = g * 2**i
        lat = rng.random(gg)
        xs = np.arange(w) * (gg / w)
        x0 = np.floor(xs).astype(int)
        fx = _fade(xs - x0)
        x0 %= gg
        out += amp * (lat[x0] * (1 - fx) + lat[(x0 + 1) % gg] * fx)
        norm += amp
        amp *= gain
    return out / norm


def _wrap(a, period):
    """Minimal-image signed difference, for wrap-aware blob distances."""
    return (a + period / 2) % period - period / 2


def _height(rng, size, tile_mm, layer_mm, k, grain_mm):
    px_mm = tile_mm / size
    period = layer_mm / px_mm
    if abs(round(size / period) - size / period) > 1e-6:
        raise ValueError(
            f"layer period {period:.3f}px does not divide {size}px evenly — "
            f"pick a tile size / layer height that gives an integer layer count"
        )
    r = k * layer_mm / 2
    x, row = np.arange(size)[None, :], np.arange(size)[:, None]
    y_up = (size - 1 - row) * px_mm

    wobble = ((_fbm_1d(size, 4, rng, octaves=4) - 0.5) * 2 * WOBBLE_MM)[None, :]
    d = ((y_up - wobble) % layer_mm) - layer_mm / 2
    n_layers = int(round(tile_mm / layer_mm))
    layer_idx = np.floor((y_up - wobble) / layer_mm).astype(int)
    amp = (1.0 + (rng.random(n_layers) - 0.5) * 2 * LAYER_VAR)[layer_idx % n_layers]

    z_min = np.sqrt(max(r**2 - (layer_mm / 2) ** 2, 0.0))
    z = (np.sqrt(np.maximum(r**2 - d**2, 0.0)) - z_min) * amp
    z = z + (_fbm(size, size, 24, 24, 4, rng) - 0.5) * 2 * grain_mm
    z = z + (_fbm(size, size, 96, 6, 3, rng) - 0.5) * 2 * grain_mm * 1.5  # drag marks

    sigma = BLOB_MM / 2.5 / px_mm
    for _ in range(BLOBS):
        dx = _wrap(x - rng.integers(0, size), size)
        dy = _wrap(row - rng.integers(0, size), size)
        z = z + BLOB_H * np.exp(-(dx**2 + dy**2) / (2 * sigma**2))
    return z, px_mm


def _u8(a):
    return np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)


def generate(
    out_dir: Path,
    size: int = SIZE,
    tile_mm: float = TILE_MM,
    layer_mm: float = LAYER_MM,
    seed: int = SEED,
    k: float = K,
    grain_mm: float = GRAIN_MM,
    rough: tuple[float, float] = (ROUGH_BASE, ROUGH_SPAN),
    tint: tuple[float, float] = (TINT_BASE, TINT_SPAN),
) -> dict[str, str]:
    """Write color/normal/roughness PNGs into *out_dir*; return {field: filename}."""
    rng = np.random.default_rng(seed)
    z, px_mm = _height(rng, size, tile_mm, layer_mm, k, grain_mm)
    hn = (z - z.min()) / (z.max() - z.min())
    out_dir.mkdir(parents=True, exist_ok=True)

    # central differences on a periodic field → tangent-space normal (OpenGL).
    # rows increase downward, so d/d(y_up) = -d/d(row).
    nx = -(np.roll(z, -1, 1) - np.roll(z, 1, 1)) / (2 * px_mm) * NORMAL_STRENGTH
    ny = (np.roll(z, -1, 0) - np.roll(z, 1, 0)) / (2 * px_mm) * NORMAL_STRENGTH
    inv = 1.0 / np.sqrt(nx**2 + ny**2 + 1.0)
    normal = np.stack([_u8(c * inv * 0.5 + 0.5) for c in (nx, ny)]
                      + [_u8(inv * 0.5 + 0.5)], axis=-1)
    Image.fromarray(normal, "RGB").save(out_dir / "normal.png")

    r = rough[0] + rough[1] * (1.0 - hn)
    r = r + (_fbm(size, size, 32, 32, 3, rng) - 0.5) * 0.10
    Image.fromarray(_u8(np.clip(r, 0, 1)), "L").save(out_dir / "roughness.png")

    t = tint[0] + tint[1] * hn
    t = _u8(np.clip(t + (_fbm(size, size, 16, 16, 3, rng) - 0.5) * 0.05, 0, 1))
    Image.fromarray(np.stack([t] * 3, axis=-1), "RGB").save(out_dir / "color.png")

    return {f: f"{f}.png" for f in ("color", "normal", "roughness")}
