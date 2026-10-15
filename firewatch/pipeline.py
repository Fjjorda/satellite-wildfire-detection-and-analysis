"""Orquestación del análisis completo de una escena."""
from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Callable

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from . import __version__
from .burned import BurnResult, burn_score, burn_score_tc, recover_under_smoke, segment_burned
from .composition import assign_inputs, guess_composition
from .config import Params
from .fire import FireResult, detect_fire
from .front import FrontResult, compute_front
from .geometry import GeoRef, mask_polygons, polygon_length, to_geojson
from .io import border_mask, load_rgb_scaled, match_size
from .masks import classify_smoke, cloud_mask, smoke_mask_swir, smoke_mask_tc, water_mask, water_mask_tc
from .render import render_debug, render_overlay, render_validation
from .validate import ValidationResult, describe_validation, validate_fire


@dataclass
class Analysis:
    swir: np.ndarray | None
    truecolor: np.ndarray | None
    reference: np.ndarray | None          # Wildfire de referencia: SOLO para validar, nunca para detectar
    valid: np.ndarray
    water: np.ndarray
    smoke: np.ndarray | None
    cloud: np.ndarray
    burn: BurnResult
    fire: FireResult
    report: dict
    burn_polygons: list
    geojson: dict
    smoke_low: np.ndarray | None = None   # humo de baja confianza (posible error), aparte de `smoke`
    front: FrontResult | None = None      # frente activo, zona con actividad y dirección del humo
    validation: ValidationResult | None = None   # comparación con la referencia (si se aportó)
    primary: np.ndarray | None = None     # imagen sobre la que se trabaja: SWIR si hay, si no True Color
    mode: str = "swir+truecolor"           # entradas usadas: swir+truecolor | solo_swir | solo_truecolor


