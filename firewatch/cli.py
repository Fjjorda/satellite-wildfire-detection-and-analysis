"""Línea de comandos.

Una escena:
    python -m firewatch --swir swir.png --truecolor tc.png --out resultados/avila

Varias escenas (lote), emparejando por nombre de fichero:
    python -m firewatch --batch images/ --out resultados/
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .batch import discover_scenes, run_batch, write_html_index
from .config import Params
from .geometry import GeoRef
from .pipeline import analyze, save_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="firewatch",
                                 description="Delimita el área quemada y detecta focos activos.")
    ap.add_argument("--swir", help="Composición SWIR (B12-B8A-B4) de UNA escena. Obligatoria salvo con --batch.")
    ap.add_argument("--batch", metavar="CARPETA",
                    help="Analiza todas las escenas de la carpeta (recursivo): el SWIR, el True Color y la "
                         "Wildfire de cada una se emparejan por nombre de fichero. Excluyente con --swir.")
    ap.add_argument("--truecolor", help="Composición True Color de la misma escena (recomendada).")
    ap.add_argument("--wildfire", help="Composición Wildfire de la misma escena (opcional).")
    ap.add_argument("--out", required=True, help="Carpeta de salida (con --batch, una subcarpeta por escena).")
    ap.add_argument("--base", choices=["swir", "truecolor", "wildfire"], default="swir",
                    help="Imagen sobre la que se dibuja el resultado.")
    ap.add_argument("--pixel-size", type=float, help="Tamaño de píxel en metros (p. ej. 10 o 20).")
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
                    help="Extensión geográfica WGS84 de la imagen.")
    ap.add_argument("--max-side", type=int, default=2500,
                    help="Lado mayor máximo en px para el análisis (por defecto 2500; 0 = tamaño original, lento).")
    ap.add_argument("--no-smoke-recovery", action="store_true",
                    help="No estimar el área quemada probable bajo el humo.")
    ap.add_argument("--no-debug", action="store_true", help="No generar el panel de diagnóstico.")
    a = ap.parse_args(argv)

    if bool(a.swir) == bool(a.batch):
        ap.error("indica --swir (una escena) o --batch CARPETA (varias escenas); son excluyentes.")

    params = Params()
    if a.no_smoke_recovery:
        params.burn.smoke_recovery = False
    geo = GeoRef(pixel_size_m=a.pixel_size, bbox=tuple(a.bbox) if a.bbox else None)
    max_side = a.max_side or None

    if a.batch:
        return _batch(a, params, geo, max_side)

    t0 = time.time()
    an = analyze(a.swir, a.truecolor, a.wildfire, geo=geo, max_side=max_side, params=params)
    save_outputs(an, a.out, base=a.base, debug=not a.no_debug)
    r = an.report
    print(r["descripcion"])
    for w in r["avisos"]:
        print("AVISO:", w)
    print(f"Focos aceptados: {len(r['focos_activos'])} | descartados: {len(r['focos_descartados'])}")
    print(f"Tiempo: {time.time() - t0:.1f} s")
    print(f"Salida: {a.out}")
    return 0


def _batch(a, params: Params, geo: GeoRef, max_side: int | None) -> int:
    try:
        scenes, avisos = discover_scenes(a.batch)
    except NotADirectoryError as exc:
        print(f"ERROR: {exc}")
        return 2
    for w in avisos:
        print("AVISO:", w)
    if not scenes:
        print(f"No se ha encontrado ninguna escena con imagen SWIR en {a.batch}. "
              "Los ficheros se emparejan por nombre: debe haber un 'swir' por carpeta.")
        return 2

    print(f"Escenas encontradas: {len(scenes)}")
    t0 = time.time()
    results = run_batch(scenes, a.out, base=a.base, geo=geo, max_side=max_side, params=params,
                        debug=not a.no_debug)
    index = write_html_index(a.out, results, avisos)

    ok = sum(1 for x in results if x.estado == "ok")
    con_incendio = sum(1 for x in results if x.informe and x.informe["incendio_detectado"])
    fallos = len(results) - ok
    print(f"Resumen: {ok}/{len(results)} escenas analizadas · {con_incendio} con incendio detectado"
          + (f" · {fallos} con error" if fallos else ""))
    print(f"Tiempo total: {time.time() - t0:.1f} s")
    print(f"Índice: {index}")
    return 0 if fallos == 0 else 1
