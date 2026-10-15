"""Procesamiento por lotes: descubre escenas en una carpeta y las analiza en serie.

El emparejamiento de las composiciones es **por nombre de fichero** (sin importar la
carpeta en la que esté cada uno):

    swir.png · *swir*.jpg                     -> SWIR (obligatoria)
    truecolor.png · tc.png · *truecolor*     -> True Color
    wildfire.png · *_Wildfires.jpg            -> Wildfire

Una escena es una carpeta que contiene una imagen SWIR; el resto de composiciones de
esa misma carpeta se le añaden si existen. Las carpetas sin SWIR se omiten con aviso.

Ejemplo:
    python -m firewatch --batch images/ --out resultados/
"""
from __future__ import annotations

import copy
import html
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Params
from .geometry import GeoRef
from .pipeline import analyze, save_outputs

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


@dataclass
class Scene:
    """Unidades de trabajo: una carpeta con una SWIR y sus composiciones acompañantes."""
    nombre: str
    carpeta: Path
    swir: Path
    truecolor: Path | None = None
    wildfire: Path | None = None
    avisos: list[str] = field(default_factory=list)
    geo: GeoRef | None = None    # bbox del sidecar escena.json, si la escena se descargó con --download


@dataclass
class SceneResult:
    scene: Scene
    estado: str                      # "ok" | "error"
    tiempo: float = 0.0
    error: str | None = None
    informe: dict | None = None      # report de pipeline (solo si estado == "ok")
    out_dir: Path | None = None
    overlay: str | None = None       # ruta de resultado.png


def _role(path: Path) -> str | None:
    """Clasifica un fichero por su nombre. Prioridad: nombre exacto > contiene
    'swir' > 'truecolor' > 'wildfire' (para no confundir nombres dobles)."""
    stem = path.stem.lower()
    if stem == "swir":
        return "swir"
    if stem in ("truecolor", "true_color", "tc"):
        return "truecolor"
    if stem == "wildfire":
        return "wildfire"
    if "swir" in stem:
        return "swir"
    if "truecolor" in stem:
        return "truecolor"
    if "wildfire" in stem:      # cubre "…_Wildfires.jpg" de Copernicus Browser
        return "wildfire"
    return None


def _first(paths: list[Path], que: str, avisos: list[str], rel: Path) -> Path:
    if len(paths) > 1:
        avisos.append(f"{rel}: varias imágenes de {que} "
                      f"({', '.join(p.name for p in paths)}); se usa {paths[0].name}.")
    return paths[0]


def _geo_sidecar(carpeta: Path) -> GeoRef | None:
    """Lee el bbox de `escena.json` (sidecar que escribe --download) para dar escala real."""
    p = carpeta / "escena.json"
    if not p.is_file():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        bbox = meta.get("bbox")
        if not bbox or len(bbox) != 4:
            return None
        return GeoRef(bbox=tuple(float(x) for x in bbox))
    except (OSError, ValueError, TypeError):
        return None