def analyze(swir_path, truecolor_path=None, params: Params | None = None,
            geo: GeoRef | None = None, max_side: int | None = 2500, auto_assign: bool = True,
            extra_paths=(), reference_path=None, *,
            on_progress: Callable[[str], None] | None = None) -> Analysis:
    """Analiza una escena con SWIR y/o True Color. La detección NO usa la Wildfire.

    reference_path: composición Wildfire de la misma escena, usada únicamente DESPUÉS de detectar,
    para validar los focos (precisión / recall). Si se encuentra entre `extra_paths`, se toma también
    como referencia.
    max_side: lado mayor máximo (px) con el que se analiza. Las imágenes más grandes se reducen
    (la superficie se corrige con el factor de reducción). None = resolución original."""
    params = params or Params()
    geo = geo or GeoRef()
    progress = on_progress or (lambda _stage: None)
    progress("loading")
    assign_warn = []
    if reference_path:
        from .composition import classify_file
        if classify_file(reference_path)[0] == "swir":
            raise ValueError(f"'{Path(reference_path).name}' es una composición SWIR, no una Wildfire. "
                             "La SWIR es la entrada del análisis (--swir); --validar-con solo admite la "
                             "imagen Wildfire de referencia.")
    if auto_assign:
        paths, assign_warn, _det = assign_inputs(swir_path, truecolor_path, reference_path, extra_paths)
        swir_path, truecolor_path = paths.get("swir"), paths.get("truecolor")
        reference_path = paths.get("wildfire")
        for wmsg in assign_warn:
            warnings.warn(wmsg)
    if swir_path is None and truecolor_path is None:
        raise ValueError("Hace falta al menos una imagen de entrada: SWIR (--swir) o True Color (--truecolor). "
                         "Sirve cualquiera de las dos (o ambas, que da el mejor resultado). La imagen Wildfire "
                         "solo sirve para validar (--validar-con), no para detectar.")
    mode = "swir+truecolor" if (swir_path and truecolor_path) else ("solo_swir" if swir_path else "solo_truecolor")

    # La imagen principal fija el tamaño de trabajo: la SWIR si hay, si no el True Color
    primary_path = swir_path or truecolor_path
    primary, transp, (ow, oh) = load_rgb_scaled(primary_path, max_side=max_side)
    shape = primary.shape[:2]
    scale = ow / shape[1]
    if geo.pixel_size_m:
        geo = GeoRef(pixel_size_m=geo.pixel_size_m * scale, bbox=geo.bbox)
    geo = geo.resolve(shape)
    valid = ~(transp | border_mask(primary))

    kind, kmetrics = guess_composition(primary, valid)
    comp_info = {("swir" if swir_path else "truecolor"): {"detectado": kind, **kmetrics}}
    swir = primary if swir_path else None
    tc = primary if not swir_path else None
    wf = None
    if swir_path and truecolor_path:
        tc, _t, _o = load_rgb_scaled(truecolor_path, size=(shape[1], shape[0]))
        tc = match_size(tc, shape, "imagen True Color")
        k2, m2 = guess_composition(tc)
        comp_info["truecolor"] = {"detectado": k2, **m2}
    if reference_path:
        wf, _t, _o = load_rgb_scaled(reference_path, size=(shape[1], shape[0]))
        wf = match_size(wf, shape, "imagen Wildfire de referencia")
        k3, m3 = guess_composition(wf)
        comp_info["wildfire"] = {"detectado": k3, **m3}

    if swir is not None:
        # --- 1. Vetos ---------------------------------------------------
        progress("masks")
        water = water_mask(swir, params.water, tc=tc, valid=valid)
        cloud = cloud_mask(swir, params.cloud, valid=valid)
        smoke_tc = smoke_mask_tc(tc, params.smoke, valid=valid) if (tc is not None) else None
        smoke = smoke_tc if smoke_tc is not None else smoke_mask_swir(swir, params.smoke, valid=valid)
        smoke = smoke & ~water & ~cloud
        # --- 2. Área quemada (reto básico) -------------------------------
        progress("burned")
        score = burn_score(swir, valid & ~cloud, params.burn, tc=tc, smoke_tc=smoke_tc)
        burn = segment_burned(score, valid & ~cloud, water, params.burn)
        # Humo: solo el de confianza alta alimenta el resto del análisis; el suelto se marca aparte.
        smoke, smoke_low, n_smoke_low = classify_smoke(smoke, burn.footprint, params.smoke)
        burn.under_smoke = (recover_under_smoke(swir, smoke, water, valid & ~cloud, burn.footprint, params.burn)
                            if params.burn.smoke_recovery else np.zeros(shape, dtype=bool))
        fire_src, source = swir, "swir"
    else:
        # Solo True Color: el humo y la nube se confunden (ambos blancos); no se distingue la nube.
        progress("masks")
        water = water_mask_tc(tc, params.water, valid=valid)
        cloud = np.zeros(shape, dtype=bool)
        smoke = smoke_mask_tc(tc, params.smoke, valid=valid) & ~water
        progress("burned")
        score = burn_score_tc(tc, valid, params.burn, smoke=smoke)
        burn = segment_burned(score, valid, water, params.burn)
        smoke, smoke_low, n_smoke_low = classify_smoke(smoke, burn.footprint, params.smoke)
        burn.under_smoke = np.zeros(shape, dtype=bool)      # la recuperación bajo el humo necesita SWIR
        fire_src, source = tc, "truecolor"

    # --- 3. Fuego activo (reto avanzado) --------------------------------
    progress("fire")
    fire = detect_fire(fire_src, valid, cloud, burn.footprint, smoke, params.fire, source=source)
    # Frente activo: perímetro de la huella completa (confirmada + probable bajo humo)
    full_fp = burn.footprint | (burn.under_smoke if burn.under_smoke is not None else False)
    front = compute_front(full_fp, fire.accepted, burn.burned, smoke, params.fire, geo.pixel_size_m)
    # --- 4. Métricas e informe ------------------------------------------
    progress("metrics")
    polys = mask_polygons(burn.footprint)
    report = _build_report(shape, valid, water, smoke, cloud, burn, fire, polys, geo, comp_info,
                           params, bool(tc is not None and swir is not None),
                           smoke_low=smoke_low, n_smoke_low=n_smoke_low, front=front, mode=mode)

    # --- 5. Validación contra la referencia (después de detectar; no influye en nada de lo anterior)
    validation = None
    if wf is not None:
        # En modo solo True Color, el propio TC puede mostrar la llama: no se usa para filtrar la referencia
        validation = validate_fire(wf, valid, tc if swir is not None else None, primary, fire, params.validation, params.fire)
        report["validacion"] = {**validation.metrics,
                                "detalle_fallos": validation.misses,
                                "detalle_falsos_positivos": validation.false_pos,
                                "resumen": describe_validation(validation.metrics, validation.misses,
                                                               validation.false_pos)}
    gj = to_geojson(polys, [c for c in fire.clusters if c.confidence != "baja"], shape, geo,
                    {"generador": f"firewatch {__version__}"}, smoke_polys=mask_polygons(burn.under_smoke),
                    front_segments=front.segments)
    report["avisos"] = assign_warn + report["avisos"]
    return Analysis(swir, tc, wf, valid, water, smoke, cloud, burn, fire, report, polys, gj,
                    smoke_low=smoke_low, front=front, validation=validation, primary=primary, mode=mode)


