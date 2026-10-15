"""Reto avanzado: detección de focos de fuego activo.

Física: las llamas (800-1200 K) emiten muchísima radiación a 2,2 µm (B12) y,
si son intensas, también a 1,6 µm (B11). En la composición SWIR (R=B12)
eso aparece como canal rojo saturado, muy por encima del verde (B8A).
Ninguna superficie natural "fría" refleja tanto en B12 sin subir también B8A.

Después, cada candidato se valida por contexto espacial: el fuego vive en el
borde del área quemada (el frente) o, como rescoldo, dentro de ella.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

from .config import FireParams
from .features import channels, saturation
from .morph import dilate, disk


@dataclass
class FireCluster:
    id: int
    centroid_xy: tuple[float, float]
    bbox_xywh: tuple[int, int, int, int]
    n_pixels: int
    peak_red: float
    mean_ratio: float          # R/G medio: proxy de intensidad
    intensity: str             # "baja" | "media" | "alta"
    location: str              # "frente" | "interior" | "aislado"
    near_smoke: bool
    confidence: str            # "alta" | "media" | "baja"
    reasons: list = field(default_factory=list)
    contrast: float = 0.0      # rojo medio del foco menos el de su entorno inmediato


@dataclass
class FireResult:
    candidates: np.ndarray          # píxeles calientes (antes del contexto)
    accepted: np.ndarray            # píxeles de focos aceptados
    clusters: list                  # todos los focos, aceptados y descartados
    edge_ring: np.ndarray


def hot_pixels(swir: np.ndarray, valid: np.ndarray, cloud: np.ndarray, p: FireParams,
               source: str = "swir") -> np.ndarray:
    """Píxeles con firma de llama en SWIR. Dos firmas complementarias:

    A) rojo dominante (B12 >> B8A): llama moderada.
    B) amarillo/naranja muy brillante con B bajo: llama intensa, que también emite en B8A y por eso
       no cumple R/G alto. Como el suelo brillante también es claro en SWIR, aquí se exige que el
       píxel destaque mucho de la mediana REGIONAL y que la mancha sea pequeña.
    """
    r, g, b = channels(swir)
    if source == "truecolor":
        return _hot_pixels_tc(swir, valid, cloud, p)
    a = (r >= p.min_red) & (r / (g + 1.0) >= p.min_red_green_ratio) & ((r - b) >= p.min_red_minus_blue)
    a &= saturation(swir) >= p.min_sat
    h, w = r.shape
    win = max(15, int(round(p.hot_window_frac * float(np.hypot(h, w)))) | 1)
    bsig = (r >= p.hot_min_red) & ((r - b) >= p.hot_min_red_minus_blue) & (saturation(swir) >= p.hot_min_sat)
    bsig &= valid & ~cloud
    if bsig.any():
        regional = ndi.median_filter(r[::2, ::2].astype(np.float32), size=max(3, win // 2))
        regional = np.repeat(np.repeat(regional, 2, axis=0), 2, axis=1)[:h, :w]
        bsig &= (r - regional) >= p.hot_local_contrast
        lab, n = ndi.label(bsig, structure=np.ones((3, 3)))
        if n:
            sizes = np.bincount(lab.ravel(), minlength=n + 1)
            keep = (sizes >= 2) & (sizes <= max(30, p.hot_max_blob_frac * h * w))   # >=2 px: un píxel suelto es ruido
            keep[0] = False
            bsig = keep[lab]
    m = (a | bsig) & valid & ~cloud
    return m


def _hot_pixels_tc(tc: np.ndarray, valid: np.ndarray, cloud: np.ndarray, p: FireParams) -> np.ndarray:
    """Llamas visibles en True Color: píxeles naranja/amarillo muy brillantes que destacan de su entorno.

    En visible solo se ve una llama muy intensa, así que es la firma B (contraste regional alto, mancha
    pequeña, >= 2 px) más un rojo claramente por encima del verde para no confundirla con suelo claro.
    """
    r, g, b = channels(tc)
    h, w = r.shape
    m = ((r >= p.hot_min_red) & ((r - b) >= p.hot_min_red_minus_blue) & (saturation(tc) >= p.hot_min_sat)
         & (r / (g + 1.0) >= p.tc_hot_min_red_green_ratio) & valid & ~cloud)
    if not m.any():
        return m
    win = max(15, int(round(p.hot_window_frac * float(np.hypot(h, w)))) | 1)
    regional = ndi.median_filter(r[::2, ::2].astype(np.float32), size=max(3, win // 2))
    regional = np.repeat(np.repeat(regional, 2, axis=0), 2, axis=1)[:h, :w]
    m &= (r - regional) >= p.tc_hot_local_contrast
    lab, n = ndi.label(m, structure=np.ones((3, 3)))
    if n:
        sizes = np.bincount(lab.ravel(), minlength=n + 1)
        keep = (sizes >= 2) & (sizes <= max(30, p.hot_max_blob_frac * h * w))
        keep[0] = False
        m = keep[lab]
    return m


def _intensity(ratio: float, peak: float) -> str:
    if ratio >= 2.5 and peak >= 245:
        return "alta"
    if ratio >= 1.8:
        return "media"
    return "baja"


def detect_fire(swir: np.ndarray, valid: np.ndarray, cloud: np.ndarray, footprint: np.ndarray,
                smoke: np.ndarray | None, p: FireParams, source: str = "swir") -> FireResult:
    """Detecta focos SOLO a partir de la composición de entrada (SWIR o True Color) y los valida por contexto.

    source="truecolor": las llamas casi no se ven en visible; la confianza máxima de un foco es "media".
    """
    h, w = swir.shape[:2]
    diag = float(np.hypot(h, w))
    cand = hot_pixels(swir, valid, cloud, p, source)

    ring_px = max(p.edge_ring_min_px, int(round(p.edge_ring_frac * diag)))
    if footprint.any():
        dist_out = ndi.distance_transform_edt(~footprint)   # distancia desde fuera hasta el quemado
        dist_in = ndi.distance_transform_edt(footprint)     # distancia desde dentro hasta el borde
        edge_ring = (dist_out <= ring_px) & (dist_in <= ring_px)
    else:
        dist_out = np.full((h, w), np.inf)
        dist_in = np.zeros((h, w))
        edge_ring = np.zeros((h, w), dtype=bool)

    if smoke is not None and smoke.any():
        smoke_near = ndi.distance_transform_edt(~smoke) <= max(3, p.smoke_near_frac * diag)
    else:
        smoke_near = np.zeros((h, w), dtype=bool)

    merge_px = max(p.cluster_merge_min_px, int(round(p.cluster_merge_frac * diag)))
    grouped = dilate(cand, merge_px)

    # Polígonos quemados principales vs. secundarios (pequeños respecto al mayor)
    main_burn = footprint
    if footprint.any():
        blab, bn = ndi.label(footprint, structure=np.ones((3, 3)))
        bsizes = np.bincount(blab.ravel()); bsizes[0] = 0
        main_burn = bsizes[blab] >= p.secondary_frac * bsizes.max()
        main_burn &= footprint
        dist_main = ndi.distance_transform_edt(~main_burn)
    else:
        dist_main = np.full((h, w), np.inf)
    lab, n = ndi.label(grouped, structure=np.ones((3, 3)))
    lab = np.where(cand, lab, 0)

    r, g, _ = channels(swir)
    ratio = r / (g + 1.0)
    from .morph import disk as _disk
    ring_se = _disk(p.context_ring_px)
    pad = p.context_ring_px + 1
    clusters = []
    accepted = np.zeros((h, w), dtype=bool)
    objs = ndi.find_objects(lab)
    cid = 0
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        sub = lab[sl] == i
        if not sub.any():
            continue
        ys, xs = np.nonzero(sub)
        ys = ys + sl[0].start
        xs = xs + sl[1].start
        cid += 1
        on_edge = bool(edge_ring[ys, xs].any())
        inside = bool(footprint[ys, xs].mean() > 0.5) if footprint.any() else False
        near_burn = float(dist_out[ys, xs].min()) <= ring_px
        nsmoke = bool(smoke_near[ys, xs].any())
        peak = float(r[ys, xs].max())
        mratio = float(ratio[ys, xs].mean())
        reasons = []
        near_main = float(dist_main[ys, xs].min()) <= ring_px
        if on_edge or near_burn:
            location = "frente"
            reasons.append("en el borde del área quemada")
            conf = "alta" if near_main else "media"
            if not near_main:
                reasons.append("junto a un polígono quemado pequeño y separado")
        elif inside:
            location, conf = "interior", "media"
            reasons.append("dentro del área quemada (posible rescoldo)")
        else:
            location = "aislado"
            reasons.append("lejos de cualquier área quemada")
            # Un punto caliente aislado solo se acepta si es muy intenso y está junto a humo
            conf = "media" if (nsmoke and mratio >= 2.5) else "baja"
        if nsmoke:
            reasons.append("junto a humo")
            if conf == "media" and location == "interior":
                conf = "alta"
        # Contraste local: rojo del foco frente a su anillo (excluyendo otros candidatos)
        y0, y1 = max(0, sl[0].start - pad), min(h, sl[0].stop + pad)
        x0, x1 = max(0, sl[1].start - pad), min(w, sl[1].stop + pad)
        local = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        local[ys - y0, xs - x0] = True
        ring = ndi.binary_dilation(local, structure=ring_se) & ~local & ~cand[y0:y1, x0:x1] & valid[y0:y1, x0:x1]
        bg = float(np.median(r[y0:y1, x0:x1][ring])) if ring.any() else 0.0
        contrast = float(r[ys, xs].mean()) - bg
        if contrast < p.min_contrast:
            conf = "baja"
            reasons.append(f"no destaca de su entorno (contraste {contrast:.0f} < {p.min_contrast:.0f})")
        intensity = _intensity(mratio, peak)
        if source == "truecolor":
            reasons.append("detectado solo en True Color (firma de llama poco fiable)")
            if conf == "alta":
                conf = "media"
        clusters.append(FireCluster(
            id=cid,
            centroid_xy=(float(xs.mean()), float(ys.mean())),
            bbox_xywh=(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)),
            n_pixels=int(len(xs)), peak_red=peak, mean_ratio=round(mratio, 3),
            intensity=intensity, location=location, near_smoke=nsmoke,
            confidence=conf, reasons=reasons, contrast=round(contrast, 1),
        ))
        if conf != "baja":
            accepted[ys, xs] = True
    return FireResult(candidates=cand, accepted=accepted, clusters=clusters, edge_ring=edge_ring)
