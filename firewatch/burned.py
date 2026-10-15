"""Reto básico: delimitación del área quemada."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi
from skimage.filters import apply_hysteresis_threshold, threshold_otsu

from .config import BurnParams
from .features import channels, luma, pseudo_nbr, saturation
from .morph import disk, remove_small


def _clip01(x):
    return np.clip(x, 0.0, 1.0)


def _darkness(L: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Oscuridad relativa a la mediana de la propia escena (0.5 = como la mediana).

    Relativa y no absoluta para no depender del brillo de cada exportación.
    """
    ref = float(np.median(L[valid])) if valid.any() else 128.0
    ref = max(ref, 1.0)
    return _clip01((ref - L) / ref + 0.5)


@dataclass
class BurnResult:
    score: np.ndarray        # puntuación continua [0,1] por píxel
    burned: np.ndarray       # píxeles quemados (sin agua ni islas)
    burned_strict: np.ndarray  # versión estricta para el rango de superficie
    footprint: np.ndarray    # huella del incendio: quemado + agua interior + huecos pequeños (para el perímetro)
    islands: np.ndarray      # islas interiores no quemadas (excluidas)
    water_inside: np.ndarray  # agua rodeada por el incendio (dentro del perímetro, fuera del área)
    under_smoke: np.ndarray | None = None  # quemado PROBABLE bajo el humo (estimación, aparte del confirmado)


def burn_score(swir: np.ndarray, valid: np.ndarray, p: BurnParams,
               tc: np.ndarray | None = None, smoke_tc: np.ndarray | None = None) -> np.ndarray:
    """Puntuación de quemado en [0, 1].

    score = c_pnbr * (combinación de oscuridad, saturación y oscuridad en True Color)

    - c_pnbr: condición necesaria. Sin dominante roja en SWIR no hay quemado.
    - oscuridad: lo quemado es más oscuro que la escena (suelos y urbano rojizos son claros).
    - saturación: lo quemado es un rojo "intenso"; urbano/suelo es rosado/pálido.
    - oscuridad True Color: en visible lo quemado es gris-negro; suelo rojizo es ocre claro.
      Bajo humo el True Color no informa, así que allí se usa un valor neutro.
    """
    pn = pseudo_nbr(swir)
    c_pn = _clip01((-pn + p.pnbr_start) / p.pnbr_span)
    c_dark = _darkness(luma(swir), valid)
    c_sat = _clip01((saturation(swir) - p.sat_start) / p.sat_span)
    if tc is not None:
        c_tc = _darkness(luma(tc), valid & ~(smoke_tc if smoke_tc is not None else False))
        if smoke_tc is not None:
            c_tc = np.where(smoke_tc, 0.5, c_tc)
        support = 0.40 * c_dark + 0.30 * c_sat + 0.30 * c_tc
    else:
        support = 0.55 * c_dark + 0.45 * c_sat
    score = c_pn * _clip01(support * 1.25)
    if p.smooth_sigma > 0:
        score = ndi.gaussian_filter(score, p.smooth_sigma)
    score[~valid] = 0.0
    return score.astype(np.float32)


def burn_score_tc(tc: np.ndarray, valid: np.ndarray, p: BurnParams,
                  smoke: np.ndarray | None = None) -> np.ndarray:
    """Puntuación de quemado SOLO con True Color, en [0, 1].

    Sin SWIR no hay firma espectral directa de la cicatriz; se usa lo que sí se ve: el carbón es
    OSCURO (respecto a la propia escena) y NO VERDE (la vegetación sana tiene ExG alto). Es una
    estimación menos fiable: sombras de relieve y bosque denso muy oscuro pueden parecerse.
    """
    r, g, b = channels(tc)
    exg = (2 * g - r - b) / (r + g + b + 1e-3)
    c_nogreen = _clip01((p.tc_green_start - exg) / p.tc_green_span)
    L = luma(tc)
    ok = valid & ~(smoke if smoke is not None else False)
    c_dark = _clip01((_darkness(L, ok) - p.tc_dark_start) / p.tc_dark_span)
    score = c_nogreen * c_dark
    if smoke is not None:
        score = np.where(smoke, 0.0, score)
    if p.smooth_sigma > 0:
        score = ndi.gaussian_filter(score, p.smooth_sigma)
    score[~valid] = 0.0
    return score.astype(np.float32)