def _direction(x, y, w, h) -> str:
    col = 0 if x < w / 3 else (1 if x < 2 * w / 3 else 2)
    row = 0 if y < h / 3 else (1 if y < 2 * h / 3 else 2)
    names = [["noroeste", "norte", "noreste"], ["oeste", "centro", "este"], ["suroeste", "sur", "sureste"]]
    return names[row][col]


def _fmt(x: float, nd: int = 0) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _build_report(shape, valid, water, smoke, cloud, burn, fire, polys, geo, comp_info, params,
                  has_tc, smoke_low=None, n_smoke_low=0, front=None, mode="swir+truecolor") -> dict:
    h, w = shape
    n_valid = int(valid.sum())
    px_burn = int(burn.burned.sum())
    px_under = int(burn.under_smoke.sum()) if burn.under_smoke is not None else 0
    px_strict = int(burn.burned_strict.sum())
    perim_px = sum(polygon_length(p["outer"]) for p in polys)

    # Parte del perímetro que linda con humo => posible infraestimación
    smoke_border_frac = 0.0
    if smoke is not None and smoke.any() and burn.footprint.any():
        edge = burn.footprint & ~ndi.binary_erosion(burn.footprint)
        near_smoke = ndi.binary_dilation(smoke, iterations=3)
        smoke_border_frac = float((edge & near_smoke).sum() / max(edge.sum(), 1))

    m = geo.pixel_size_m
    area = {
        "pixeles": px_burn,
        "pixeles_estricto": px_strict,
        "porcentaje_imagen": round(100 * px_burn / max(n_valid, 1), 3),
        "pixeles_bajo_humo_probable": px_under,
        "porcentaje_bajo_humo_probable": round(100 * px_under / max(n_valid, 1), 3),
    }
    perimeter = {"pixeles": round(perim_px, 1)}
    if m:
        area["hectareas"] = round(px_burn * m * m / 1e4, 1)
        area["hectareas_bajo_humo_probable"] = round(px_under * m * m / 1e4, 1)
        # El límite superior incluye lo probable bajo el humo
        area["hectareas_rango"] = [round(px_strict * m * m / 1e4, 1), round((px_burn + px_under) * m * m / 1e4, 1)]
        perimeter["km"] = round(perim_px * m / 1000, 2)

    # Confianza por polígono: puntuación media y tamaño relativo al mayor
    poly_info = []
    blab, bn = ndi.label(burn.footprint, structure=np.ones((3, 3)))
    if bn:
        sizes = np.bincount(blab.ravel())[1:]
        means = ndi.mean(burn.score, blab, index=np.arange(1, bn + 1))
        for i in np.argsort(-sizes):
            rel = sizes[i] / sizes.max()
            conf = "alta" if (means[i] >= 0.55 and rel >= 0.05) else ("media" if means[i] >= 0.4 else "baja")
            poly_info.append({"pixeles": int(sizes[i]), "puntuacion_media": round(float(means[i]), 3),
                              "tipo": "principal" if rel >= 0.05 else "secundario", "confianza": conf})

    accepted = [c for c in fire.clusters if c.confidence != "baja"]
    discarded = [c for c in fire.clusters if c.confidence == "baja"]

    has_fire_area = px_burn > 0
    centroid = None
    if has_fire_area:
        ys, xs = np.nonzero(burn.burned)
        centroid = (float(xs.mean()), float(ys.mean()))

    warnings_out = []
    if not m:
        warnings_out.append("Sin escala: superficie en píxeles y % de la imagen. "
                            "Indica --pixel-size o --bbox para obtener hectáreas.")
    if n_smoke_low:
        warnings_out.append(f"{n_smoke_low} zonas de humo marcadas como de baja confianza (pequeñas o sueltas, "
                            "lejos del penacho principal): posibles errores de detección, no se usan en el análisis.")
    if px_under:
        warnings_out.append("Se ha estimado área quemada probable bajo el humo (marcada aparte, con trama distinta): "
                            "es una estimación menos fiable que el área confirmada.")
    if smoke_border_frac > 0.15 and not px_under:
        warnings_out.append(f"El {round(100 * smoke_border_frac)}% del perímetro linda con humo: "
                            "el área puede estar infraestimada en esa zona.")
    if mode == "solo_truecolor":
        warnings_out.append("Solo True Color: el área quemada se estima por ser oscura y no verde, sin la firma espectral "
                            "del SWIR. Es utilizable, pero menos fiable: las sombras del relieve, el bosque muy oscuro o "
                            "el agua pueden confundirse, y la nube y el humo no se distinguen. Con SWIR el resultado mejora.")
        warnings_out.append("Los focos activos en True Color son difíciles de ver (solo llamas muy intensas): "
                            "se aceptan con confianza máxima media.")
    elif mode == "solo_swir":
        warnings_out.append("Sin imagen True Color: el humo se estima solo desde SWIR (menos fiable).")

    fire_info = _fire_section(front, burn, accepted, geo, (w, h))

    report = {
        "version": __import__("firewatch").__version__,
        "modo": mode,
        "imagen": {"ancho": w, "alto": h, "pixeles_validos": n_valid,
                   "tamano_pixel_m": round(m, 2) if m else None,
                   "composiciones": comp_info},
        "incendio_detectado": has_fire_area,
        "area_quemada": area,
        "perimetro": perimeter,
        "poligonos": poly_info,
        "islas_no_quemadas": int(ndi.label(burn.islands)[1]),
        "agua_dentro_perimetro_px": int(burn.water_inside.sum()),
        "agua_total_px": int(water.sum()),
        "humo_px": int(smoke.sum()) if smoke is not None else 0,
        "humo_baja_confianza_px": int(smoke_low.sum()) if smoke_low is not None else 0,
        "humo_baja_confianza_zonas": int(n_smoke_low),
        "nubes_px": int(cloud.sum()),
        "fraccion_perimetro_con_humo": round(smoke_border_frac, 3),
        "fuego_activo": fire_info,
        "focos_activos": [_cluster_dict(c, geo, (h, w)) for c in accepted],
        "focos_descartados": [asdict(c) for c in discarded],
        "avisos": warnings_out,
        "parametros": params.to_dict(),
    }
    report["descripcion"] = describe(report, centroid, (w, h), accepted)
    return report


