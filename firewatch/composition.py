"""Identificación automática del tipo de composición (SWIR / True Color / Wildfire).

Heurística sencilla y explicable; el usuario siempre puede indicarlo a mano.
"""
from __future__ import annotations

import numpy as np

from .features import pseudo_nbr, saturation


def guess_composition(rgb: np.ndarray, valid: np.ndarray | None = None) -> tuple[str, dict]:
    """Devuelve ('swir' | 'truecolor' | 'wildfire', métricas usadas).

    - SWIR (B12-B8A-B4): la vegetación (mayoría del territorio) sale verde
      intenso => (G - R)/(G + R) mediano claramente positivo y saturación alta.
    - True Color: tonos ocres/olivas poco saturados, G ≈ R.
    - Wildfire: aspecto de True Color + píxeles naranja/amarillo muy saturados.
    """
    if valid is None:
        valid = np.ones(rgb.shape[:2], dtype=bool)
    v = rgb[valid]
    if v.size == 0:
        return "truecolor", {}
    sub = v[:: max(1, len(v) // 200_000)]
    pn = pseudo_nbr(sub[None])[0]
    sat = saturation(sub[None])[0]
    r, b = sub[:, 0], sub[:, 2]
    hot = (r >= 220) & (sat >= 0.6) & (r - b >= 120)
    metrics = {
        "median_pseudo_nbr": round(float(np.median(pn)), 3),
        "median_saturation": round(float(np.median(sat)), 3),
        "hot_fraction": round(float(hot.mean()), 6),
    }
    if metrics["median_pseudo_nbr"] > 0.04 and metrics["median_saturation"] > 0.3:
        kind = "swir"
    elif metrics["hot_fraction"] > 2e-5:
        kind = "wildfire"
    else:
        kind = "truecolor"
    return kind, metrics


NAMES = {"swir": "SWIR", "truecolor": "True Color", "wildfire": "Wildfire"}
FLAGS = {"swir": "--swir", "truecolor": "--truecolor", "wildfire": "--validar-con"}


def classify_file(path, max_side: int = 800) -> tuple[str, dict]:
    """Tipo de composición de un fichero (sobre una miniatura, rápido)."""
    from .io import border_mask, load_rgb_scaled
    rgb, transp, _ = load_rgb_scaled(path, max_side=max_side)
    return guess_composition(rgb, ~(transp | border_mask(rgb)))


def assign_inputs(swir=None, truecolor=None, wildfire=None, extra=()):
    """Reparte las imágenes por su tipo REAL de composición, no por la opción con que se pasaron.

    - La distinción SWIR / visible es muy fiable (la vegetación es verde intensa solo en SWIR),
      así que una imagen detectada como SWIR se usa como SWIR aunque llegue por otra opción, y
      una pasada como --swir que no lo es se reubica.
    - La Wildfire (referencia de validación) no entra en la detección. Entre True Color y Wildfire se respeta lo indicado: una Wildfire sin fuego activo es
      indistinguible de un True Color. Las imágenes sin opción (`extra`) se asignan por detección.

    Returns: ({tipo: ruta}, [avisos], {ruta: tipo_detectado})
    """
    items = [(p, h) for p, h in ((swir, "swir"), (truecolor, "truecolor"), (wildfire, "wildfire")) if p]
    items += [(p, None) for p in extra]
    detected = {p: classify_file(p)[0] for p, _ in items}
    out, warnings, pending = {}, [], []

    # 1) SWIR por detección
    swirs = [p for p, _ in items if detected[p] == "swir"]
    if swirs:
        pick = next((p for p, h in items if p in swirs and h == "swir"), swirs[0])
        out["swir"] = pick
    for p, h in items:
        if out.get("swir") == p:
            continue
        if detected[p] == "swir" and "swir" in out:
            warnings.append(f"{_name(p)} parece otra composición SWIR; se ignora.")
            continue
        pending.append((p, h))

    # 2) Visibles: se respeta la opción si es visible; si venía como --swir o sin opción, por detección
    for p, h in pending:
        kind = h if h in ("truecolor", "wildfire") else detected[p]
        if kind == "swir":           # pasada como --swir pero no lo es
            kind = detected[p]
        if kind in out:
            other = "wildfire" if kind == "truecolor" else "truecolor"
            if other not in out and other != "swir":
                kind = other
            else:
                warnings.append(f"{_name(p)}: ya hay una imagen {NAMES[kind]}; se ignora.")
                continue
        out[kind] = p

    for p, h in items:
        final = next((k for k, v in out.items() if v == p), None)
        if h and final and final != h:
            warnings.append(f"{_name(p)} se pasó con {FLAGS[h]} pero es una composición {NAMES[final]} "
                            f"(detectada automáticamente): se analiza como {NAMES[final]}.")
    return out, warnings, detected


def _name(p) -> str:
    from pathlib import Path
    return Path(p).name
