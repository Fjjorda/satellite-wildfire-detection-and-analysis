"""Línea de comandos.

Ejemplo:
    python -m firewatch --swir swir.png --truecolor tc.png --out resultados/avila
"""
from __future__ import annotations

import argparse
import time
import warnings
from pathlib import Path

from .batch import discover_scenes, run_batch, write_html_index
from .config import Params
from .download import (ClienteSH, ErrorDescarga, SinCredenciales, comprobar_acceso,
                       descargar_escena, leer_manifiesto)
from .geometry import GeoRef
from .pipeline import analyze, save_outputs


def main(argv=None) -> int:
    _blindar_consola()
    ap = argparse.ArgumentParser(prog="firewatch",
                                 description="Delimita el área quemada y detecta focos activos.")
    ap.add_argument("--swir", required=True, help="Composición SWIR (B12-B8A-B4). Obligatoria.")
    ap.add_argument("--truecolor", help="Composición True Color de la misma escena (recomendada).")
    ap.add_argument("--wildfire", help="Composición Wildfire de la misma escena (opcional).")
    ap.add_argument("--out", required=True, help="Carpeta de salida.")
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
    if not (a.imagenes or a.swir or a.truecolor or a.lote):
        ap.error("indica al menos una imagen de entrada: --swir y/o --truecolor, --imagenes, o --lote. "
                 "La Wildfire no es una entrada: va en --validar-con.")

    modos = [a.swir, a.batch, a.download, a.run_manifiesto]
    if a.check_auth:
        if any(modos):
            ap.error("--check-auth no se combina con otros modos.")
        return _check_auth()

    if sum(bool(m) for m in modos) != 1:
        ap.error("indica exactamente un modo: --swir (una escena), --batch CARPETA (lote), "
                 "--download MANIFIESTO (descarga) o --run-manifiesto MANIFIESTO (descarga + lote).")
    if not a.out:
        ap.error("--out es obligatorio salvo con --check-auth.")

    params = Params()
    if a.no_smoke_recovery:
        params.burn.smoke_recovery = False
    t0 = time.time()
    an = analyze(a.swir, a.truecolor, a.wildfire,
                 geo=GeoRef(pixel_size_m=a.pixel_size, bbox=tuple(a.bbox) if a.bbox else None),
                 max_side=a.max_side or None, params=params)
    files = save_outputs(an, a.out, base=a.base, debug=not a.no_debug)
    r = an.report
    print(r["descripcion"])
    for w in r["avisos"]:
        print("AVISO:", w)
    print(f"Focos aceptados: {len(r['focos_activos'])} | descartados: {len(r['focos_descartados'])}")
    print(f"Tiempo: {time.time() - t0:.1f} s")
    for k, v in files.items():
        print(f"  {k}: {v}")
    return 0


def _descargar_todo(manifiesto, out_dir, size, force) -> tuple[int, int, int, int, list[str]]:
    """Descarga todas las escenas del manifiesto.

    Devuelve (descargadas, omitidas, fallos, total, usables). `usables` son las escenas
    con descarga correcta (o ya al día): las que fallan se ignoran y no se analizan.
    `total` es 0 si el manifiesto no se pudo leer o faltan credenciales.
    """
    try:
        escenas = leer_manifiesto(manifiesto)
        cliente = ClienteSH.desde_entorno()
    except (ErrorDescarga, SinCredenciales) as exc:
        print(f"ERROR: {exc}")
        return 0, 0, 1, 0, []
    if not escenas:
        print(f"El manifiesto {manifiesto} no contiene ninguna escena.")
        return 0, 0, 1, 0, []

    print(f"Escenas en el manifiesto: {len(escenas)}")
    descargadas = omitidas = fallos = 0
    usables: list[str] = []
    for i, esc in enumerate(escenas, 1):
        print(f"[{i}/{len(escenas)}] {esc.nombre} ({esc.desde}"
              + (f"–{esc.hasta}" if esc.hasta else "") + ") …", end="")
        try:
            res = descargar_escena(esc, out_dir, cliente, size=size, fuerza=force)
        except ErrorDescarga as exc:
            print(f" ERROR: {exc}")
            fallos += 1
            continue
        usables.append(esc.nombre)
        if res["estado"] == "omitida":
            print(" ya descargada, se omite")
            omitidas += 1
        elif res["estado"] == "actualizada":
            print(f" actualizada (el manifiesto cambió) -> {res['carpeta']}")
            descargadas += 1
        else:
            print(f" OK -> {res['carpeta']}")
            descargadas += 1
    print(f"Descarga: {descargadas} nuevas · {omitidas} ya existentes"
          + (f" · {fallos} con error (se ignoran)" if fallos else ""))
    return descargadas, omitidas, fallos, len(escenas), usables


def _download(a) -> int:
    t0 = time.time()
    descargadas, omitidas, fallos, total, usables = _descargar_todo(a.download, a.out, a.size, a.force)
    if total == 0:
        return 2
    print(f"Tiempo: {time.time() - t0:.1f} s")
    print(f"Imágenes en: {a.out}")
    print("Siguiente paso: python -m firewatch --batch "
          f"\"{a.out}\" --out resultados/lote")
    return 0 if fallos == 0 else 1


def _escenas_del_manifiesto(images_out, usables: list[str]) -> tuple[list, list[str]]:
    """De todo lo que hay en `images_out`, quédate solo con las escenas del manifiesto.

    Se ignoran (con aviso) tanto las descargas anteriores que no están en el
    manifiesto como las escenas del manifiesto que fallaron (no están en `usables`).
    """
    scenes, avisos = discover_scenes(images_out)
    pedidas = set(usables)
    del_manifiesto = [sc for sc in scenes if sc.nombre in pedidas]
    ajenas = [sc.nombre for sc in scenes if sc.nombre not in pedidas]
    if ajenas:
        avisos.append(f"se ignoran {len(ajenas)} escenas de {images_out} que no están en el "
                      f"manifiesto o fallaron: {', '.join(sorted(ajenas))}")
    return del_manifiesto, avisos


def _run_manifiesto(a, params: Params, geo: GeoRef, max_side: int | None) -> int:
    t0 = time.time()
    descargadas, omitidas, fallos, total, usables = _descargar_todo(a.run_manifiesto,
                                                                   a.images_out, a.size, a.force)
    if total == 0:
        return 2
    scenes, avisos = _escenas_del_manifiesto(a.images_out, usables)
    for w in avisos:
        print("AVISO:", w)
    if fallos:
        print(f"AVISO: {fallos} escena(s) con error de descarga se ignoran en el análisis.")
    if not scenes:
        print(f"No hay ninguna escena del manifiesto con imagen SWIR en {a.images_out}. "
              "¿Han fallado todas las descargas?")
        return 2
    print(f"Escenas a analizar: {len(scenes)}")
    results = run_batch(scenes, a.out, base=a.base, geo=geo, max_side=max_side, params=params,
                        debug=not a.no_debug)
    index = write_html_index(a.out, results, avisos)
    ok = sum(1 for x in results if x.estado == "ok")
    fallos_analisis = len(results) - ok
    print(f"Análisis: {ok}/{len(results)} escenas correctas")
    print(f"Tiempo total: {time.time() - t0:.1f} s")
    print(f"Índice: {index}")
    return 0 if (fallos == 0 and fallos_analisis == 0) else 1


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
