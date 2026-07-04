import importlib
from typing import TYPE_CHECKING

from threejs_materials.convert import encode_texture_base64
from threejs_materials.gltf import collect_gltf_textures, inject_materials
from threejs_materials.library import PbrProperties
from threejs_materials.models import (
    Color,
    PbrMaps,
    PbrOverrides,
    PbrValues,
    TextureTransform,
)
from threejs_materials.sources import CACHE_DIR, clear_cache, list_cache
from threejs_materials.utils import texture_average_color

# Bundled material factories live in the pbr_properties/ subpackage; re-export
# them here so `from threejs_materials import metal` works without the subpackage
# in the path. Lazy (via __getattr__) to keep `import threejs_materials` light and
# to avoid a bootstrap cycle when the generator regenerates them. The TYPE_CHECKING
# block lets static analysers resolve the names (autocomplete / go-to-definition).
_CATEGORY_MODULES = ("coats", "glass", "metal", "paper", "plastic", "textile", "wood")

if TYPE_CHECKING:
    from threejs_materials.pbr_properties import (  # noqa: F401
        coats,
        glass,
        metal,
        paper,
        plastic,
        textile,
        wood,
    )


def __getattr__(name: str):
    if name in _CATEGORY_MODULES:
        return importlib.import_module(f"threejs_materials.pbr_properties.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PbrProperties",
    "PbrValues",
    "PbrMaps",
    "PbrOverrides",
    "TextureTransform",
    "Color",
    "CACHE_DIR",
    "encode_texture_base64",
    "collect_gltf_textures",
    "inject_materials",
    "list_cache",
    "clear_cache",
    "texture_average_color",
    *_CATEGORY_MODULES,
]