def segment_burned(score: np.ndarray, valid: np.ndarray, water: np.ndarray, p: BurnParams) -> BurnResult:
    allowed = valid & ~water
    s = np.where(allowed, score, 0.0)

    # Histéresis: solo crecen regiones que contienen al menos un núcleo muy seguro.
    m = apply_hysteresis_threshold(s, p.hyst_low, p.hyst_high)
    if p.open_radius:
        m = ndi.binary_opening(m, structure=disk(p.open_radius))
    if p.close_radius:
        m = ndi.binary_closing(m, structure=disk(p.close_radius))
    m &= allowed
    m = remove_small(m, int(p.min_area_frac * m.size))

    # Huecos interiores: agua -> dentro del perímetro pero fuera del área;
    # huecos pequeños -> se rellenan; huecos grandes no-agua -> islas no quemadas.
    filled = ndi.binary_fill_holes(m | (water & _near(m, 3)))
    holes = filled & ~m
    lab, n = ndi.label(holes)
    island_min = int(p.island_min_frac * m.size)
    footprint = m.copy()
    islands = np.zeros_like(m)
    water_inside = np.zeros_like(m)
    if n:
        sizes = np.bincount(lab.ravel())
        water_frac = np.bincount(lab.ravel(), weights=water.ravel().astype(float)) / np.maximum(sizes, 1)
        for i in range(1, n + 1):
            region = lab == i
            if water_frac[i] > 0.5:
                footprint |= region
                water_inside |= region & water
                m_fill = region & ~water  # orillas mezcladas: cuentan como quemado
                m |= m_fill
            elif sizes[i] < island_min:
                footprint |= region
                m |= region
            else:
                islands |= region
    min_px = int(p.min_area_frac * m.size)
    footprint = remove_small(footprint, min_px)
    m &= footprint
    water_inside &= footprint
    strict = m & (score >= p.strict_score)
    return BurnResult(score=score, burned=m, burned_strict=strict, footprint=footprint,
                      islands=islands, water_inside=water_inside)


def _near(mask: np.ndarray, r: int) -> np.ndarray:
    return ndi.binary_dilation(mask, structure=disk(r)) if mask.any() else mask


def recover_under_smoke(swir: np.ndarray, smoke: np.ndarray | None, water: np.ndarray, valid: np.ndarray,
                        footprint: np.ndarray, p: BurnParams) -> np.ndarray:
    """Estima el área quemada oculta bajo el humo (devuelve una máscara aparte, "probable").

    Idea: dentro de la zona de humo, la composición SWIR pierde el tono rojizo de lo quemado,
    pero B8A (canal verde) sigue siendo claramente más bajo sobre carbón que sobre vegetación.
    Se separan ambas clases con Otsu SOLO dentro del humo y se conserva lo que:
      1. forma una clase realmente separada (si no hay bimodalidad, se abstiene), y
      2. toca el área quemada confirmada (un incendio es contiguo; evita zonas oscuras sueltas).
    """
    h, w = swir.shape[:2]
    empty = np.zeros((h, w), dtype=bool)
    if smoke is None or not smoke.any() or not footprint.any():
        return empty
    zone = smoke & ~water & valid & ~footprint
    if zone.sum() < 500:
        return empty
    g = ndi.gaussian_filter(swir[..., 1], p.smoke_sigma)
    vals = g[zone]
    if vals.std() < 1e-3:
        return empty
    t = threshold_otsu(vals)
    dark, light = vals[vals <= t], vals[vals > t]
    if len(dark) == 0 or len(light) == 0:
        return empty
    frac = len(dark) / len(vals)
    if (light.mean() - dark.mean()) < p.smoke_min_gap or not (p.smoke_dark_frac_min <= frac <= p.smoke_dark_frac_max):
        return empty
    cand = zone & (g <= t)
    cand = ndi.binary_opening(cand, structure=disk(2))
    cand = ndi.binary_closing(cand, structure=disk(3))
    diag = float(np.hypot(h, w))
    link = ndi.binary_dilation(footprint, structure=disk(max(3, int(round(p.smoke_link_frac * diag)))))
    lab, n = ndi.label(cand)
    if n == 0:
        return empty
    keep = np.unique(lab[link & cand])
    keep = keep[keep > 0]
    sel = np.isin(lab, keep)
    sel = ndi.binary_fill_holes(sel)
    sel = remove_small(sel & ~water & ~footprint, int(p.smoke_min_area_frac * sel.size))
    return sel
