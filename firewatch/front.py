"""Reto avanzado: caracterización del frente activo.

A partir de los focos aceptados y de la huella del incendio:
  - Tramos del PERÍMETRO con actividad (frente activo) frente a perímetro sin actividad,
    como polilíneas con su longitud.
  - Superficie quemada CON actividad (a menos de un radio de un foco) y SIN actividad.
  - Dirección del humo (hacia dónde lo lleva el viento) y qué parte del frente activo está
    en ese lado: el fuego avanza a favor del viento, así que ese tramo es probablemente la
    cabeza del incendio.

Supone imagen orientada al norte (así se exportan desde Copernicus Browser).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage as ndi

from .config import FireParams
from .morph import dilate

COMPASS = ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]


@dataclass
class FrontSegment:
    points: np.ndarray            # (N, 2) píxeles (x, y) de la polilínea
    length_px: float
    downwind: bool                # en el lado hacia el que va el humo (avance probable)


@dataclass
class FrontResult:
    segments: list = field(default_factory=list)
    active_length_px: float = 0.0
    perimeter_length_px: float = 0.0
    active_zone: np.ndarray | None = None      # quemado a menos de `radius_px` de un foco
    radius_px: float = 0.0
    smoke_vector: tuple | None = None          # (dx, dy) unitario en píxeles, y hacia abajo
    smoke_direction: str | None = None         # rumbo hacia el que va el humo ("suroeste"...)
    smoke_origin_xy: tuple | None = None

    @property
    def active_fraction(self) -> float:
        return self.active_length_px / self.perimeter_length_px if self.perimeter_length_px else 0.0

    @property
    def downwind_fraction(self) -> float | None:
        if self.smoke_vector is None or self.active_length_px == 0:
            return None
        return sum(s.length_px for s in self.segments if s.downwind) / self.active_length_px


def compass(dx: float, dy: float) -> str:
    """Rumbo de un vector en imagen (y hacia abajo, norte arriba)."""
    bearing = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    return COMPASS[int(round(bearing / 45)) % 8]


def _smoke_vector(footprint: np.ndarray, smoke: np.ndarray | None, p: FireParams):
    """Vector desde el incendio hasta el centro del penacho principal (humo de confianza alta)."""
    if smoke is None or not smoke.any() or not footprint.any():
        return None, None
    lab, n = ndi.label(smoke, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel()); sizes[0] = 0
    main = lab == int(np.argmax(sizes))
    h, w = footprint.shape
    # El penacho nace en el incendio: origen = parte del quemado más cercana al humo
    near = dilate(main, max(3, 0.02 * math.hypot(h, w))) & footprint & ~main
    src = near if near.any() else footprint
    oy, ox = [float(v.mean()) for v in np.nonzero(src)]
    sy, sx = [float(v.mean()) for v in np.nonzero(main)]
    dx, dy = sx - ox, sy - oy
    norm = math.hypot(dx, dy)
    if norm < p.smoke_dir_min_frac * math.hypot(h, w):
        return None, None
    return (dx / norm, dy / norm), (ox, oy)


def _runs(flags: np.ndarray, max_gap: int):
    """Tramos consecutivos True en una secuencia CIRCULAR, uniendo huecos cortos."""
    n = len(flags)
    if not flags.any():
        return []
    if flags.all():
        return [np.arange(n)]
    # Cerrar huecos cortos
    f = flags.copy()
    start = int(np.argmin(f))           # empezar en un False para no partir un tramo
    order = np.r_[start:n, 0:start]
    seq = f[order]
    i = 0
    while i < n:
        if not seq[i]:
            j = i
            while j < n and not seq[j]:
                j += 1
            if 0 < i and j < n and (j - i) <= max_gap:
                seq[i:j] = True
            i = j
        else:
            i += 1
    runs, i = [], 0
    while i < n:
        if seq[i]:
            j = i
            while j < n and seq[j]:
                j += 1
            runs.append(order[i:j])
            i = j
        else:
            i += 1
    return runs


def _smooth(pts: np.ndarray, win: int = 5) -> np.ndarray:
    """Suaviza la polilínea (el contorno píxel a píxel va en escalera) y la simplifica."""
    if len(pts) <= win:
        return pts.astype(float)
    k = np.ones(win) / win
    xs = np.convolve(pts[:, 0].astype(float), k, mode="valid")
    ys = np.convolve(pts[:, 1].astype(float), k, mode="valid")
    sm = np.column_stack([xs, ys])
    sm = np.vstack([pts[:1], sm, pts[-1:]]).astype(np.float32)
    ap = cv2.approxPolyDP(sm.reshape(-1, 1, 2), 0.7, False)[:, 0, :]
    return ap.astype(float)


def _length(pts: np.ndarray) -> float:
    if len(pts) < 2:
        return 0.0
    d = np.diff(pts.astype(float), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def compute_front(footprint: np.ndarray, accepted: np.ndarray, burned: np.ndarray,
                  smoke: np.ndarray | None, p: FireParams, pixel_size_m: float | None = None) -> FrontResult:
    h, w = footprint.shape
    diag = math.hypot(h, w)
    res = FrontResult()
    if not footprint.any():
        res.active_zone = np.zeros((h, w), dtype=bool)
        return res

    vec, origin = _smoke_vector(footprint, smoke, p)
    if vec is not None:
        res.smoke_vector, res.smoke_origin_xy = vec, origin
        res.smoke_direction = compass(*vec)
    # Normal hacia fuera del borde: -gradiente de la huella suavizada (apunta de quemado a no quemado)
    sm = ndi.gaussian_filter(footprint.astype(np.float32), sigma=max(2.0, 0.004 * diag))
    gy, gx = np.gradient(sm)

    # Distancia de cada píxel al foco aceptado más cercano
    dist_fire = ndi.distance_transform_edt(~accepted) if accepted.any() else np.full((h, w), np.inf)
    near_px = max(p.edge_ring_min_px, p.edge_ring_frac * diag)

    contours = cv2.findContours(footprint.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    contours = contours[0] if len(contours) == 2 else contours[1]
    max_gap = max(2, int(round(p.front_gap_frac * diag)))
    for c in contours:
        pts = c[:, 0, :]
        if len(pts) < 3:
            continue
        closed = np.vstack([pts, pts[:1]])
        res.perimeter_length_px += _length(closed)
        active = dist_fire[pts[:, 1], pts[:, 0]] <= near_px
        for idx in _runs(active, max_gap):
            seg = _smooth(pts[idx])
            L = _length(seg)
            if L < max(4.0, p.front_min_len_frac * diag):
                continue
            downwind = False
            if vec is not None:
                raw = pts[idx]
                mx = -float(gx[raw[:, 1], raw[:, 0]].mean())
                my = -float(gy[raw[:, 1], raw[:, 0]].mean())
                nrm = math.hypot(mx, my)
                downwind = nrm > 0 and (mx * vec[0] + my * vec[1]) / nrm >= p.head_cos
            res.segments.append(FrontSegment(points=seg, length_px=L, downwind=downwind))
            res.active_length_px += L

    # Superficie con actividad: quemado a menos de un radio de un foco
    if pixel_size_m:
        r = p.zone_radius_m / pixel_size_m
    else:
        r = p.zone_radius_frac * diag
    res.radius_px = float(max(1.0, r))
    res.active_zone = burned & (dist_fire <= res.radius_px)
    return res
