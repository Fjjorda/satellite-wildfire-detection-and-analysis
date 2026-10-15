"""Convierte una zona dibujada en el navegador de Copernicus (GeoJSON) a una fila del manifiesto.

En el navegador de Copernicus (<https://browser.dataspace.copernicus.eu>) se puede dibujar un
rectángulo y copiar su geometría como GeoJSON, por ejemplo:

    {"type":"Polygon","coordinates":[[[-4.872405,40.152925],[-4.872405,40.511843],
                                      [-4.114238,40.511843],[-4.114238,40.152925],
                                      [-4.872405,40.152925]]]}

firewatch no trabaja con polígonos, sino con un rectángulo (bbox) en el manifiesto CSV:

    nombre,lon_min,lat_min,lon_max,lat_max,desde,hasta

Este módulo saca los cuatro números (mínimo y máximo de las coordenadas; en GeoJSON cada par
es [longitud, latitud]) y, si quieres, añade la fila a un manifiesto:

    # Pegando el JSON por la entrada estándar
    python -m firewatch.geojson_csv - -n avila-26jul --desde 2026-07-26

    # Desde un fichero, y añadiéndolo al manifiesto (crea la cabecera si falta)
    python -m firewatch.geojson_csv zona.geojson -n avila-26jul --desde 2026-07-26 -o test1.csv
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from .download import ErrorDescarga, leer_manifiesto

CABECERA = "nombre,lon_min,lat_min,lon_max,lat_max,desde,hasta"
LON_LIMITE = (-180.0, 180.0)
LAT_LIMITE = (-90.0, 90.0)


class ErrorZona(RuntimeError):
    """GeoJSON que no se puede convertir (formato raro, coordenadas fuera de rango, etc.)."""


# ------------------------------------------------------------------ conversión

def _pares(nodo) -> list[tuple[float, float]]:
    """Recorre cualquier forma de GeoJSON y devuelve todos los pares [lon, lat].

    Funciona con Polygon, MultiPolygon, Feature, FeatureCollection, GeometryCollection o
    directamente con el array `coordinates`. Ignora `properties`, `id` y `bbox` (no son
    coordenadas, aunque contengan números).
    """
    pares: list[tuple[float, float]] = []
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if clave in ("properties", "bbox", "id"):
                continue
            pares += _pares(valor)
    elif isinstance(nodo, (list, tuple)):
        if (len(nodo) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nodo)):
            pares.append((float(nodo[0]), float(nodo[1])))
        else:
            for valor in nodo:
                pares += _pares(valor)
    return pares


def bbox_de_geojson(obj) -> tuple[float, float, float, float]:
    """Devuelve (lon_min, lat_min, lon_max, lat_max) a partir de un GeoJSON.

    No depende del orden del anillo: usa el mínimo y el máximo de cada eje.
    Lanza ErrorZona con un mensaje accionable si la geometría no vale.
    """
    pares = _pares(obj)
    if not pares:
        raise ErrorZona("no he encontrado coordenadas en el GeoJSON. Pega el objeto completo, "
                        'por ejemplo {"type":"Polygon","coordinates":[[[...]]]}.')
    lons = [x for x, _ in pares]
    lats = [y for _, y in pares]
    fuera_lon = [x for x in lons if not LON_LIMITE[0] <= x <= LON_LIMITE[1]]
    if fuera_lon:
        raise ErrorZona(f"longitud fuera de rango (-180 a 180): {fuera_lon[0]}.")
    fuera_lat = [y for y in lats if not LAT_LIMITE[0] <= y <= LAT_LIMITE[1]]
    if fuera_lat:
        pista = (" ¿Has puesto [latitud, longitud]? En GeoJSON el orden es [longitud, latitud]."
                 if all(LAT_LIMITE[0] <= x <= LAT_LIMITE[1] for x in lons) else "")
        raise ErrorZona(f"latitud fuera de rango (-90 a 90): {fuera_lat[0]}.{pista}")

    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    if lon_min == lon_max or lat_min == lat_max:
        raise ErrorZona("el área tiene ancho o alto cero; hacen falta al menos dos puntos "
                        "distintos en cada eje.")
    if lon_max - lon_min > 180:
        raise ErrorZona(f"el área abarca {lon_max - lon_min:.1f}° de longitud (>180°): "
                        "¿has cruzado el meridiano 180 o pegado coordenadas de otro CRS?")
    return lon_min, lat_min, lon_max, lat_max


def _num(valor: float, decimales: int | None = None) -> str:
    """Número para el CSV: sin decimales forzados conserva la precisión original."""
    if decimales is not None:
        return f"{valor:.{decimales}f}"
    texto = repr(float(valor))
    return texto[:-2] if texto.endswith(".0") else texto


def a_fila_csv(nombre: str, bbox, desde: str | None = None, hasta: str | None = None,
               decimales: int | None = None) -> str:
    """Construye la fila del manifiesto. Sin fechas, deja los marcadores desde,hasta."""
    campos = [nombre] + [_num(v, decimales) for v in bbox]
    campos.append(desde or "desde")
    campos.append(hasta or desde or "hasta")
    return ",".join(campos)


# ------------------------------------------------------------------ CLI

def _nombre_por_defecto(fichero: str | None) -> str:
    if not fichero or fichero == "-":
        return "escena"
    return re.sub(r"[^0-9A-Za-z._-]+", "_", Path(fichero).stem) or "escena"


def _nombre_valido(nombre: str) -> str:
    limpio = (nombre or "").strip()
    if not limpio:
        raise ErrorZona("el nombre no puede estar vacío.")
    if "," in limpio or "\n" in limpio or "\r" in limpio:
        raise ErrorZona("el nombre no puede contener comas ni saltos de línea.")
    return limpio


def _fecha_valida(valor: str, etiqueta: str) -> str:
    valor = (valor or "").strip()
    try:
        datetime.strptime(valor, "%Y-%m-%d")
    except ValueError:
        raise ErrorZona(f"fecha '{etiqueta}' inválida: {valor!r} (formato AAAA-MM-DD).")
    return valor


def _anadir_al_manifiesto(manifiesto, nombre: str, fila: str, fuerza: bool) -> None:
    """Añade la fila al manifiesto (crea la cabecera si hace falta). No duplica nombres."""
    p = Path(manifiesto)
    nuevo = not p.is_file()
    if not nuevo:
        existentes = {e.nombre for e in leer_manifiesto(p)}
        if nombre in existentes and not fuerza:
            raise ErrorZona(f"'{nombre}' ya está en {p}. Usa otro --nombre o --force para añadirlo igual.")
    with p.open("a", encoding="utf-8", newline="") as f:
        if nuevo:
            f.write(CABECERA + "\n")
        f.write(fila + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m firewatch.geojson_csv",
        description="Convierte una zona del navegador de Copernicus (GeoJSON) en una fila del "
                    "manifiesto CSV que usa firewatch --download / --run-manifiesto.")
    ap.add_argument("fichero", nargs="?", default="-",
                    help="Fichero .geojson/.json, o '-' (por defecto) para pegarlo por la entrada estándar.")
    ap.add_argument("-n", "--nombre", help="Nombre de la escena (por defecto, el del fichero).")
    ap.add_argument("--desde", help="Fecha inicial, AAAA-MM-DD.")
    ap.add_argument("--hasta", help="Fecha final, AAAA-MM-DD (por defecto, igual que --desde).")
    ap.add_argument("--decimales", type=int,
                    help="Redondear las coordenadas a N decimales (por defecto, sin redondear).")
    ap.add_argument("-o", "--append", metavar="MANIFIESTO.csv",
                    help="Añade la fila a este manifiesto, creando la cabecera si no existe.")
    ap.add_argument("--force", action="store_true",
                    help="Con --append, añade la fila aunque el nombre ya esté en el manifiesto.")
    a = ap.parse_args(argv)

    try:
        if a.fichero in (None, "-"):
            texto = sys.stdin.read()
        else:
            texto = Path(a.fichero).read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"ERROR: no se ha podido leer {a.fichero}: {exc}")
        return 1

    try:
        obj = json.loads(texto)
    except ValueError as exc:
        print(f"ERROR: el texto no es JSON válido ({exc}). Pega el objeto completo, por ejemplo "
              '{"type":"Polygon","coordinates":[[[...]]]}.')
        return 1

    try:
        bbox = bbox_de_geojson(obj)
        nombre = _nombre_valido(a.nombre or _nombre_por_defecto(a.fichero))
        desde = _fecha_valida(a.desde, "desde") if a.desde else None
        hasta = _fecha_valida(a.hasta, "hasta") if a.hasta else None
        fila = a_fila_csv(nombre, bbox, desde, hasta, a.decimales)
        if desde and hasta and hasta < desde:
            print("  (aviso: --hasta es anterior a --desde; el rango de fechas queda vacío y la "
                  "descarga devolverá 'sin datos')", file=sys.stderr)
        if a.append:
            if not desde:
                raise ErrorZona("para añadir al manifiesto hace falta --desde (si no, la fila no es válida).")
            _anadir_al_manifiesto(a.append, nombre, fila, a.force)
    except ErrorZona as exc:
        print(f"ERROR: {exc}")
        return 1

    print(fila)
    print(f"  bbox: lon {_num(bbox[0])} - {_num(bbox[2])} | lat {_num(bbox[1])} - {_num(bbox[3])}",
          file=sys.stderr)
    if not desde:
        print("  (sin --desde: rellena las fechas 'desde,hasta' de la fila antes de usarla)",
              file=sys.stderr)
    if a.append:
        print(f"  añadida a {a.append}", file=sys.stderr)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
