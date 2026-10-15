"""Máscaras de veto: agua, humo y nubes.

Son las principales fuentes de falsos positivos. Se calculan antes que el
área quemada y se excluyen explícitamente de ella.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

from .config import CloudParams, SmokeParams, WaterParams
from .features import channels, luma, red_excess, saturation
from .morph import dilate, disk, remove_small


def water_mask(swir: np.ndarray, p: WaterParams, tc: np.ndarray | None = None,
               valid: np.ndarray | None = None) -> np.ndarray:
    """Agua (embalses, ríos, lagos).

    En SWIR el agua absorbe NIR y SWIR => casi negra y SIN dominante roja.
    El quemado también es oscuro, pero conserva R (B12) claramente por encima
    de G y B. Ese es el criterio que evita confundirlos.

    Si hay True Color, se exige además que allí no parezca suelo quemado
    (en True Color el agua es verde/azul oscura, G >= R; lo quemado es gris-marrón, R > G).
    """
    L = luma(swir)
    m = (L < p.max_luma) & ((red_excess(swir) < p.max_red_excess) | (L < p.very_dark_luma))
    if tc is not None:
        r, g, _ = channels(tc)
        m &= (g >= r - 10) | (luma(tc) < 45)
    if valid is not None:
        m &= valid
    m = remove_small(m, p.min_area_px)
    # Huecos diminutos dentro del agua (artefactos JPEG, reflejos) siguen siendo agua
    if m.any():
        holes = ndi.binary_fill_holes(m) & ~m
        m |= holes & ~remove_small(holes, max(50, int(2e-4 * m.size)))
    if p.dilate_px > 0 and m.any():
        m = ndi.binary_dilation(m, structure=disk(p.dilate_px))
    return m


def water_mask_tc(tc: np.ndarray, p: WaterParams, valid: np.ndarray | None = None) -> np.ndarray:
    """Agua SOLO con True Color: oscura y con el azul por encima del rojo.

    El carbón es oscuro pero neutro o rojizo (B <= R) y el bosque oscuro es verde (G > B); el agua
    es la que tira a azul/verde-azulado. Menos fiable que con SWIR (donde el agua es casi negra).
    """
    r, _, b = channels(tc)
    m = (luma(tc) < p.tc_max_luma) & ((b - r) >= p.tc_min_blue_minus_red)
    if valid is not None:
        m &= valid
    m = remove_small(m, p.min_area_px)
    if m.any():
        holes = ndi.binary_fill_holes(m) & ~m
        m |= holes & ~remove_small(holes, max(50, int(2e-4 * m.size)))
    if p.dilate_px > 0 and m.any():
        m = ndi.binary_dilation(m, structure=disk(p.dilate_px))
    return m


def smoke_mask_tc(tc: np.ndarray, p: SmokeParams, valid: np.ndarray | None = None) -> np.ndarray:
    """Humo a partir del True Color: brillante, poco saturado y neutro/azulado.

    El humo dispersa la luz visible (por eso tapa el True Color) pero es casi
    transparente en SWIR. El suelo claro también es brillante, pero tiene
    dominante roja-ocre (R > B); el humo tiene B >= R.
    """
    r, _, b = channels(tc)
    m = (luma(tc) > p.tc_min_luma) & (saturation(tc) < p.tc_max_sat) & ((b - r) > p.tc_min_blue_minus_red)
    if valid is not None:
        m &= valid
    return _clean_large(m, p.min_area_frac)


def smoke_mask_swir(swir: np.ndarray, p: SmokeParams, valid: np.ndarray | None = None) -> np.ndarray:
    """Aproximación del humo cuando solo hay SWIR: velo azul-cian difuso.

    Es menos fiable que la versión True Color; se usa solo como respaldo para
    rebajar la confianza, nunca para borrar área quemada.
    """
    r, g, b = channels(swir)
    s = r + g + b + 1e-3
    m = (b / s > 0.28) & (luma(swir) > 60)
    if valid is not None:
        m &= valid
    return _clean_large(m, p.min_area_frac)


def _clean_large(m: np.ndarray, min_area_frac: float) -> np.ndarray:
    """El humo son manchas grandes y difusas: suavizado fuerte + tamaño mínimo."""
    if not m.any():
        return m
    m = ndi.binary_opening(m, structure=disk(2))
    m = ndi.binary_closing(m, structure=disk(6))
    m = ndi.binary_fill_holes(m)
    return remove_small(m, int(min_area_frac * m.size))


def cloud_mask(swir: np.ndarray, p: CloudParams, valid: np.ndarray | None = None) -> np.ndarray:
    """Nubes: blancas y muy brillantes también en SWIR.

    Distinción clave con el humo: el humo desaparece en SWIR, la nube no.
    Se usan para impedir que una nube brillante se tome como fuego activo.
    """
    m = (luma(swir) > p.min_luma) & (saturation(swir) < p.max_sat)
    if valid is not None:
        m &= valid
    m = remove_small(m, 20)
    if m.any():
        m = ndi.binary_dilation(m, structure=disk(2))
    return m


def classify_smoke(smoke: np.ndarray | None, footprint: np.ndarray | None, p: SmokeParams):
    """Separa el humo en confianza alta y baja. Devuelve (alta, baja, n_baja).

    El humo se mueve en conjunto: un penacho es una masa grande y compacta, normalmente
    pegada al incendio. Se considera de ALTA confianza si, comparado con el humo más
    grande de la escena:
      - es grande (>= main_rel_area), o
      - es mediano (>= near_rel_area) y está junto al área quemada,
    y además es compacto (solidez >= min_solidity). El resto (pequeño, suelto o muy
    fragmentado) pasa a BAJA confianza: posible error de detección.
    """
    if smoke is None or not smoke.any():
        empty = np.zeros_like(smoke) if smoke is not None else None
        return smoke, empty, 0
    h, w = smoke.shape
    lab, n = ndi.label(smoke, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel()); sizes[0] = 0
    biggest = sizes.max()
    near = None
    if footprint is not None and footprint.any():
        r = max(3, int(round(p.near_burn_frac * float(np.hypot(h, w)))))
        near = dilate(footprint, r)
    high = np.zeros(n + 1, dtype=bool)
    objs = ndi.find_objects(lab)
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        rel = sizes[i] / biggest
        if rel < p.near_rel_area:
            continue
        touches = bool(near is not None and near[sl][lab[sl] == i].any())
        if not (rel >= p.main_rel_area or touches):
            continue
        sub = lab[sl] == i
        if _solidity(sub) >= p.min_solidity:
            high[i] = True
    alta = high[lab]
    baja = smoke & ~alta
    return alta, baja, int(ndi.label(baja, structure=np.ones((3, 3)))[1])


def _solidity(sub: np.ndarray) -> float:
    """Área / área de la envolvente convexa (1 = compacto, ~0 = fragmentado o filiforme)."""
    import cv2
    pts = cv2.findNonZero(sub.astype(np.uint8))
    if pts is None:
        return 0.0
    hull = cv2.convexHull(pts)
    ha = cv2.contourArea(hull)
    return float(sub.sum() / ha) if ha > 0 else 1.0
