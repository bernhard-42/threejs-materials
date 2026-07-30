"""Procedural FDM layer-line generator (scripts/fdm_layers.py).

Guards the two properties the bundled `plastic_fdm` maps depend on: the tile
wraps seamlessly in both axes, and the layer period lands on an integer pixel
count (a fractional period would drift the phase across the wrap).
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fdm_layers  # noqa: E402

SIZE = 256  # px_mm = 0.025 → 8 px per 0.2 mm layer, 16 px per 0.4 mm skin line

# The two shipped parameter sets: wall layer lines and the flatter 45° top skin.
VARIANTS = {
    "wall": {},
    "skin": {"layer_mm": 0.4, "k": 8, "grain_mm": 0.0025},
}


@pytest.fixture(scope="module", params=VARIANTS.values(), ids=VARIANTS)
def texture(request, tmp_path_factory):
    """(maps, pixel period of one bead) for one parameter set."""
    out = tmp_path_factory.mktemp("fdm")
    files = fdm_layers.generate(out, size=SIZE, **request.param)
    maps = {f: np.asarray(Image.open(out / n), dtype=float) for f, n in files.items()}
    layer_mm = request.param.get("layer_mm", fdm_layers.LAYER_MM)
    return maps, round(layer_mm / (fdm_layers.TILE_MM / SIZE))


def test_emits_the_three_bundled_maps(texture):
    maps, _ = texture
    assert set(maps) == {"color", "normal", "roughness"}
    assert all(a.shape[:2] == (SIZE, SIZE) for a in maps.values())


def test_tiles_seamlessly_in_both_axes(texture):
    """The V wrap lands mid-flank on a bead, where row-to-row steps are steepest
    anyway, so it is compared against the interior steps at the *same* bead phase
    rather than against the average. A real phase break would jump by most of the
    bead height — an order of magnitude past this bound, not a few percent."""
    maps, period = texture
    rows = [r for r in range(SIZE - 1) if (SIZE - 1 - r) % period == 0]
    for field, a in maps.items():
        same_phase = max(np.abs(a[r + 1] - a[r]).mean() for r in rows)
        interior_u = np.abs(np.diff(a, axis=1)).mean(axis=tuple(range(1, a.ndim)))
        assert np.abs(a[0] - a[-1]).mean() <= 1.5 * same_phase, field
        assert np.abs(a[:, 0] - a[:, -1]).mean() <= 1.5 * interior_u.max(), field


def test_layer_period_is_an_integer_pixel_count(texture):
    """Autocorrelation of the row profile peaks exactly on every multiple of the
    bead period. Ranking lags by height would not do: the shallow skin profile
    correlates smoothly, so its peaks have shoulders nearly as tall as the peak.
    A fractional period instead walks the peak off the grid, further with each
    multiple — which is what this catches."""
    maps, period = texture
    profile = maps["normal"][..., 1].mean(axis=1)
    profile = profile - profile.mean()
    ac = np.correlate(np.r_[profile, profile], profile, "valid")[:SIZE]
    half = period // 2
    for m in range(period, SIZE // 2, period):
        window = ac[m - half : m + half + 1]
        assert int(np.argmax(window)) == half, f"peak off-grid at lag {m}"


def test_skin_relief_is_shallower_than_a_wall(tmp_path):
    """k flattens the bead: the skin sits on a flat substrate, so its normals
    deviate far less from straight up than a free-hanging wall bead's."""
    spans = {}
    for name, kw in VARIANTS.items():
        out = tmp_path / name
        fdm_layers.generate(out, size=SIZE, **kw)
        g = np.asarray(Image.open(out / "normal.png"), dtype=float)[..., 1]
        spans[name] = g.max() - g.min()
    assert spans["skin"] < spans["wall"] / 2


def test_non_dividing_layer_height_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not divide"):
        fdm_layers.generate(tmp_path, size=SIZE, layer_mm=0.15)


def test_color_map_is_neutral_grayscale(texture):
    """`color` multiplies the map, so any hue in it would fight the tint."""
    c = texture[0]["color"]
    assert np.array_equal(c[..., 0], c[..., 1]) and np.array_equal(c[..., 1], c[..., 2])