def _cluster_dict(c, geo, shape) -> dict:
    d = asdict(c)
    ll = geo.to_lonlat(*c.centroid_xy, shape) if geo is not None else None
    if ll:
        d["lon"], d["lat"] = round(ll[0], 6), round(ll[1], 6)
    return d


def _fire_section(front, burn, accepted, geo, size) -> dict:
    """Bloque del informe del reto avanzado: frente activo y superficie con/sin actividad."""
    if front is None:
        return {}
    m = geo.pixel_size_m
    zone = front.active_zone if front.active_zone is not None else np.zeros_like(burn.burned)
    px_zone = int((zone & burn.burned).sum())
    px_quiet = int(burn.burned.sum()) - px_zone
    out = {
        "focos": len(accepted),
        "tramos_frente_activo": len(front.segments),
        "frente_activo_px": round(front.active_length_px, 1),
        "perimetro_total_px": round(front.perimeter_length_px, 1),
        "fraccion_perimetro_activo": round(front.active_fraction, 3),
        "radio_zona_activa_px": round(front.radius_px, 1),
        "quemado_con_actividad_px": px_zone,
        "quemado_sin_actividad_px": px_quiet,
        "direccion_humo": front.smoke_direction,
        "fraccion_frente_a_favor_del_humo": (round(front.downwind_fraction, 3)
                                             if front.downwind_fraction is not None else None),
        "nota": "Dirección del humo suponiendo imagen orientada al norte. El tramo del frente a favor "
                "del humo es probablemente la cabeza del incendio (avance a favor del viento).",
    }
    if m:
        out["frente_activo_km"] = round(front.active_length_px * m / 1000, 2)
        if burn.burned.any():
            out["radio_zona_activa_m"] = round(front.radius_px * m)
            out["quemado_con_actividad_ha"] = round(px_zone * m * m / 1e4, 1)
            out["quemado_sin_actividad_ha"] = round(px_quiet * m * m / 1e4, 1)
    if not burn.burned.any():
        for k in ("quemado_con_actividad_px", "quemado_sin_actividad_px", "radio_zona_activa_px"):
            out.pop(k, None)
    return out