def discover_scenes(root) -> tuple[list[Scene], list[str]]:
    """Busca escenas bajo `root` (recursivo). Devuelve (escenas, avisos)."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"No existe la carpeta: {root}")

    by_dir: dict[Path, dict[str, list[Path]]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        role = _role(p)
        if role is None:
            continue
        by_dir.setdefault(p.parent, {}).setdefault(role, []).append(p)

    avisos: list[str] = []
    scenes: list[Scene] = []
    for carpeta in sorted(by_dir):
        roles = by_dir[carpeta]
        rel = carpeta.relative_to(root)
        swirs = sorted(roles.get("swir", []), key=lambda p: p.name.lower())
        if not swirs:
            nombres = ", ".join(p.name for ps in roles.values() for p in sorted(ps))
            avisos.append(f"{rel.as_posix()}: hay {nombres} pero ninguna imagen SWIR; "
                          "se omite (toda la lógica necesita la composición SWIR).")
            continue
        if len(swirs) > 1:
            avisos.append(f"{rel.as_posix()}: varias imágenes SWIR "
                          f"({', '.join(p.name for p in swirs)}); se usa {swirs[0].name}.")
        tcs = sorted(roles.get("truecolor", []), key=lambda p: p.name.lower())
        wfs = sorted(roles.get("wildfire", []), key=lambda p: p.name.lower())
        nombre = "-".join(rel.parts) if rel.parts else "escena"
        sc = Scene(nombre=nombre, carpeta=carpeta, swir=swirs[0],
                   truecolor=_first(tcs, "True Color", avisos, rel) if tcs else None,
                   wildfire=_first(wfs, "Wildfire", avisos, rel) if wfs else None,
                   geo=_geo_sidecar(carpeta))
        scenes.append(sc)

    # Nombres únicos (dos carpetas distintas pueden llamarse igual)
    usados: dict[str, int] = {}
    for sc in scenes:
        n = usados.get(sc.nombre, 0) + 1
        usados[sc.nombre] = n
        if n > 1:
            sc.nombre = f"{sc.nombre}-{n}"
    return scenes, avisos


def run_batch(scenes: list[Scene], out_root, *, base: str = "swir",
              geo: GeoRef | None = None, max_side: int | None = 2500,
              params: Params | None = None, debug: bool = True, log=print) -> list[SceneResult]:
    """Analiza las escenas en serie. Un fallo en una no detiene el lote."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    results: list[SceneResult] = []
    total = len(scenes)
    for i, sc in enumerate(scenes, 1):
        t0 = time.time()
        out_dir = out_root / sc.nombre
        log(f"[{i}/{total}] {sc.nombre} …", end="")
        # El bbox del sidecar (escena descargada) tiene prioridad sobre el --bbox global.
        g = sc.geo if sc.geo is not None else geo
        try:
            an = analyze(sc.swir, sc.truecolor, sc.wildfire,
                         params=copy.deepcopy(params) if params is not None else None,
                         geo=GeoRef(pixel_size_m=g.pixel_size_m, bbox=g.bbox) if g else None,
                         max_side=max_side)
            files = save_outputs(an, out_dir, base=base, debug=debug)
        except Exception as exc:  # una imagen rota no debe abortar el lote
            dt = time.time() - t0
            log(f" ERROR ({dt:.1f} s): {exc}")
            results.append(SceneResult(sc, "error", dt, error=str(exc)))
            continue
        dt = time.time() - t0
        r = an.report
        estado = "incendio detectado" if r["incendio_detectado"] else "sin incendio"
        log(f" OK ({dt:.1f} s) — {estado}, {len(r['focos_activos'])} focos")
        results.append(SceneResult(sc, "ok", dt, informe=r, out_dir=out_dir,
                                   overlay=files.get("overlay")))
    return results


# ------------------------------------------------------------------ resumen

def _fmt(x: float, nd: int = 0) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _celda_escena(res: SceneResult, out_root: Path) -> str:
    sc = res.scene
    nombre = html.escape(sc.nombre)
    partes = [f'<strong>{nombre}</strong>']
    if res.out_dir is not None:
        rel = res.out_dir.relative_to(out_root).as_posix()
        for fich, texto in (("resultado.png", "imagen"),
                            ("informe.json", "informe"),
                            ("diagnostico.png", "diagnóstico")):
            if (res.out_dir / fich).exists():
                partes.append(f'<a href="{rel}/{fich}">{texto}</a>')
    return "<br>".join(partes)


def _celda_miniatura(res: SceneResult, out_root: Path) -> str:
    if res.out_dir is None or res.overlay is None:
        return "—"
    rel = Path(res.overlay).relative_to(out_root).as_posix()
    alt = html.escape((res.informe or {}).get("descripcion", "")[:300])
    return (f'<a href="{rel}"><img src="{rel}" alt="{alt}" width="220" '
            f'loading="lazy"></a>')


def _celda_superficie(r: dict) -> str:
    a = r["area_quemada"]
    if "hectareas" in a:
        lo, hi = a["hectareas_rango"]
        return f"{_fmt(lo)}–{_fmt(hi)} ha"
    return f"{_fmt(a['porcentaje_imagen'], 1)} % de la imagen"


def _celda_perimetro(r: dict) -> str:
    p = r["perimetro"]
    if "km" in p:
        return f"{_fmt(p['km'], 1)} km"
    return f"{_fmt(p['pixeles'])} px"


