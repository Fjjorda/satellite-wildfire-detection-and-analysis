"""Validación de los focos detectados contra una imagen de referencia (composición Wildfire).

IMPORTANTE: la imagen Wildfire NUNCA entra en la detección. El análisis se hace solo con SWIR
(+ True Color) y, después, este módulo decodifica la Wildfire como "verdad terreno" de dónde hay
llamas visibles y compara.

Métricas (a nivel de foco, con tolerancia espacial):
  - recall    = focos reales de la referencia que el modelo encontró / focos reales
  - precision = focos aceptados por el modelo que coinciden con uno real / focos aceptados
  - f1
También se mide el recall de los CANDIDATOS (antes del filtro de contexto) para saber si un fallo
viene del umbral SWIR (no llegó a ser candidato) o del filtro de contexto (se descartó después).
Para cada foco real no encontrado se anota qué ve la imagen de entrada en esa zona, para ajustar con datos.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

from .config import FireParams, ValidationParams
from .features import channels, saturation
from .morph import dilate, disk


def wildfire_hot_pixels(wf: np.ndarray, valid: np.ndarray, tc: np.ndarray | None = None,
                        p: ValidationParams | None = None):
    """Decodifica la composición "Wildfire" (QuickFire, Pierre Markuse, Sentinel Hub).

    El script pinta encima del color natural los píxeles con B12+B11 > {1,0; 1,25; 1,5; 2,0}
    en rojo -> naranja -> amarillo según intensidad. Pero su fondo mezcla una capa con B12, así
    que los suelos muy brillantes (canteras, eriales) también salen salmón/naranja. Lo que el
    suelo nunca tiene es el NÚCLEO AMARILLO de los píxeles más calientes. Por eso:
      - grupo con núcleo amarillo => fuego;
      - fragmento pequeño sin núcleo pegado a un grupo con núcleo => fuego (borde de la llama);
      - resto => descartado (probable suelo brillante).

    Returns: (calientes_validados, nivel 0-3 por píxel, descartados)
    """
    p = p or ValidationParams()
    r, g, b = channels(wf)
    sat = saturation(wf)
    hot = (r >= 200) & (sat >= 0.5) & (r - b >= 100) & valid
    if tc is not None:
        hot &= (r - tc[..., 0]) >= 40          # claramente más rojo que el mismo píxel en True Color
    hue_ratio = g / (r + 1.0)                  # 0 = rojo puro, ~1 = amarillo
    level = np.zeros(r.shape, dtype=np.uint8)
    level[hot & (hue_ratio < 0.45)] = 1
    level[hot & (hue_ratio >= 0.45) & (hue_ratio < 0.75)] = 2
    level[hot & (hue_ratio >= 0.75)] = 3
    if not hot.any():
        return hot, level, np.zeros_like(hot)

    core = hot & (r >= 240) & (g >= 200) & ((g - b) >= 90)
    h, w = hot.shape
    diag = float(np.hypot(h, w))
    grp = ndi.binary_closing(hot, structure=disk(2)) | hot
    lab, n = ndi.label(grp, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    ncore = np.bincount(lab.ravel(), weights=core.ravel().astype(float), minlength=n + 1)
    has_core = ncore >= p.core_min_px
    has_core[0] = False
    keep_lab = has_core.copy()
    if has_core.any():
        dist = ndi.distance_transform_edt(~has_core[lab])
        mind = np.asarray(ndi.minimum(dist, lab, index=np.arange(1, n + 1)))
        max_px = max(20, p.attach_max_frac * h * w)
        attach = (mind <= p.attach_frac * diag) & (sizes[1:] <= max_px)
        keep_lab[1:] |= attach
    validated = keep_lab[lab] & hot
    return validated, level, hot & ~validated


@dataclass
class ValidationResult:
    truth: np.ndarray                     # píxeles de llama reales según la referencia
    truth_rejected: np.ndarray            # naranja de la referencia descartado (probable suelo brillante)
    hits: list = field(default_factory=list)       # focos reales encontrados
    misses: list = field(default_factory=list)     # focos reales NO encontrados (con diagnóstico SWIR)
    false_pos: list = field(default_factory=list)  # focos aceptados sin correspondencia real
    metrics: dict = field(default_factory=dict)
    tol_px: float = 0.0
    truth_groups: np.ndarray | None = None         # etiquetas de focos reales
    pred_tp: np.ndarray | None = None              # píxeles aceptados que coinciden
    pred_fp: np.ndarray | None = None              # píxeles aceptados sin coincidencia


def _safe_div(a: float, b: float) -> float | None:
    return round(a / b, 3) if b else None


def validate_fire(wf: np.ndarray, valid: np.ndarray, tc: np.ndarray | None, swir: np.ndarray,
                  fire, p: ValidationParams | None = None, fp: FireParams | None = None) -> ValidationResult:
    """Compara los focos detectados con SWIR con los de la referencia Wildfire."""
    p = p or ValidationParams()
    h, w = valid.shape
    diag = math.hypot(h, w)
    truth, _level, rejected = wildfire_hot_pixels(wf, valid, tc, p)
    tol = max(float(p.tol_min_px), p.tol_frac * diag)
    merge = max(2, int(round(p.merge_frac * diag)))

    # Focos reales: píxeles de referencia agrupados
    tlab, tn = ndi.label(dilate(truth, merge), structure=np.ones((3, 3)))
    tlab = np.where(truth, tlab, 0)
    ids = np.unique(tlab[tlab > 0])
    pred = fire.accepted
    cand = fire.candidates
    d_pred = ndi.distance_transform_edt(~pred) if pred.any() else np.full((h, w), np.inf)
    d_cand = ndi.distance_transform_edt(~cand) if cand.any() else np.full((h, w), np.inf)
    d_truth = ndi.distance_transform_edt(~truth) if truth.any() else np.full((h, w), np.inf)

    res = ValidationResult(truth=truth, truth_rejected=rejected, tol_px=tol, truth_groups=tlab)
    r, g, b = channels(swir)
    for i in ids:
        ys, xs = np.nonzero(tlab == i)
        info = {"centroide_xy": [round(float(xs.mean()), 1), round(float(ys.mean()), 1)],
                "pixeles": int(len(xs))}
        found = bool((d_pred[ys, xs] <= tol).any())
        as_cand = bool((d_cand[ys, xs] <= tol).any())
        info["candidato"] = as_cand
        if found:
            res.hits.append(info)
        else:
            # ¿Qué ve la SWIR en esa zona? (datos para ajustar los umbrales con criterio)
            y0, y1 = max(0, ys.min() - int(tol)), min(h, ys.max() + int(tol) + 1)
            x0, x1 = max(0, xs.min() - int(tol)), min(w, xs.max() + int(tol) + 1)
            rr, gg, bb = r[y0:y1, x0:x1], g[y0:y1, x0:x1], b[y0:y1, x0:x1]
            k = np.unravel_index(int(np.argmax(rr + 0.5 * gg)), rr.shape)
            ratio = rr / (gg + 1.0)
            info["entrada_en_la_zona"] = {
                "rojo_max": int(rr.max()), "verde_en_el_max": int(gg[k]), "azul_en_el_max": int(bb[k]),
                "razon_R_G_max": round(float(ratio.max()), 2),
            }
            cl = [c for c in fire.clusters if x0 <= c.centroid_xy[0] <= x1 and y0 <= c.centroid_xy[1] <= y1]
            if as_cand and cl:
                info["causa"] = "candidato SWIR descartado por contexto: " + "; ".join(cl[0].reasons)
            elif as_cand:
                info["causa"] = "candidato SWIR descartado por contexto"
            else:
                info["causa"] = "la firma de llama de la imagen de entrada no llega a los umbrales"
            res.misses.append(info)

    # Falsos positivos: focos aceptados lejos de cualquier llama de referencia
    tp = np.zeros((h, w), dtype=bool)
    fpm = np.zeros((h, w), dtype=bool)
    for c in fire.clusters:
        if c.confidence == "baja":
            continue
        x, y, bw, bh = c.bbox_xywh
        sub = fire.accepted[y:y + bh, x:x + bw]
        near = d_truth[y:y + bh, x:x + bw]
        ok = bool((near[sub] <= tol).any()) if sub.any() else False
        (tp if ok else fpm)[y:y + bh, x:x + bw] |= sub
        if not ok:
            res.false_pos.append({"centroide_xy": [round(c.centroid_xy[0], 1), round(c.centroid_xy[1], 1)],
                                  "pixeles": c.n_pixels, "confianza": c.confidence, "ubicacion": c.location})
    res.pred_tp, res.pred_fp = tp, fpm

    n_truth = len(ids)
    n_hit = len(res.hits)
    n_pred_total = sum(1 for c in fire.clusters if c.confidence != "baja")
    n_pred_ok = n_pred_total - len(res.false_pos)
    n_cand_hit = sum(1 for m in res.hits + res.misses if m["candidato"])
    prec = _safe_div(n_pred_ok, n_pred_total)
    rec = _safe_div(n_hit, n_truth)
    f1 = round(2 * prec * rec / (prec + rec), 3) if prec and rec else (0.0 if n_truth and n_pred_total else None)
    # A nivel de píxel (con tolerancia): cuánto de la llama real cubre lo detectado y viceversa
    px_rec = _safe_div(int((truth & (d_pred <= tol)).sum()), int(truth.sum()))
    px_prec = _safe_div(int((pred & (d_truth <= tol)).sum()), int(pred.sum()))
    res.metrics = {
        "focos_reales_referencia": n_truth,
        "focos_detectados_aceptados": n_pred_total,
        "aciertos": n_hit,
        "fallos_no_detectados": len(res.misses),
        "falsos_positivos": len(res.false_pos),
        "recall": rec,
        "precision": prec,
        "f1": f1,
        "recall_candidatos": _safe_div(n_cand_hit, n_truth),
        "recall_pixel": px_rec,
        "precision_pixel": px_prec,
        "tolerancia_px": round(tol, 1),
        "pixeles_referencia": int(truth.sum()),
        "pixeles_detectados": int(pred.sum()),
        "naranja_referencia_descartado_zonas": int(ndi.label(rejected, structure=np.ones((3, 3)))[1]),
    }
    return res


def describe_validation(m: dict, misses: list, false_pos: list) -> str:
    """Resumen en lenguaje natural de la validación."""
    if not m["focos_reales_referencia"]:
        if m["focos_detectados_aceptados"]:
            return (f"Validación: la imagen de referencia no muestra llamas, pero el modelo aceptó "
                    f"{m['focos_detectados_aceptados']} focos (posibles falsos positivos).")
        return "Validación: la referencia no muestra llamas y el modelo tampoco detecta focos (coincide)."
    pct = lambda v: "—" if v is None else f"{round(100 * v)} %"
    parts = [f"Validación contra la referencia Wildfire: {m['focos_reales_referencia']} focos reales; "
             f"el modelo (sin usar la Wildfire) encontró {m['aciertos']} (recall {pct(m['recall'])}) "
             f"y {m['falsos_positivos']} de sus {m['focos_detectados_aceptados']} focos no coinciden "
             f"(precisión {pct(m['precision'])})."]
    if misses:
        no_cand = sum(1 for x in misses if not x["candidato"])
        if no_cand:
            parts.append(f"{no_cand} de los fallos ni siquiera llegaron a candidato (la firma de llama no alcanza el umbral).")
        if len(misses) - no_cand:
            parts.append(f"{len(misses) - no_cand} fueron candidatos descartados por el filtro de contexto.")
    return " ".join(parts)
