"""Independent image layers and pixel-based browser results."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from ..geometry import mask_polygons
from ..pipeline import Analysis

COLORS = {
    "burned": "#D55E00", "under_smoke": "#CC79A7", "front": "#F0E442",
    "fire": "#E69F00", "water": "#0072B2", "smoke": "#56B4E9",
    "perimeter": "#D55E00", "smoke_low": "#333333",
}


def _path(mask: np.ndarray) -> str:
    rings = []
    for polygon in mask_polygons(mask):
        for ring in [polygon["outer"], *polygon["holes"]]:
            rings.append("M" + "L".join(f"{x},{y}" for x, y in ring) + "Z")
    return " ".join(rings)


def front_segments(analysis: Analysis) -> list[list[list[float]]]:
    """Uses the engine's active-front sectors without a second estimation algorithm."""
    if analysis.front is None:
        return []
    return [segment.points.tolist() for segment in analysis.front.segments]


def _svg(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{body}</svg>'
    )


def _outlined(path: str, color: str, width: float, dash: str = "") -> str:
    attr = f' stroke-dasharray="{dash}"' if dash else ""
    return "".join(
        f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="{size}" '
        f'stroke-linejoin="round" stroke-linecap="round"{attr}/>'
        for stroke, size in [("#000000", width + 3), ("#FFFFFF", width + 1.5), (color, width)]
    )


