from bd_materials import finishes
from bd_materials.materials import plastics, wood
from bd_materials.processes import fdm
from build123d import Box, Color, Pos
from ocp_vscode import show

TEAL = (0.0, 0.5, 0.5)
GREY = Color(0.6, 0.6, 0.6)

parts = []
labels = []
for i, (label, material) in enumerate([
    ("oak", wood.oak()),
    ("oak_dyed_teal", wood.oak(finish=finishes.dye(TEAL))),
    ("petg_grey", plastics.petg(color=GREY)),
    ("petg_grey_fdm", plastics.petg(color=GREY, process=fdm())),
]):
    b = Pos(i * 30, 0, 0) * Box(20, 20, 20)
    b.material = material
    b.label = label
    parts.append(b)
    labels.append(label)
    print(f"{label:16} preview color = {material.pbr.interpolate_color()}")

show(*parts, names=labels)