def describe(report: dict, centroid, size, accepted) -> str:
    """Texto alternativo / resumen en lenguaje natural (determinista)."""
    text = _describe_core(report, centroid, size, accepted)
    if report.get("modo") == "solo_truecolor":
        text = "Análisis solo con True Color (estimación menos fiable que con SWIR). " + text
    return text


def _describe_core(report: dict, centroid, size, accepted) -> str:
    w, h = size
    if not report["incendio_detectado"]:
        return ("No se ha detectado ninguna zona quemada en la imagen. "
                + (f"Se han encontrado {len(accepted)} puntos calientes que conviene revisar." if accepted else ""))
    a = report["area_quemada"]
    if "hectareas" in a:
        lo, hi = a["hectareas_rango"]
        sup = f"confirmada de unas {_fmt(a['hectareas'])} ha (rango {_fmt(lo)}–{_fmt(hi)} ha)"
    else:
        sup = f"que ocupa el {_fmt(a['porcentaje_imagen'], 1)} % de la imagen"
    parts = [f"Área quemada {sup}, situada al {_direction(*centroid, w, h)} de la imagen."]
    if "km" in report["perimetro"]:
        parts.append(f"Perímetro aproximado de {_fmt(report['perimetro']['km'], 1)} km.")
    if report["islas_no_quemadas"]:
        parts.append(f"Contiene {report['islas_no_quemadas']} islas interiores sin quemar.")
    if a.get("pixeles_bajo_humo_probable"):
        extra = (f" (unas {_fmt(a['hectareas_bajo_humo_probable'])} ha, incluidas en el límite superior del rango)"
                 if "hectareas_bajo_humo_probable" in a else
                 f" ({_fmt(a['porcentaje_bajo_humo_probable'], 1)} % de la imagen)")
        parts.append("Además se estima área quemada probable bajo el humo" + extra + ", menos fiable que el resto.")
    if report["agua_dentro_perimetro_px"]:
        parts.append("Hay masas de agua dentro del perímetro, excluidas de la superficie.")
    _describe_fire(parts, report, accepted, w, h)
    if report["fraccion_perimetro_con_humo"] > 0.15:
        parts.append("Parte del borde está cubierta por humo.")
    if report.get("humo_baja_confianza_zonas"):
        parts.append(f"Hay {report['humo_baja_confianza_zonas']} zonas de humo de baja confianza "
                     "(pequeñas o aisladas), posibles errores de detección.")
    return " ".join(parts)


