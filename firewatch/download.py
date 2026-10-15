"""Descarga de escenas Sentinel-2 desde el Copernicus Data Space Ecosystem (CDSE).

Usa el **Process API** de Sentinel Hub dentro del CDSE: la composición renderizada
(PNG) vuelve directamente en la respuesta, sin descargar productos .SAFE de cientos
de megas. Cada escena se pide con **el mismo bbox y tamaño** en las tres
composiciones (SWIR, True Color y Wildfire), así que quedan alineadas por
construcción y listas para `--batch`.

Requisitos: una cuenta gratuita en https://dataspace.copernicus.eu y un cliente
OAuth creado en la consola (Authentication -> OAuth Clients). Las credenciales se
pasan por variables de entorno o por un `.env` local no versionado, **nunca en el
código ni en el repositorio**:

    PowerShell:
        $env:SH_CLIENT_ID = "..."
        $env:SH_CLIENT_SECRET = "..."
    o, en la raíz del repo, un .env (copia .env.example):
        SH_CLIENT_ID=...
        SH_CLIENT_SECRET=...

Se pueden comprobar sin descargar nada:
    python -m firewatch --check-auth

Manifiesto (CSV), una fila por escena:
    nombre,lon_min,lat_min,lon_max,lat_max,desde,hasta
    avila,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31

Uso:
    python -m firewatch --download escenas.csv --out images/descargadas
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
             "protocol/openid-connect/token")
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
FUENTE = "Copernicus Data Space Ecosystem (Sentinel Hub Process API, sentinel-2-l2a)"

# Evalscripts oficiales (repo sentinel-hub/custom-scripts y docs de Sentinel Hub).
# Salen 4 bandas: la última es dataMask, que PIL interpreta como canal alfa.
EVALSCRIPT_SWIR = """//VERSION=3
// SWIR RGB composite (B12, B8A, B04) - Sentinel Hub custom scripts
function setup() {
  return { input: ["B12", "B8A", "B04", "dataMask"], output: { bands: 4 } };
}
function evaluatePixel(sample) {
  return [2.5 * sample.B12, 2.5 * sample.B8A, 2.5 * sample.B04, sample.dataMask];
}
"""

EVALSCRIPT_TRUECOLOR = """//VERSION=3
// True color con compresión de realces (HighlightCompressVisualizer), como en Copernicus Browser
function setup() {
  return { input: ["B02", "B03", "B04", "dataMask"], output: { bands: 4 } };
}
function evaluatePixel(sample) {
  let viz = new HighlightCompressVisualizer(0.0, 0.4);
  let rgb = viz.processList([sample.B04, sample.B03, sample.B02]);
  return [rgb[0], rgb[1], rgb[2], sample.dataMask];
}
"""

# QuickFire V1.0.0 by Pierre Markuse - CC BY 4.0 (script oficial "Wildfire visualization")
EVALSCRIPT_WILDFIRE = """//VERSION=3
// QuickFire V1.0.0 by Pierre Markuse (https://twitter.com/Pierre_Markuse) - CC BY 4.0
function setup() {
  return {
    input: ["B01", "B02", "B03", "B04", "B08", "B8A", "B11", "B12", "CLP", "dataMask"],
    output: { bands: 4 }
  };
}
function stretch(val, min, max) { return (val - min) / (max - min); }
function satEnh(arr, s) {
  var avg = arr.reduce((a, b) => a + b, 0) / arr.length;
  return arr.map(a => avg * (1 - s) + a * s);
}
function layerBlend(lay1, lay2, lay3, op1, op2, op3) {
  return lay1.map(function (num, index) {
    return (num / 100 * op1 + (lay2[index] / 100 * op2) + (lay3[index] / 100 * op3));
  });
}
function evaluatePixel(sample) {
  const hsThreshold = [2.0, 1.5, 1.25, 1.0];
  const hsSensitivity = 1.0;
  const boost = 1;
  const cloudAvoidanceThreshold = 245;
  const avoidanceHelper = 0.8;
  const offset = -0.000;
  const saturation = 1.10;
  const brightness = 1.00;
  const sMin = 0.01;
  const sMax = 0.99;
  const Black = [0, 0, 0];
  const naturalColorsCC = [Math.sqrt(brightness * sample.B04 + offset), Math.sqrt(brightness * sample.B03 + offset), Math.sqrt(brightness * sample.B02 + offset)];
  const naturalColors = [(2.5 * brightness * sample.B04 + offset), (2.5 * brightness * sample.B03 + offset), (2.5 * brightness * sample.B02 + offset)];
  const URBAN = [Math.sqrt(brightness * sample.B12 * 1.2 + offset), Math.sqrt(brightness * sample.B11 * 1.4 + offset), Math.sqrt(brightness * sample.B04 + offset)];
  var Viz = layerBlend(URBAN, naturalColors, naturalColorsCC, 10, 40, 50);
  Viz = satEnh(Viz, saturation);
  for (let i = 0; i < 3; i += 1) { Viz[i] = stretch(Viz[i], sMin, sMax); }
  if ((sample.CLP < cloudAvoidanceThreshold) && (sample.B02 < avoidanceHelper)) {
    if ((sample.B12 + sample.B11) > (hsThreshold[0] / hsSensitivity)) return [((boost * 0.50 * sample.B12) + Viz[0]), ((boost * 0.50 * sample.B11) + Viz[1]), Viz[2], sample.dataMask];
    if ((sample.B12 + sample.B11) > (hsThreshold[1] / hsSensitivity)) return [((boost * 0.50 * sample.B12) + Viz[0]), ((boost * 0.20 * sample.B11) + Viz[1]), Viz[2], sample.dataMask];
    if ((sample.B12 + sample.B11) > (hsThreshold[2] / hsSensitivity)) return [((boost * 0.50 * sample.B12) + Viz[0]), ((boost * 0.10 * sample.B11) + Viz[1]), Viz[2], sample.dataMask];
    if ((sample.B12 + sample.B11) > (hsThreshold[3] / hsSensitivity)) return [((boost * 0.50 * sample.B12) + Viz[0]), Viz[1], Viz[2], sample.dataMask];
  }
  return [Viz[0], Viz[1], Viz[2], sample.dataMask];
}
"""

COMPOSICIONES: dict[str, str] = {
    "swir": EVALSCRIPT_SWIR,
    "truecolor": EVALSCRIPT_TRUECOLOR,
    "wildfire": EVALSCRIPT_WILDFIRE,
}
FICHEROS: dict[str, str] = {k: f"{k}.png" for k in COMPOSICIONES}

# Si más de esta fracción del PNG es transparente, asumimos que no hay producto que cubra la escena.
UMBRAL_SIN_DATOS = 0.9


class ErrorDescarga(RuntimeError):
    """Fallo previsible del proceso de descarga (API, manifiesto, sin datos...)."""


class SinCredenciales(ErrorDescarga):
    pass


def cargar_dotenv(ruta=None) -> dict[str, str]:
    """Lee un `.env` local (no versionado) con SH_CLIENT_ID / SH_CLIENT_SECRET.

    Formato simple: una línea por variable (`CLAVE=valor`), con `#` para comentarios
    y comillas opcionales. No pisa variables de entorno ya definidas.
    """
    p = Path(ruta) if ruta else Path.cwd() / ".env"
    valores: dict[str, str] = {}
    if not p.is_file():
        return valores
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if linea.startswith("export "):
            linea = linea[len("export "):].strip()
        if "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave:
            valores[clave] = valor
    return valores


@dataclass
class Escena:
    """Una fila del manifiesto: zona + rango de fechas a pedir."""
    nombre: str
    bbox: tuple[float, float, float, float]   # lon_min, lat_min, lon_max, lat_max (WGS84)
    desde: str                                 # YYYY-MM-DD
    hasta: str | None = None                   # None = mismo día que `desde`


@dataclass
class ClienteSH:
    """Cliente mínimo del Process API (token + peticiones PNG)."""
    client_id: str
    client_secret: str
    session: object = None                     # requests-like; los tests inyectan el suyo
    _token: str | None = None
    _caduca: float = 0.0

    def __post_init__(self):
        if self.session is None:
            self.session = requests.Session()

    @classmethod
    def desde_entorno(cls, environ=None) -> "ClienteSH":
        if environ is None:
            # Un .env local no versionado, pero las variables de entorno reales mandan.
            env = dict(cargar_dotenv())
            env.update(os.environ)
        else:
            env = environ
        cid = (env.get("SH_CLIENT_ID") or "").strip()
        cs = (env.get("SH_CLIENT_SECRET") or "").strip()
        if not cid or not cs:
            raise SinCredenciales(
                "Faltan credenciales de Copernicus Data Space. Crea una cuenta gratis en "
                "https://dataspace.copernicus.eu, registra un cliente OAuth en la consola "
                "(Authentication -> OAuth Clients) y define SH_CLIENT_ID y SH_CLIENT_SECRET "
                "como variables de entorno, o en un fichero .env local (copia .env.example).")
        return cls(cid, cs)

    def _access_token(self) -> str:
        if self._token and time.time() < self._caduca - 60:
            return self._token
        r = self.session.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if getattr(r, "status_code", 0) != 200:
            pista = (" Revisa SH_CLIENT_ID y SH_CLIENT_SECRET (¿has copiado bien el secret, "
                     "que solo se muestra una vez?).") if getattr(r, "status_code", 0) in (400, 401) else ""
            raise ErrorDescarga(f"No se ha podido obtener el token "
                                f"(HTTP {getattr(r, 'status_code', '?')}): "
                                f"{getattr(r, 'text', '')[:200]}.{pista}")
        data = r.json()
        self._token = data["access_token"]
        self._caduca = time.time() + float(data.get("expires_in", 600))
        return self._token

    def pedir_png(self, solicitud: dict) -> bytes:
        r = self.session.post(PROCESS_URL, json=solicitud, headers={
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "image/png",
        })
        if getattr(r, "status_code", 0) != 200:
            texto = getattr(r, "text", "")[:300]
            pista = ""
            if "meters per pixel" in texto:
                pista = (" La petición es demasiado gruesa: sube --size o usa un bbox/fechas más "
                         "pequeños (S2L2A admite como máximo 1500 m/píxel).")
            raise ErrorDescarga(f"Process API (HTTP {getattr(r, 'status_code', '?')}): {texto}{pista}")
        return r.content


def comprobar_acceso(cliente: ClienteSH, size: int | None = None) -> dict:
    """Valida las credenciales de punta a punta sin descargar ninguna escena.

    Pide un token y una imagen diminuta (no le importa si la zona tiene datos: lo
    que comprueba es que la autenticación y el Process API responden).
    Devuelve un resumen; lanza ErrorDescarga con el motivo si algo falla.
    """
    t0 = time.time()
    cliente._access_token()
    # Zona cualquiera con cobertura habitual (Ávila): solo sirve para validar el acceso.
    bbox = (-4.90, 40.45, -4.55, 40.75)
    if size is None:
        # S2L2A rechaza peticiones de más de 1500 m/píxel: pedimos el mínimo que cumple.
        ancho_m = (bbox[2] - bbox[0]) * 111_320 * math.cos(math.radians((bbox[1] + bbox[3]) / 2))
        alto_m = (bbox[3] - bbox[1]) * 110_574
        size = max(32, math.ceil(max(ancho_m, alto_m) / 1400))
    esc = Escena("comprobacion", bbox, "2026-07-01", "2026-07-31")
    png = cliente.pedir_png(_solicitud(esc, EVALSCRIPT_SWIR, size))
    return {"cliente": cliente.client_id, "png_bytes": len(png), "tamano_px": size,
            "segundos": round(time.time() - t0, 2)}


def leer_manifiesto(ruta) -> list[Escena]:
    """Lee el CSV de escenas. Lanza ErrorDescarga con un mensaje accionable si algo no cuadra."""
    p = Path(ruta)
    if not p.is_file():
        raise ErrorDescarga(f"No existe el manifiesto: {p}")
    with p.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columnas = set(reader.fieldnames or [])
        requeridas = {"nombre", "lon_min", "lat_min", "lon_max", "lat_max", "desde"}
        faltan = requeridas - columnas
        if faltan:
            raise ErrorDescarga(f"El manifiesto no tiene las columnas {sorted(faltan)}. "
                                "Cabecera esperada: nombre,lon_min,lat_min,lon_max,lat_max,desde[,hasta]")
        escenas: list[Escena] = []
        for i, fila in enumerate(reader, start=2):
            nombre = (fila.get("nombre") or "").strip()
            if not nombre:
                continue    # fila en blanco
            try:
                lon_min, lat_min = float(fila["lon_min"]), float(fila["lat_min"])
                lon_max, lat_max = float(fila["lon_max"]), float(fila["lat_max"])
            except (TypeError, ValueError):
                raise ErrorDescarga(f"Fila {i}: el bbox no son números ({fila.get('lon_min')}, ...).")
            if not (lon_min < lon_max and lat_min < lat_max):
                raise ErrorDescarga(f"Fila {i}: bbox inválido; hace falta lon_min < lon_max y "
                                    f"lat_min < lat_max (recibido {lon_min}, {lat_min}, {lon_max}, {lat_max}).")
            desde = (fila.get("desde") or "").strip()
            hasta = (fila.get("hasta") or "").strip() or None
            for etiq, valor in (("desde", desde), ("hasta", hasta)):
                if valor:
                    try:
                        datetime.strptime(valor, "%Y-%m-%d")
                    except ValueError:
                        raise ErrorDescarga(f"Fila {i}: fecha '{etiq}' inválida: {valor} (formato AAAA-MM-DD).")
            if not desde:
                raise ErrorDescarga(f"Fila {i}: falta la columna 'desde'.")
            escenas.append(Escena(nombre=nombre, bbox=(lon_min, lat_min, lon_max, lat_max),
                                  desde=desde, hasta=hasta))
        return escenas


def _solicitud(esc: Escena, evalscript: str, size: int) -> dict:
    d1 = f"{esc.desde}T00:00:00Z"
    d2 = f"{(esc.hasta or esc.desde)}T23:59:59Z"
    return {
        "input": {
            "bounds": {"bbox": list(esc.bbox),
                       "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {"timeRange": {"from": d1, "to": d2}}}],
        },
        "output": {"width": size, "height": size,
                   "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": evalscript,
    }


def _comprobar_datos(esc: Escena, composicion: str, png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    if "A" in img.getbands():
        transp = img.getchannel("A").point(lambda a: 255 if a == 0 else 0)
        frac = sum(transp.histogram()[255:]) / (img.width * img.height)
        if frac > UMBRAL_SIN_DATOS:
            raise ErrorDescarga(
                f"{esc.nombre} ({composicion}): el resultado es casi todo transparente "
                f"({round(100 * frac)} % sin datos). Revisa el bbox y las fechas {esc.desde}"
                + (f"–{esc.hasta}" if esc.hasta else "") +
                ": puede que no haya pasada de Sentinel-2 cubriendo esa zona en ese rango.")
    return img


def _leer_meta(carpeta: Path) -> dict | None:
    """Lee el escena.json de una descarga previa (None si no existe o está corrupto)."""
    p = carpeta / "escena.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _coincide(meta: dict | None, esc: Escena, size: int) -> bool:
    """¿La descarga previa corresponde a la misma petición (bbox, fechas y tamaño)?"""
    if not meta:
        return False
    return (meta.get("bbox") == list(esc.bbox)
            and meta.get("desde") == esc.desde
            and meta.get("hasta") == (esc.hasta or esc.desde)
            and meta.get("tamano_px") == size)


def descargar_escena(esc: Escena, out_dir, cliente: ClienteSH, size: int = 1024,
                     fuerza: bool = False) -> dict:
    """Descarga las 3 composiciones de una escena a `out_dir/<nombre>/`.

    Si la carpeta ya tiene las 3 imágenes **de la misma petición** se omite. Si el
    manifiesto cambió (bbox, fechas o tamaño) y el nombre es el mismo, se vuelve a
    descargar para no quedarse con la versión anterior.

    Devuelve {"estado": "descargada"|"actualizada"|"omitida", "carpeta": str}. Lanza
    ErrorDescarga (dejando la carpeta como estaba) si la API falla o no hay datos.
    """
    carpeta = Path(out_dir) / esc.nombre
    completos = all((carpeta / f).exists() for f in FICHEROS.values())
    al_dia = _coincide(_leer_meta(carpeta), esc, size)
    if not fuerza and completos and al_dia:
        return {"estado": "omitida", "carpeta": str(carpeta)}
    actualizada = completos and not al_dia    # había una descarga, pero de otra petición

    carpeta.mkdir(parents=True, exist_ok=True)
    escritos: list[Path] = []
    try:
        for nombre, evalscript in COMPOSICIONES.items():
            png = cliente.pedir_png(_solicitud(esc, evalscript, size))
            _comprobar_datos(esc, nombre, png)
            destino = carpeta / FICHEROS[nombre]
            destino.write_bytes(png)
            escritos.append(destino)
        meta = {
            "nombre": esc.nombre,
            "bbox": list(esc.bbox),
            "desde": esc.desde,
            "hasta": esc.hasta or esc.desde,
            "tamano_px": size,
            "composiciones": list(COMPOSICIONES),
            "fuente": FUENTE,
            "descargado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (carpeta / "escena.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    except Exception:
        for p in escritos:     # no dejar descargas a medias
            p.unlink(missing_ok=True)
        raise
    return {"estado": "actualizada" if actualizada else "descargada", "carpeta": str(carpeta)}