def write_html_index(out_root, results: list[SceneResult], avisos: list[str] = ()) -> Path:
    """Índice accesible (sin JS ni red) con una fila por escena."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    ok = [x for x in results if x.estado == "ok"]
    errores = [x for x in results if x.estado == "error"]
    con_incendio = sum(1 for x in ok if x.informe["incendio_detectado"])

    filas = []
    for res in results:
        r = res.informe
        if r is not None:
            focos = str(len(r["focos_activos"]))
            avisos_escena = list(res.scene.avisos) + list(r["avisos"])
            estado = '<td class="ok">correcto</td>'
        else:
            focos = "—"
            avisos_escena = list(res.scene.avisos)
            estado = (f'<td class="error" role="status">ERROR: '
                      f'{html.escape(res.error or "fallo desconocido")}</td>')
        celda_avisos = ("<ul class='avisos'>" + "".join(
            f"<li>{html.escape(a)}</li>" for a in avisos_escena) + "</ul>") if avisos_escena else "—"
        filas.append(
            "<tr>"
            f"<th scope='row'>{_celda_escena(res, out_root)}</th>"
            f"<td class='mini'>{_celda_miniatura(res, out_root)}</td>"
            f"<td>{'Sí' if (r and r['incendio_detectado']) else ('No' if r else '—')}</td>"
            f"<td>{_celda_superficie(r) if r else '—'}</td>"
            f"<td>{_celda_perimetro(r) if r else '—'}</td>"
            f"<td class='num'>{focos}</td>"
            f"<td>{f'{res.tiempo:.1f} s'}</td>"
            f"<td>{celda_avisos}</td>"
            f"{estado}"
            "</tr>")

    avisos_html = ""
    if avisos:
        avisos_html = ("<h2>Avisos de la búsqueda</h2><ul>"
                       + "".join(f"<li>{html.escape(a)}</li>" for a in avisos)
                       + "</ul>")

    doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>firewatch · resumen del lote</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 75rem;
         padding: 0 1rem; color: #1b1b1b; background: #fff; line-height: 1.45; }}
  h1 {{ font-size: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }}
  caption {{ text-align: left; font-weight: 600; padding-bottom: .5rem; }}
  th, td {{ border: 1px solid #b8b8b8; padding: .5rem .6rem; vertical-align: top;
           text-align: left; }}
  thead th {{ background: #eceff1; position: sticky; top: 0; }}
  tbody th {{ font-weight: 600; }}
  td.mini img {{ display: block; border: 1px solid #b8b8b8; height: auto; }}
  td.num {{ text-align: right; }}
  td.ok {{ background: #dff2e9; color: #0a3b2a; font-weight: 600; white-space: nowrap; }}
  td.error {{ background: #fbe4d8; color: #7a2e00; font-weight: 600; }}
  ul.avisos {{ margin: 0; padding-left: 1.1rem; font-size: .82rem; }}
  a {{ color: #0b5394; }}
  a:focus-visible {{ outline: 3px solid #0b5394; outline-offset: 2px; }}
  .totales {{ font-size: 1rem; }}
</style>
</head>
<body>
<h1>firewatch · resumen del lote</h1>
<p class="totales">{len(results)} escenas · {len(ok)} correctas · {len(errores)} con error ·
{con_incendio} con incendio detectado · generado el
{datetime.now().strftime('%d/%m/%Y %H:%M')}.</p>
{avisos_html}
<table>
<caption>Resultados por escena. Cada miniatura enlaza a la imagen anotada.</caption>
<thead>
<tr>
  <th scope="col">Escena</th>
  <th scope="col">Resultado</th>
  <th scope="col">Incendio</th>
  <th scope="col">Superficie</th>
  <th scope="col">Perímetro</th>
  <th scope="col">Focos</th>
  <th scope="col">Tiempo</th>
  <th scope="col">Avisos</th>
  <th scope="col">Estado</th>
</tr>
</thead>
<tbody>
{chr(10).join(filas)}
</tbody>
</table>
</body>
</html>
"""
    p = out_root / "resumen.html"
    p.write_text(doc, encoding="utf-8")
    return p