def _describe_fire(parts, report, accepted, w, h):
    if accepted:
        front = sum(1 for c in accepted if c.location == "frente")
        strong = sum(1 for c in accepted if c.intensity == "alta")
        dirs = sorted({_direction(*c.centroid_xy, w, h) for c in accepted})
        medium = sum(1 for c in accepted if c.confidence == "media")
        parts.append(f"{len(accepted)} focos activos ({front} en el frente"
                     + (f", {medium} de confianza media" if medium else "")
                     + ""
                     + (f", {strong} de intensidad alta" if strong else "")
                     + f"), en la zona {', '.join(dirs)}.")
        fa = report.get("fuego_activo") or {}
        if fa.get("tramos_frente_activo"):
            length = (f"{_fmt(fa['frente_activo_km'], 1)} km" if "frente_activo_km" in fa
                      else f"{_fmt(fa['frente_activo_px'])} px")
            parts.append(f"Frente activo de {length} en {fa['tramos_frente_activo']} tramos, "
                         f"el {_fmt(100 * fa['fraccion_perimetro_activo'])} % del perímetro; "
                         "el resto del perímetro no muestra actividad.")
        elif fa.get("frente_activo_px"):
            length = (f"{_fmt(fa['frente_activo_km'], 1)} km" if "frente_activo_km" in fa
                      else f"{_fmt(fa['frente_activo_px'])} px")
            parts.append(f"Línea de llamas de unos {length} en total.")
        if "quemado_con_actividad_ha" in fa:
            parts.append(f"Del área confirmada, unas {_fmt(fa['quemado_con_actividad_ha'])} ha están a menos de "
                         f"{fa['radio_zona_activa_m']} m de un foco (con actividad) y "
                         f"{_fmt(fa['quemado_sin_actividad_ha'])} ha ya no muestran actividad.")
        elif fa.get("quemado_con_actividad_px") is not None and report["area_quemada"]["pixeles"]:
            pct = 100 * fa["quemado_con_actividad_px"] / report["area_quemada"]["pixeles"]
            parts.append(f"El {_fmt(pct)} % del área confirmada está junto a algún foco (con actividad); "
                         "el resto ya no muestra actividad.")
        if fa.get("direccion_humo"):
            dw = fa.get("fraccion_frente_a_favor_del_humo")
            txt = f"El humo se desplaza hacia el {fa['direccion_humo']}"
            if dw:
                txt += (f"; el {_fmt(100 * dw)} % del frente activo está en ese lado "
                        "y es probablemente la cabeza del incendio")
            parts.append(txt + ".")
    else:
        parts.append("No se detectan focos activos.")


