# Typewriter Grid Builder for QGIS
# Rotated bounding box (user angle, defaults to map canvas rotation)
# Typewriter ratios: 10 cols/in, 6 rows/in
# Styles mostly_overlap: True=#b2df8a, False=#a6cee3

from qgis.PyQt.QtWidgets import QInputDialog
from qgis.core import (
    QgsProject, QgsWkbTypes, QgsVectorLayer, QgsField,
    QgsFeature, QgsGeometry, QgsSpatialIndex, QgsFeatureRequest,
    QgsPointXY,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsFillSymbol
)
from PyQt5.QtCore import QVariant
from math import cos, sin, radians

# --- 1) Active polygon layer ---
layer = iface.activeLayer()
if layer is None:
    raise Exception("Please select (activate) a polygon layer in the Layers panel.")
if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
    raise Exception("Active layer must be a polygon layer.")

crs = layer.crs()

# --- 2) Inputs ---
width_in, ok = QInputDialog.getDouble(
    iface.mainWindow(),
    "Typewriter Map Width",
    "Map width on paper (in):",
    decimals=2, min=1.0, max=1000.0, value=6.0
)
if not ok:
    raise Exception("Canceled.")

thresh, ok = QInputDialog.getDouble(
    iface.mainWindow(),
    "Mostly-Overlap Threshold",
    "Minimum overlap to mark True (0–1):",
    decimals=2, min=0.0, max=1.0, value=0.50
)
if not ok:
    raise Exception("Canceled.")

default_rot = float(iface.mapCanvas().rotation())  # degrees
angle_deg, ok = QInputDialog.getDouble(
    iface.mainWindow(),
    "Grid Rotation",
    "Rotation angle for the grid (degrees).\n"
    "Tip: This defaults to your current map view rotation.",
    decimals=2, min=-180.0, max=180.0, value=default_rot
)
if not ok:
    raise Exception("Canceled.")

# --- 3) Typewriter ratios (authoritative) ---
cols_per_in = 10.0
rows_per_in = 6.0
aspect = cols_per_in / rows_per_in  # cell_h = cell_w * (10/6)

cols = max(1, int(round(width_in * cols_per_in)))

# --- 4) Union geometry ---
geoms = []
for feat in layer.getFeatures():
    g = feat.geometry()
    if g and not g.isEmpty():
        geoms.append(g)
if not geoms:
    raise Exception("Layer has no valid geometries.")

union_geom = QgsGeometry.unaryUnion(geoms)
if not union_geom or union_geom.isEmpty():
    raise Exception("Failed to build union geometry.")

# Center point for projections (stable reference)
bb = union_geom.boundingBox()
cx = (bb.xMinimum() + bb.xMaximum()) / 2.0
cy = (bb.yMinimum() + bb.yMaximum()) / 2.0

# Rotated basis vectors (u = "width axis", v = "height axis")
# u points along the grid columns direction, v points along rows direction
theta = radians(angle_deg)
ux, uy = cos(theta), sin(theta)
vx, vy = -sin(theta), cos(theta)

# Project all vertices into (u,v) coordinates to get rotated bbox extents
min_u = float("inf")
max_u = float("-inf")
min_v = float("inf")
max_v = float("-inf")

for pt in union_geom.vertices():
    dx = pt.x() - cx
    dy = pt.y() - cy
    u = dx * ux + dy * uy
    v = dx * vx + dy * vy
    if u < min_u: min_u = u
    if u > max_u: max_u = u
    if v < min_v: min_v = v
    if v > max_v: max_v = v

bbox_w = max_u - min_u
bbox_h = max_v - min_v
if bbox_w <= 0 or bbox_h <= 0:
    raise Exception("Degenerate rotated bounding box.")

# Cell sizes in map units
cell_w = bbox_w / float(cols)
cell_h = cell_w * aspect

rows = int(bbox_h // cell_h)
if rows < 1:
    rows = 1

usable_h = rows * cell_h  # trims to full rows so the grid stays inside the bbox

# --- 5) Grid layer ---
grid_lyr = QgsVectorLayer(f"Polygon?crs={crs.authid()}", "Typewriter Grid", "memory")
prov = grid_lyr.dataProvider()
prov.addAttributes([
    QgsField("row", QVariant.Int),
    QgsField("col", QVariant.Int),
    QgsField("mostly_overlap", QVariant.Bool),
    QgsField("overlap_ratio", QVariant.Double)
])
grid_lyr.updateFields()

# --- 6) Spatial index for overlap checks ---
index = QgsSpatialIndex(layer.getFeatures())

# Helper: convert (u,v) back to XY
def uv_to_xy(u, v):
    return (cx + ux * u + vx * v, cy + uy * u + vy * v)

# --- 7) Build cells and compute overlap ---
features_to_add = []
cell_area = cell_w * cell_h

for r in range(rows):
    v0 = min_v + r * cell_h
    v1 = v0 + cell_h
    for c in range(cols):
        u0 = min_u + c * cell_w
        u1 = u0 + cell_w

        ax, ay = uv_to_xy(u0, v0)
        bx, by = uv_to_xy(u1, v0)
        cx2, cy2 = uv_to_xy(u1, v1)
        dx, dy = uv_to_xy(u0, v1)

        A = QgsPointXY(ax, ay)
        B = QgsPointXY(bx, by)
        C = QgsPointXY(cx2, cy2)
        D = QgsPointXY(dx, dy)

        cell_geom = QgsGeometry.fromPolygonXY([[A, B, C, D, A]])

        cand_ids = index.intersects(cell_geom.boundingBox())
        overlap_area = 0.0
        if cand_ids:
            req = QgsFeatureRequest().setFilterFids(cand_ids)
            for feat in layer.getFeatures(req):
                g = feat.geometry()
                if not g or g.isEmpty():
                    continue
                inter = cell_geom.intersection(g)
                if not inter.isEmpty():
                    overlap_area += inter.area()
                    if cell_area > 0 and (overlap_area / cell_area) >= thresh:
                        break

        ratio = 0.0 if cell_area == 0 else min(1.0, overlap_area / cell_area)
        mostly = ratio >= thresh

        f = QgsFeature(grid_lyr.fields())
        f.setGeometry(cell_geom)
        f["row"] = r + 1
        f["col"] = c + 1
        f["mostly_overlap"] = mostly
        f["overlap_ratio"] = round(ratio, 4)
        features_to_add.append(f)

prov.addFeatures(features_to_add)
grid_lyr.updateExtents()

# --- 8) Style grid by mostly_overlap ---
true_symbol = QgsFillSymbol.createSimple({
    "color": "#b2df8a",
    "outline_color": "0,0,0,80",
    "outline_width": "0.1"
})
false_symbol = QgsFillSymbol.createSimple({
    "color": "#a6cee3",
    "outline_color": "0,0,0,80",
    "outline_width": "0.1"
})

renderer = QgsCategorizedSymbolRenderer("mostly_overlap", [
    QgsRendererCategory(True, true_symbol, "True"),
    QgsRendererCategory(False, false_symbol, "False")
])
grid_lyr.setRenderer(renderer)
grid_lyr.triggerRepaint()

QgsProject.instance().addMapLayer(grid_lyr)

est_w_in = cols / cols_per_in
est_h_in = rows / rows_per_in
print(
    f"Grid created (rotated bbox at {angle_deg:.2f}°): "
    f"{cols} columns x {rows} rows. "
    f"Estimated print size: {est_w_in:.2f} in × {est_h_in:.2f} in."
)
