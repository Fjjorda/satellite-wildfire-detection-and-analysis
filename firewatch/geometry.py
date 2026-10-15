"""Polígonos, perímetro, superficie y exportación GeoJSON."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class GeoRef:
    """Georreferencia opcional.

    - pixel_size_m: tamaño del píxel en metros (Sentinel-2 nativo: 10 m; SWIR 20 m).
    - bbox: (lon_min, lat_min, lon_max, lat_max) en WGS84 de la imagen completa,
      como el que muestra Copernicus Browser al exportar. Con bbox se asume una
      proyección geográfica lineal (válido para escenas de decenas de km).
    """
    pixel_size_m: float | None = None
    bbox: tuple[float, float, float, float] | None = None

    def resolve(self, shape: tuple[int, int]) -> "GeoRef":
        if self.pixel_size_m is None and self.bbox is not None:
            h, w = shape
            lon0, lat0, lon1, lat1 = self.bbox
            lat_c = math.radians((lat0 + lat1) / 2)
            mx = abs(lon1 - lon0) * 111_320 * math.cos(lat_c) / w
            my = abs(lat1 - lat0) * 110_574 / h
            return GeoRef(pixel_size_m=(mx + my) / 2, bbox=self.bbox)
        return self

    def to_lonlat(self, x: float, y: float, shape: tuple[int, int]):
        if self.bbox is None:
            return None
        h, w = shape
        lon0, lat0, lon1, lat1 = self.bbox
        return (lon0 + (x / w) * (lon1 - lon0), lat1 - (y / h) * (lat1 - lat0))


def mask_polygons(mask: np.ndarray, simplify_px: float = 1.0):
    """Contornos de una máscara binaria como lista de polígonos con agujeros.

    Returns: lista de {"outer": Nx2 array, "holes": [Mx2 arrays]} en coordenadas de píxel (x, y).
    """
    m = mask.astype(np.uint8)
    if not m.any():
        return []
    res = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    contours, hierarchy = res if len(res) == 2 else res[1:]
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]
    polys = []
    for i, c in enumerate(contours):
        if hierarchy[i][3] != -1:
            continue  # es un agujero; se procesa con su padre
        outer = cv2.approxPolyDP(c, simplify_px, True)[:, 0, :]
        holes = []
        child = hierarchy[i][2]
        while child != -1:
            hc = cv2.approxPolyDP(contours[child], simplify_px, True)[:, 0, :]
            if len(hc) >= 3:
                holes.append(hc)
            child = hierarchy[child][0]
        if len(outer) >= 3:
            polys.append({"outer": outer, "holes": holes})
    return polys


def polygon_length(pts: np.ndarray) -> float:
    d = np.diff(np.vstack([pts, pts[:1]]).astype(float), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def to_geojson(burn_polys, fire_clusters, shape, geo: GeoRef, props: dict, smoke_polys=None,
               front_segments=None) -> dict:
    """GeoJSON con el área quemada y los focos.

    Sin bbox, las coordenadas son de píxel (x hacia la derecha, y hacia abajo)
    y se indica en las propiedades.
    """
    def tx(pt):
        ll = geo.to_lonlat(float(pt[0]), float(pt[1]), shape)
        return list(ll) if ll else [float(pt[0]), float(pt[1])]

    def ring(arr):
        coords = [tx(p) for p in arr]
        coords.append(coords[0])
        return coords

    features = []
    for k, poly in enumerate(burn_polys):
        features.append({
            "type": "Feature",
            "properties": {"kind": "area_quemada", "id": k + 1},
            "geometry": {"type": "Polygon", "coordinates": [ring(poly["outer"])] + [ring(h) for h in poly["holes"]]},
        })
    for k, poly in enumerate(smoke_polys or []):
        features.append({
            "type": "Feature",
            "properties": {"kind": "area_quemada_probable_bajo_humo", "id": k + 1},
            "geometry": {"type": "Polygon", "coordinates": [ring(poly["outer"])] + [ring(h) for h in poly["holes"]]},
        })
    for k, seg in enumerate(front_segments or []):
        props_seg = {"kind": "frente_activo", "id": k + 1, "longitud_px": round(seg.length_px, 1),
                     "a_favor_del_humo": bool(seg.downwind)}
        if geo.pixel_size_m:
            props_seg["longitud_km"] = round(seg.length_px * geo.pixel_size_m / 1000, 3)
        features.append({
            "type": "Feature",
            "properties": props_seg,
            "geometry": {"type": "LineString", "coordinates": [tx(p) for p in seg.points]},
        })
    for c in fire_clusters:
        features.append({
            "type": "Feature",
            "properties": {"kind": "foco_activo", "id": c.id, "confianza": c.confidence,
                           "intensidad": c.intensity, "ubicacion": c.location, "pixeles": c.n_pixels},
            "geometry": {"type": "Point", "coordinates": tx(c.centroid_xy)},
        })
    return {
        "type": "FeatureCollection",
        "properties": {**props, "crs": "EPSG:4326" if geo.bbox else "pixel (x, y)"},
        "features": features,
    }