def save_outputs(an: Analysis, out_dir, base: str = "swir", debug: bool = True) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base_img = {"swir": an.swir, "truecolor": an.truecolor}.get(base)
    if base_img is None:
        base_img = an.primary
    r = an.report
    a = r["area_quemada"]
    lines = []
    if "hectareas" in a:
        lines.append(f"Superficie: {_fmt(a['hectareas_rango'][0])}–{_fmt(a['hectareas_rango'][1])} ha")
    else:
        lines.append(f"Superficie: {_fmt(a['porcentaje_imagen'], 1)} % de la imagen")
    lines.append(f"Focos activos: {len(r['focos_activos'])}")
    fa = r.get("fuego_activo") or {}
    if fa.get("tramos_frente_activo"):
        lines.append("Frente activo: " + (f"{_fmt(fa['frente_activo_km'], 1)} km" if "frente_activo_km" in fa
                                          else f"{_fmt(100 * fa['fraccion_perimetro_activo'])} % del perímetro"))
    if fa.get("direccion_humo"):
        lines.append(f"Humo hacia el {fa['direccion_humo']}")
    overlay = render_overlay(base_img, an.burn, an.fire, an.smoke, lines, smoke_low=an.smoke_low, front=an.front)
    files = {}
    p = out / "resultado.png"; Image.fromarray(overlay).save(p); files["overlay"] = str(p)
    p = out / "mascara_quemado.png"; Image.fromarray((an.burn.burned * 255).astype(np.uint8)).save(p); files["mask_burned"] = str(p)
    if an.burn.under_smoke is not None:
        p = out / "mascara_bajo_humo.png"; Image.fromarray((an.burn.under_smoke * 255).astype(np.uint8)).save(p); files["mask_under_smoke"] = str(p)
    if an.front is not None and an.front.active_zone is not None:
        p = out / "mascara_zona_activa.png"; Image.fromarray((an.front.active_zone * 255).astype(np.uint8)).save(p); files["mask_active_zone"] = str(p)
    p = out / "mascara_focos.png"; Image.fromarray((an.fire.accepted * 255).astype(np.uint8)).save(p); files["mask_fire"] = str(p)
    p = out / "resultado.geojson"; p.write_text(json.dumps(an.geojson, ensure_ascii=False), encoding="utf-8"); files["geojson"] = str(p)
    p = out / "informe.json"; p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8"); files["report"] = str(p)
    p = out / "descripcion.txt"; p.write_text(r["descripcion"] + "\n", encoding="utf-8"); files["alt_text"] = str(p)
    if debug:
        p = out / "diagnostico.png"
        render_debug(p, an.primary, an.burn.score, an.water, an.smoke, an.cloud, an.burn, an.fire, overlay,
                     smoke_low=an.smoke_low, front=an.front, validation=an.validation)
        files["debug"] = str(p)
    if an.validation is not None:
        p = out / "validacion.png"
        Image.fromarray(render_validation(an.primary, an.fire, an.validation)).save(p); files["validation"] = str(p)
        p = out / "validacion.txt"
        p.write_text(_validation_text(r["validacion"]), encoding="utf-8"); files["validation_text"] = str(p)
    return files


def _validation_text(v: dict) -> str:
    pc = lambda x: "—" if x is None else f"{round(100 * x)} %"
    L = [v["resumen"], "",
         f"Focos reales en la referencia : {v['focos_reales_referencia']}",
         f"Focos aceptados por el modelo : {v['focos_detectados_aceptados']}",
         f"Aciertos / fallos / falsos +  : {v['aciertos']} / {v['fallos_no_detectados']} / {v['falsos_positivos']}",
         f"Recall {pc(v['recall'])} | Precisión {pc(v['precision'])} | F1 {v['f1']}",
         f"Recall de candidatos          : {pc(v['recall_candidatos'])}  (antes del filtro de contexto)",
         f"Recall / precisión por píxel  : {pc(v['recall_pixel'])} / {pc(v['precision_pixel'])}",
         f"Tolerancia espacial           : {v['tolerancia_px']} px", ""]
    if v["detalle_fallos"]:
        L.append("FOCOS REALES NO DETECTADOS (x, y en píxeles analizados):")
        for m in v["detalle_fallos"]:
            z = m.get("entrada_en_la_zona", {})
            L.append(f"  - ({m['centroide_xy'][0]:.0f}, {m['centroide_xy'][1]:.0f}) {m['pixeles']} px · {m['causa']} · "
                     f"entrada máx R={z.get('rojo_max')} G={z.get('verde_en_el_max')} B={z.get('azul_en_el_max')} "
                     f"R/G máx={z.get('razon_R_G_max')}")
    if v["detalle_falsos_positivos"]:
        L.append("FOCOS ACEPTADOS SIN LLAMA EN LA REFERENCIA:")
        for m in v["detalle_falsos_positivos"]:
            L.append(f"  - ({m['centroide_xy'][0]:.0f}, {m['centroide_xy'][1]:.0f}) {m['pixeles']} px · "
                     f"{m['ubicacion']} · confianza {m['confianza']}")
    return "\n".join(L) + "\n"
