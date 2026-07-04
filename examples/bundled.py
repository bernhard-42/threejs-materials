from build123d import *
from ocp_vscode import *
from pathlib import Path

from threejs_materials import PbrProperties

from threejs_materials import wood
from threejs_materials import paper
from threejs_materials import metal
from threejs_materials import coats
from threejs_materials import plastic
from threejs_materials import glass
from threejs_materials import textile

from ocp_vscode import *
from ocp_vscode.utils import create_shader_ball


sb = create_shader_ball("sb")


# %%
m = wood.ash()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.beech()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.birch()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.maple()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.mdf()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.oak()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.osb()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.spruce()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = wood.walnut()
sb.color = m.interpolate_color()
show(sb, materials=[m])


# %%
m = paper.corrugated_cardboard()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = paper.foamboard()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = paper.paper()
sb.color = m.interpolate_color()
show(sb, materials=[m])


# %%
m = metal.aluminum()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.aluminum_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.aluminum_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.aluminum_anodized()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.brass()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.brass_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.brass_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.bronze()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.bronze_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.bronze_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.copper()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.copper_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.copper_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.gold()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.gold_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.gold_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.silver()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.silver_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.silver_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.stainless()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.stainless_brushed()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = metal.stainless_matte()
sb.color = m.interpolate_color()
show(sb, materials=[m])


# %%
m = coats.chrome()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = coats.colored_coat_matte(color="orange")
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = coats.colored_coat_gloss(color="orange")
sb.color = m.interpolate_color()
show(sb, materials=[m])


# %%
m = plastic.acrylic(thickness=10)
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = plastic.plastic_clean(color="grey")
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = plastic.plastic_rough(color="grey")
sb.color = m.interpolate_color()
show(sb, materials=[m])


# %%
m = glass.glass(thickness=2)
sb.color = m.interpolate_color()
show(sb, materials=[m])


# %%
m = textile.fabric_fine(color="lightblue")
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = textile.fabric_weave(color="lightblue")
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = textile.felt()
sb.color = m.interpolate_color()
show(sb, materials=[m])
# %%
m = textile.leather()
sb.color = m.interpolate_color()
show(sb, materials=[m])