def build_assets(analysis: Analysis, directory: Path) -> dict:
    """Writes independent layers and a report without geographic coordinates."""
    height, width = analysis.valid.shape
    thickness = max(1.5, np.hypot(height, width) / 800)
    layers = {}
    bases = {}
    files = []
    base_images = {"swir": analysis.swir, "truecolor": analysis.truecolor, "wildfire": analysis.reference}
    for key, array in base_images.items():
        if array is not None:
            filename = f"base-{key}.png"
            Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)).save(directory / filename)
            bases[key] = filename
            files.append(filename)

    masks = {
        "burned": analysis.burn.burned,
        "under_smoke": analysis.burn.under_smoke,
        "water": analysis.water,
        "smoke": analysis.smoke,
        "smoke_low": analysis.smoke_low,
        "fire": analysis.fire.accepted,
    }
    segments = front_segments(analysis)
    for key in COLORS:
        color = COLORS[key]
        mask = masks.get(key)
        path = _path(mask) if mask is not None else ""
        content = ""
        count = int(mask.sum()) if mask is not None else 0
        if key in ("burned", "under_smoke"):
            cross = '<path d="M0 0L12 12"/>' if key == "under_smoke" else ""
            content = (
                f'<defs><pattern id="h" width="12" height="12" patternUnits="userSpaceOnUse">'
                f'<g stroke="{color}" stroke-width="2"><path d="M-3 3L3 -3M0 12L12 0M9 15L15 9"/>'
                f'{cross}</g></pattern></defs>'
                f'<path d="{path}" fill="{color}" fill-opacity="0.22" fill-rule="evenodd"/>'
                f'<path d="{path}" fill="url(#h)" fill-rule="evenodd"/>'
            )
            if key == "under_smoke":
                content += _outlined(path, color, thickness, "3 5")
        elif key == "perimeter":
            count = int(analysis.burn.footprint.sum())
            content = _outlined(_path(analysis.burn.footprint), color, thickness)
        elif key == "water":
            content = f'<path d="{path}" fill="{color}" fill-opacity="0.55" fill-rule="evenodd"/>'
            content += _outlined(path, color, thickness)
        elif key in ("smoke", "smoke_low"):
            content = _outlined(path, color, thickness, "10 7" if key == "smoke" else "2 6")
        elif key == "front":
            count = len(segments)
            for segment in segments:
                line = "M" + "L".join(f"{x},{y}" for x, y in segment)
                content += _outlined(line, color, thickness * 2)
        elif key == "fire":
            content = f'<path d="{path}" fill="#F0E442" stroke="#000000" stroke-width="1"/>'
            for cluster in analysis.fire.clusters:
                if cluster.confidence == "baja":
                    continue
                x, y = cluster.centroid_xy
                size = max(7, thickness * (5 if cluster.intensity == "alta" else 4))
                triangle = f"M{x},{y-size}L{x-size},{y+size}L{x+size},{y+size}Z"
                fill = color if cluster.confidence == "alta" else "none"
                content += f'<path d="{triangle}" fill="{fill}" stroke="#000000" stroke-width="{thickness+3}"/>'
                content += f'<path d="{triangle}" fill="{fill}" stroke="{color}" stroke-width="{thickness}"/>'
        filename = f"layer-{key}.svg"
        (directory / filename).write_text(_svg(width, height, content), encoding="utf-8")
        layers[key] = {"file": filename, "count": count}
        files.append(filename)
        if mask is not None:
            filename = f"mask-{key}.png"
            Image.fromarray(mask.astype(np.uint8) * 255).save(directory / filename)
            files.append(filename)

    report = analysis.report
    ys, xs = np.nonzero(analysis.burn.burned)
    direction = "center"
    if len(xs):
        column = min(2, int(xs.mean() * 3 / width))
        row = min(2, int(ys.mean() * 3 / height))
        direction = [["nw", "n", "ne"], ["w", "center", "e"], ["sw", "s", "se"]][row][column]
    reason_codes = {
        "en el borde del área quemada": "edge",
        "junto a un polígono quemado pequeño y separado": "secondary",
        "dentro del área quemada (posible rescoldo)": "interior",
        "lejos de cualquier área quemada": "isolated",
        "junto a humo": "smoke",
        "detectado solo en True Color (firma de llama poco fiable)": "truecolor_only",
    }
    def clusters(accepted: bool) -> list[dict]:
        result = []
        for cluster in analysis.fire.clusters:
            if (cluster.confidence != "baja") != accepted:
                continue
            item = asdict(cluster)
            item["reasons"] = [
                "low_contrast" if reason.startswith("no destaca de su entorno (")
                else reason_codes.get(reason, "other")
                for reason in cluster.reasons
            ]
            item["reason_details"] = list(cluster.reasons)
            result.append(item)
        return result

    notes = []
    if analysis.truecolor is None:
        notes.append("no_truecolor")
    if analysis.reference is not None:
        notes.append("reference_only")
    if analysis.swir is None:
        notes.append("truecolor_only")
    if report["humo_baja_confianza_zonas"]:
        notes.append("smoke_low")
    if report["area_quemada"]["pixeles_bajo_humo_probable"]:
        notes.append("under_smoke")
    if report["fraccion_perimetro_con_humo"] > 0.15:
        notes.append("smoke_obscured")
    swir_info = report["imagen"]["composiciones"].get("swir")
    if swir_info and swir_info["detectado"] != "swir":
        notes.append("composition_mismatch")
    web_report = {
        "version": report["version"], "width": width, "height": height,
        "valid_pixels": int(analysis.valid.sum()), "detected": report["incendio_detectado"],
        "burned_pixels": int(analysis.burn.burned.sum()),
        "strict_pixels": int(analysis.burn.burned_strict.sum()),
        "burned_percent": report["area_quemada"]["porcentaje_imagen"],
        "probable_pixels": report["area_quemada"]["pixeles_bajo_humo_probable"],
        "probable_percent": report["area_quemada"]["porcentaje_bajo_humo_probable"],
        "perimeter_pixels": report["perimetro"]["pixeles"],
        "water_pixels": int(analysis.water.sum()),
        "water_inside_pixels": int(analysis.burn.water_inside.sum()),
        "islands": report["islas_no_quemadas"], "smoke_pixels": report["humo_px"],
        "smoke_low_regions": report["humo_baja_confianza_zonas"],
        "front_sectors": len(segments), "direction": direction,
        "hotspots": clusters(True), "rejected_hotspots": clusters(False), "notes": notes,
    }
    if "validacion" in report:
        web_report["validation"] = report["validacion"]
    (directory / "report.json").write_text(json.dumps(web_report, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append("report.json")
    return {"report": web_report, "bases": bases, "layers": layers, "files": files}
