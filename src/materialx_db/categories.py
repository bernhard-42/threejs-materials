"""Canonical material category mapping for all sources."""

CANONICAL_CATEGORIES = [
    "metal", "wood", "plastic", "brick", "stone", "concrete",
    "fabric", "leather", "glass", "ceramic", "organic", "terrain",
    "asphalt", "plaster", "other",
]

# ---------- PhysicallyBased ----------

_PB_CATEGORY_MAP = {
    "metal": "metal",
    "plastic": "plastic",
    "crystal": "glass",
    "liquid": "glass",
    "organic": "organic",
    "human": "organic",
    "manmade": "other",
    "stone": "stone",
    "wood": "wood",
    "fabric": "fabric",
    "glass": "glass",
    "ceramic": "ceramic",
    "concrete": "concrete",
    "leather": "leather",
    "soil": "terrain",
    "water": "glass",
}


def categorize_physicallybased(category: str) -> str:
    """Map PhysicallyBased category field to canonical group."""
    return _PB_CATEGORY_MAP.get(category.lower().strip(), "other")


# ---------- PolyHaven ----------

_PH_CATEGORY_MAP = {
    "metal": "metal",
    "wood": "wood",
    "brick": "brick",
    "stone": "stone",
    "concrete": "concrete",
    "fabric": "fabric",
    "cotton": "fabric",
    "wool": "fabric",
    "carpet": "fabric",
    "leather": "leather",
    "glass": "glass",
    "ceramic": "ceramic",
    "tiles": "ceramic",
    "plastic": "plastic",
    "asphalt": "asphalt",
    "road": "asphalt",
    "plaster": "plaster",
    "stucco": "plaster",
    "soil": "terrain",
    "ground": "terrain",
    "grass": "terrain",
    "sand": "terrain",
    "snow": "terrain",
    "rock": "stone",
    "marble": "stone",
    "gravel": "terrain",
    "bark": "wood",
    "food": "organic",
    "organic": "organic",
    "leaf": "organic",
    "moss": "organic",
    "rubber": "plastic",
    "roofing": "other",
    "paint": "plaster",
    "wallpaper": "other",
}


def categorize_polyhaven(categories: list[str]) -> str:
    """Map PolyHaven categories list to canonical group (first match wins)."""
    for cat in categories:
        key = cat.lower().strip()
        if key in _PH_CATEGORY_MAP:
            return _PH_CATEGORY_MAP[key]
    return "other"


# ---------- ambientCG / GPUOpen (name-based inference) ----------

_NAME_PREFIXES = [
    ("wood", "wood"),
    ("bark", "wood"),
    ("planks", "wood"),
    ("parquet", "wood"),
    ("lumber", "wood"),
    ("bamboo", "wood"),
    ("metal", "metal"),
    ("iron", "metal"),
    ("steel", "metal"),
    ("copper", "metal"),
    ("brass", "metal"),
    ("bronze", "metal"),
    ("gold", "metal"),
    ("silver", "metal"),
    ("aluminum", "metal"),
    ("aluminium", "metal"),
    ("titanium", "metal"),
    ("zinc", "metal"),
    ("rust", "metal"),
    ("tin", "metal"),
    ("chrome", "metal"),
    ("nickel", "metal"),
    ("cobalt", "metal"),
    ("plastic", "plastic"),
    ("rubber", "plastic"),
    ("foam", "plastic"),
    ("vinyl", "plastic"),
    ("nylon", "plastic"),
    ("polyester", "plastic"),
    ("polystyrene", "plastic"),
    ("brick", "brick"),
    ("stone", "stone"),
    ("rock", "stone"),
    ("marble", "stone"),
    ("granite", "stone"),
    ("slate", "stone"),
    ("pebble", "stone"),
    ("cobblestone", "stone"),
    ("sandstone", "stone"),
    ("limestone", "stone"),
    ("onyx", "stone"),
    ("travertine", "stone"),
    ("concrete", "concrete"),
    ("cement", "concrete"),
    ("fabric", "fabric"),
    ("cloth", "fabric"),
    ("linen", "fabric"),
    ("cotton", "fabric"),
    ("silk", "fabric"),
    ("wool", "fabric"),
    ("denim", "fabric"),
    ("tweed", "fabric"),
    ("herringbone", "fabric"),
    ("fleece", "fabric"),
    ("velvet", "fabric"),
    ("corduroy", "fabric"),
    ("burlap", "fabric"),
    ("canvas", "fabric"),
    ("gingham", "fabric"),
    ("houndstooth", "fabric"),
    ("kilim", "fabric"),
    ("knit", "fabric"),
    ("weave", "fabric"),
    ("carpet", "fabric"),
    ("rug", "fabric"),
    ("upholstery", "fabric"),
    ("curtain", "fabric"),
    ("leather", "leather"),
    ("suede", "leather"),
    ("glass", "glass"),
    ("crystal", "glass"),
    ("ice", "glass"),
    ("ceramic", "ceramic"),
    ("porcelain", "ceramic"),
    ("pottery", "ceramic"),
    ("tile", "ceramic"),
    ("terracotta", "ceramic"),
    ("asphalt", "asphalt"),
    ("road", "asphalt"),
    ("tarmac", "asphalt"),
    ("plaster", "plaster"),
    ("stucco", "plaster"),
    ("wall", "plaster"),
    ("wallpaper", "other"),
    ("paint", "plaster"),
    ("ground", "terrain"),
    ("grass", "terrain"),
    ("soil", "terrain"),
    ("dirt", "terrain"),
    ("mud", "terrain"),
    ("sand", "terrain"),
    ("gravel", "terrain"),
    ("snow", "terrain"),
    ("leaf", "organic"),
    ("moss", "organic"),
    ("food", "organic"),
    ("chip", "wood"),
    ("oak", "wood"),
    ("pine", "wood"),
    ("walnut", "wood"),
    ("maple", "wood"),
    ("cherry", "wood"),
    ("mahogany", "wood"),
    ("cedar", "wood"),
    ("teak", "wood"),
    ("birch", "wood"),
    ("ash", "wood"),
    ("elm", "wood"),
    ("beech", "wood"),
    ("roof", "other"),
    ("solar", "other"),
    ("tactile", "other"),
]


def categorize_by_name(name: str) -> str:
    """Infer category from material name/title (for ambientCG, GPUOpen)."""
    lower = name.lower().replace("_", " ")
    for prefix, cat in _NAME_PREFIXES:
        if prefix in lower:
            return cat
    return "other"
