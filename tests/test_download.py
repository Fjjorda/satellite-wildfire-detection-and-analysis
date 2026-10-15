"""Tests del módulo de descarga (CDSE/Sentinel Hub) con la red simulada.

Ningún test toca la red: la sesión HTTP se sustituye por un doble que devuelve
PNG generados por el propio test.

Ejecutar:  python -m pytest -q
"""
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from firewatch import download as dl
from firewatch.batch import discover_scenes, run_batch
from firewatch.cli import main

from synthetic import make_scene

# Referencia al cargador real: el fixture autouse lo sustituye para que ningún test
# lea un .env del usuario; los tests de dotenv lo restauran a propósito.
_CARGAR_DOTENV_REAL = dl.cargar_dotenv


@pytest.fixture(autouse=True)
def _sin_dotenv(monkeypatch):
    """Aísla los tests de un .env real en el directorio de trabajo."""
    monkeypatch.setattr(dl, "cargar_dotenv", lambda *a, **k: {})


# ------------------------------------------------------------- dobles falsos

class Respuesta:
    def __init__(self, status=200, content=b"", json_data=None, text=""):
        self.status_code = status
        self.content = content
        self.text = text or content.decode("utf-8", "replace")
        self._json = json_data

    def json(self):
        return self._json


def _png_bytes(arr, alpha=255):
    """PNG RGBA (la 4ª banda es el dataMask que devuelve la API)."""
    h, w = arr.shape[:2]
    rgba = np.dstack([arr, np.full((h, w), alpha, np.uint8)])
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


class FakeSession:
    """Doble de requests.Session: token + Process API."""
    def __init__(self, por_composicion=None, alpha=255):
        # por_composicion: {"swir": bytes_png, ...}; por defecto, un pixel gris
        self.por_composicion = por_composicion or {}
        self.alpha = alpha
        self.llamadas_token = 0
        self.solicitudes = []          # peticiones al Process API

    def post(self, url, **kw):
        if "identity.dataspace" in url:
            self.llamadas_token += 1
            return Respuesta(200, json_data={"access_token": "tok-123", "expires_in": 600})
        req = kw["json"]
        self.solicitudes.append(req)
        kind = _cual(req["evalscript"])
        png = self.por_composicion.get(kind) or _png_bytes(np.full((8, 8, 3), 120, np.uint8),
                                                           alpha=self.alpha)
        return Respuesta(200, content=png)


def _cual(evalscript: str) -> str:
    if "QuickFire" in evalscript:
        return "wildfire"
    if "HighlightCompressVisualizer" in evalscript:
        return "truecolor"
    return "swir"


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    """ClienteSH con red simulada; también sirve para los tests de CLI."""
    def _make(session):
        c = dl.ClienteSH("id", "secret", session=session)
        return c
    return _make


def _escribe_manifiesto(tmp_path, filas, nombre="escenas.csv"):
    p = tmp_path / nombre
    p.write_text("nombre,lon_min,lat_min,lon_max,lat_max,desde,hasta\n" + filas,
                 encoding="utf-8")
    return p


# ------------------------------------------------------------ manifiesto

def test_leer_manifiesto_ok(tmp_path):
    p = _escribe_manifiesto(tmp_path, "avila,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31\n"
                                       "sin_hasta,-6.95,37.30,-6.80,37.40,2026-07-21,\n")
    escenas = dl.leer_manifiesto(p)
    assert [e.nombre for e in escenas] == ["avila", "sin_hasta"]
    assert escenas[0].bbox == (-4.85, 40.50, -4.60, 40.70)
    assert escenas[0].hasta == "2026-07-31"
    assert escenas[1].hasta is None       # vacío -> mismo día que `desde`


def test_leer_manifiesto_filas_en_blanco_se_ignoran(tmp_path):
    p = _escribe_manifiesto(tmp_path, "a,-4.0,40.0,-3.9,40.1,2026-07-01,\n"
                                       ",,,,,,\n")
    assert [e.nombre for e in dl.leer_manifiesto(p)] == ["a"]


@pytest.mark.parametrize("contenido,fragmento", [
    ("nombre,desde\navila,2026-07-01\n", "lon_min"),
    ("nombre,lon_min,lat_min,lon_max,lat_max,desde\navila,x,40,-3.9,40.1,2026-07-01\n", "números"),
    ("nombre,lon_min,lat_min,lon_max,lat_max,desde\navila,-3.9,40,-4.0,40.1,2026-07-01\n", "lon_min < lon_max"),
    ("nombre,lon_min,lat_min,lon_max,lat_max,desde\navila,-4,40,-3.9,40.1,01/07/2026\n", "inválida"),
    ("nombre,lon_min,lat_min,lon_max,lat_max,desde\navila,-4,40,-3.9,40.1,\n", "'desde'"),
])
def test_leer_manifiesto_errores_accionables(tmp_path, contenido, fragmento):
    p = tmp_path / "m.csv"
    p.write_text(contenido, encoding="utf-8")
    with pytest.raises(dl.ErrorDescarga) as exc:
        dl.leer_manifiesto(p)
    assert fragmento in str(exc.value)


def test_manifiesto_inexistente(tmp_path):
    with pytest.raises(dl.ErrorDescarga):
        dl.leer_manifiesto(tmp_path / "no.csv")


# ------------------------------------------------------------- descarga

def test_descarga_tres_composiciones_mismo_bbox(tmp_path, cliente):
    ses = FakeSession()
    esc = dl.Escena("prueba", (-4.85, 40.50, -4.60, 40.70), "2026-07-21")
    res = dl.descargar_escena(esc, tmp_path, cliente(ses), size=512)
    assert res["estado"] == "descargada"

    carpeta = tmp_path / "prueba"
    assert all((carpeta / f).exists() for f in ("swir.png", "truecolor.png", "wildfire.png"))
    # Las tres composiciones: mismo bbox, mismo tamaño, mismo rango de fechas
    assert len(ses.solicitudes) == 3
    bboxes = {json.dumps(s["input"]["bounds"]["bbox"]) for s in ses.solicitudes}
    sizes = {(s["output"]["width"], s["output"]["height"]) for s in ses.solicitudes}
    assert bboxes == {json.dumps([-4.85, 40.50, -4.60, 40.70])}
    assert sizes == {(512, 512)}
    for s in ses.solicitudes:
        tr = s["input"]["data"][0]["dataFilter"]["timeRange"]
        assert tr == {"from": "2026-07-21T00:00:00Z", "to": "2026-07-21T23:59:59Z"}

    meta = json.loads((carpeta / "escena.json").read_text(encoding="utf-8"))
    assert meta["bbox"] == [-4.85, 40.50, -4.60, 40.70]
    assert meta["tamano_px"] == 512
    assert meta["composiciones"] == ["swir", "truecolor", "wildfire"]


def test_token_se_pide_una_sola_vez(tmp_path, cliente):
    ses = FakeSession()
    c = cliente(ses)
    for i in range(3):
        dl.descargar_escena(dl.Escena(f"s{i}", (-4, 40, -3.9, 40.1), "2026-07-21"), tmp_path, c)
    assert ses.llamadas_token == 1


def test_descarga_omite_existente_y_force(tmp_path, cliente):
    ses = FakeSession()
    c = cliente(ses)
    esc = dl.Escena("prueba", (-4, 40, -3.9, 40.1), "2026-07-21")
    dl.descargar_escena(esc, tmp_path, c)
    assert dl.descargar_escena(esc, tmp_path, c)["estado"] == "omitida"
    assert len(ses.solicitudes) == 3      # no se ha vuelto a pedir nada
    dl.descargar_escena(esc, tmp_path, c, fuerza=True)
    assert len(ses.solicitudes) == 6


def test_escena_sin_datos_se_marca_como_error(tmp_path, cliente):
    ses = FakeSession(alpha=0)      # todo transparente = sin producto que cubra la escena
    esc = dl.Escena("vacia", (-4, 40, -3.9, 40.1), "2026-07-21")
    with pytest.raises(dl.ErrorDescarga) as exc:
        dl.descargar_escena(esc, tmp_path, cliente(ses))
    assert "sin datos" in str(exc.value)
    assert not (tmp_path / "vacia" / "swir.png").exists()   # sin descargas a medias


def test_error_http_incluye_codigo(tmp_path):
    class SesionRota:
        def post(self, url, **kw):
            if "identity" in url:
                return Respuesta(200, json_data={"access_token": "t", "expires_in": 600})
            return Respuesta(500, text="boom")
    c = dl.ClienteSH("id", "secret", session=SesionRota())
    with pytest.raises(dl.ErrorDescarga) as exc:
        dl.descargar_escena(dl.Escena("s", (-4, 40, -3.9, 40.1), "2026-07-21"), tmp_path, c)
    assert "HTTP 500" in str(exc.value)


def test_credenciales_desde_entorno():
    with pytest.raises(dl.SinCredenciales):
        dl.ClienteSH.desde_entorno({})
    c = dl.ClienteSH.desde_entorno({"SH_CLIENT_ID": "a", "SH_CLIENT_SECRET": "b"})
    assert c.client_id == "a"


# ------------------------------------------------------------------ CLI

def _png_escena_sintetica(tmp_path, swir, nombre="sint.png"):
    p = tmp_path / nombre
    Image.fromarray(np.dstack([swir, np.full(swir.shape[:2], 255, np.uint8)]), "RGBA").save(p)
    return p


@pytest.fixture
def imagenes_simuladas(tmp_path):
    """PNG sintéticos (SWIR y True Color) de dos escenas, etiquetadas por bbox."""
    swir, tc, _ = make_scene()
    swir2, tc2, _ = make_scene(seed=1, burned=False, edge_fire=False, isolated_hot=False)
    return {
        "escena1": {"swir": _png_escena_sintetica(tmp_path, swir, nombre="s1_swir.png"),
                    "truecolor": _png_escena_sintetica(tmp_path, tc, nombre="s1_tc.png")},
        "escena2": {"swir": _png_escena_sintetica(tmp_path, swir2, nombre="s2_swir.png"),
                    "truecolor": _png_escena_sintetica(tmp_path, tc2, nombre="s2_tc.png")},
    }


def _hacer_post(imagenes, fallar=()):
    """Devuelve el `post` falso: sirve la imagen de la escena según su bbox y la composición.

    `fallar` es un conjunto de nombres de escena que responden HTTP 500 (para probar
    que una escena que falla se ignora).
    """
    def post(self, url, **kw):
        if "identity" in url:
            self.llamadas_token += 1
            return Respuesta(200, json_data={"access_token": "tok", "expires_in": 600})
        req = kw["json"]
        self.solicitudes.append(req)
        bbox = req["input"]["bounds"]["bbox"]
        nombre = "escena1" if bbox[0] < -4.5 else "escena2"
        if nombre in fallar:
            return Respuesta(500, text="boom")
        kind = _cual(req["evalscript"])
        ruta = imagenes[nombre].get(kind)
        if ruta is None:
            return Respuesta(200, content=_png_bytes(np.full((4, 4, 3), 120, np.uint8)))
        return Respuesta(200, content=Path(ruta).read_bytes())
    return post


@pytest.fixture
def entorno_simulado(tmp_path, monkeypatch, imagenes_simuladas):
    """Credenciales en el entorno + red simulada que devuelve escenas sintéticas reales."""
    monkeypatch.setenv("SH_CLIENT_ID", "id")
    monkeypatch.setenv("SH_CLIENT_SECRET", "secret")

    class FakeRequests:
        @staticmethod
        def Session():
            return FakeSession()

    monkeypatch.setattr(dl, "requests", FakeRequests)
    monkeypatch.setattr(FakeSession, "post", _hacer_post(imagenes_simuladas))
    return tmp_path


def test_cli_download_ok(entorno_simulado, capsys):
    manifiesto = _escribe_manifiesto(entorno_simulado,
                                     "escena1,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31\n"
                                     "escena2,-3.20,40.90,-3.05,41.00,2026-07-01,2026-07-31\n")
    out = entorno_simulado / "descargas"
    assert main(["--download", str(manifiesto), "--out", str(out)]) == 0
    assert (out / "escena1" / "swir.png").exists()
    assert (out / "escena2" / "escena.json").exists()
    assert "2 nuevas" in capsys.readouterr().out


def test_cli_download_segunda_vez_omite(entorno_simulado, capsys):
    manifiesto = _escribe_manifiesto(entorno_simulado,
                                     "escena1,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31\n")
    out = entorno_simulado / "descargas"
    main(["--download", str(manifiesto), "--out", str(out)])
    capsys.readouterr()
    assert main(["--download", str(manifiesto), "--out", str(out)]) == 0
    assert "1 ya existentes" in capsys.readouterr().out


def test_cli_download_sin_credenciales_devuelve_2(entorno_simulado, monkeypatch, capsys):
    monkeypatch.delenv("SH_CLIENT_ID")
    manifiesto = _escribe_manifiesto(entorno_simulado,
                                     "escena1,-4.85,40.50,-4.60,40.70,2026-07-01,\n")
    assert main(["--download", str(manifiesto), "--out", str(entorno_simulado / "d")]) == 2
    assert "SH_CLIENT_ID" in capsys.readouterr().out


def test_cli_download_manifiesto_invalido_devuelve_2(entorno_simulado, capsys):
    p = entorno_simulado / "malo.csv"
    p.write_text("nombre,desde\navila,2026-01-01\n", encoding="utf-8")
    assert main(["--download", str(p), "--out", str(entorno_simulado / "d")]) == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_run_manifiesto_descarga_y_analiza(entorno_simulado, capsys):
    manifiesto = _escribe_manifiesto(entorno_simulado,
                                     "escena1,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31\n"
                                     "escena2,-3.20,40.90,-3.05,41.00,2026-07-01,2026-07-31\n")
    imgs = entorno_simulado / "imgs"
    out = entorno_simulado / "resultados"
    code = main(["--run-manifiesto", str(manifiesto), "--out", str(out),
                 "--images-out", str(imgs), "--no-debug"])
    assert code == 0
    assert (imgs / "escena1" / "escena.json").exists()
    assert (out / "resumen.html").exists()
    # El bbox del sidecar llega al análisis: hay hectáreas sin pasar --bbox
    informe = json.loads((out / "escena1" / "informe.json").read_text(encoding="utf-8"))
    assert "hectareas" in informe["area_quemada"]
    assert "2/2 escenas correctas" in capsys.readouterr().out


def test_run_manifiesto_ignora_escenas_ajenas(entorno_simulado, capsys):
    """Una descarga anterior que no está en el manifiesto no se analiza."""
    imgs = entorno_simulado / "imgs"
    swir, tc, _ = make_scene(seed=7)
    (imgs / "vieja").mkdir(parents=True)
    Image.fromarray(swir).save(imgs / "vieja" / "swir.png")
    Image.fromarray(tc).save(imgs / "vieja" / "truecolor.png")

    manifiesto = _escribe_manifiesto(entorno_simulado,
                                     "escena1,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31\n")
    out = entorno_simulado / "res"
    code = main(["--run-manifiesto", str(manifiesto), "--out", str(out),
                 "--images-out", str(imgs), "--no-debug"])
    assert code == 0
    assert (out / "escena1").exists()
    assert not (out / "vieja").exists()          # la escena ajena no se analiza
    salida = capsys.readouterr().out
    assert "se ignoran" in salida and "vieja" in salida
    assert "1/1 escenas correctas" in salida


def test_run_manifiesto_ignora_escenas_que_fallan(entorno_simulado, imagenes_simuladas,
                                                 monkeypatch, capsys):
    """Si la descarga de una escena del manifiesto falla, se ignora y el lote sigue."""
    monkeypatch.setattr(FakeSession, "post", _hacer_post(imagenes_simuladas, fallar={"escena2"}))
    manifiesto = _escribe_manifiesto(entorno_simulado,
                                     "escena1,-4.85,40.50,-4.60,40.70,2026-07-01,2026-07-31\n"
                                     "escena2,-3.20,40.90,-3.05,41.00,2026-07-01,2026-07-31\n")
    imgs = entorno_simulado / "imgs"
    out = entorno_simulado / "res"
    code = main(["--run-manifiesto", str(manifiesto), "--out", str(out),
                 "--images-out", str(imgs), "--no-debug"])
    assert code == 1                              # hubo fallos, pero el resto se analizó
    salida = capsys.readouterr().out
    assert "se ignoran en el análisis" in salida
    assert (out / "escena1").exists()
    assert not (out / "escena2").exists()
    assert "1/1 escenas correctas" in salida


def test_descarga_repite_si_cambia_el_manifiesto(tmp_path, cliente):
    """Cambiar fechas o tamaño de una fila (mismo nombre) obliga a re-descargar."""
    ses = FakeSession()
    c = cliente(ses)
    esc = dl.Escena("s", (-4, 40, -3.9, 40.1), "2026-07-21")
    assert dl.descargar_escena(esc, tmp_path, c)["estado"] == "descargada"
    assert len(ses.solicitudes) == 3
    assert dl.descargar_escena(esc, tmp_path, c)["estado"] == "omitida"
    assert len(ses.solicitudes) == 3              # sin peticiones nuevas

    # Otro tamaño: la descarga guardada ya no vale.
    assert dl.descargar_escena(esc, tmp_path, c, size=512)["estado"] == "actualizada"
    assert len(ses.solicitudes) == 6
    assert dl.descargar_escena(esc, tmp_path, c, size=512)["estado"] == "omitida"

    # Otro rango de fechas: se vuelve a descargar y el sidecar queda al día.
    esc2 = dl.Escena("s", (-4, 40, -3.9, 40.1), "2026-07-21", "2026-07-31")
    assert dl.descargar_escena(esc2, tmp_path, c)["estado"] == "actualizada"
    assert len(ses.solicitudes) == 9
    meta = json.loads((tmp_path / "s" / "escena.json").read_text(encoding="utf-8"))
    assert meta["hasta"] == "2026-07-31" and meta["tamano_px"] == 1024


def test_cli_modos_excluyentes():
    with pytest.raises(SystemExit):
        main(["--out", "x"])                                     # sin modo
    with pytest.raises(SystemExit):
        main(["--download", "m.csv", "--batch", "img", "--out", "x"])   # dos modos
    with pytest.raises(SystemExit):
        main(["--download", "m.csv"])                            # sin --out


def test_cli_check_auth_no_se_combina():
    with pytest.raises(SystemExit):
        main(["--check-auth", "--batch", "img"])


# --------------------------------------------------- credenciales (--check-auth / .env)

def test_check_auth_ok(entorno_simulado, capsys):
    assert main(["--check-auth"]) == 0
    salida = capsys.readouterr().out
    assert "OK" in salida
    assert "válidas" in salida


def test_check_auth_sin_credenciales_devuelve_2(entorno_simulado, monkeypatch, capsys):
    monkeypatch.delenv("SH_CLIENT_ID", raising=False)
    monkeypatch.delenv("SH_CLIENT_SECRET", raising=False)
    assert main(["--check-auth"]) == 2
    assert "ERROR" in capsys.readouterr().out


def test_check_auth_credenciales_invalidas_devuelve_1(monkeypatch, capsys):
    class Sesion:
        def post(self, url, **kw):
            return Respuesta(401, text="invalid_client")

    monkeypatch.setattr(dl.ClienteSH, "desde_entorno",
                        classmethod(lambda cls, environ=None: dl.ClienteSH("id", "malo", session=Sesion())))
    assert main(["--check-auth"]) == 1
    salida = capsys.readouterr().out
    assert "HTTP 401" in salida
    assert "SH_CLIENT_SECRET" in salida      # mensaje accionable


def test_cargar_dotenv_parser(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# comentario\n\nSH_CLIENT_ID=abc\n'
                 'export SH_CLIENT_SECRET="xyz"\nSIN_IGUAL\nOTRA=valor=con=iguales\n',
                 encoding="utf-8")
    assert _CARGAR_DOTENV_REAL(p) == {"SH_CLIENT_ID": "abc", "SH_CLIENT_SECRET": "xyz",
                                      "OTRA": "valor=con=iguales"}


def test_cargar_dotenv_inexistente(tmp_path):
    assert _CARGAR_DOTENV_REAL(tmp_path / "no.env") == {}


def test_dotenv_se_lee_si_no_hay_variables(tmp_path, monkeypatch):
    monkeypatch.delenv("SH_CLIENT_ID", raising=False)
    monkeypatch.delenv("SH_CLIENT_SECRET", raising=False)
    (tmp_path / ".env").write_text('SH_CLIENT_ID="id-dotenv"\nSH_CLIENT_SECRET=secret-dotenv\n',
                                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dl, "cargar_dotenv", _CARGAR_DOTENV_REAL)
    c = dl.ClienteSH.desde_entorno()
    assert c.client_id == "id-dotenv"
    assert c.client_secret == "secret-dotenv"


def test_variables_de_entorno_ganan_al_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SH_CLIENT_ID=id-dotenv\nSH_CLIENT_SECRET=secret-dotenv\n",
                                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dl, "cargar_dotenv", _CARGAR_DOTENV_REAL)
    monkeypatch.setenv("SH_CLIENT_ID", "id-entorno")
    monkeypatch.setenv("SH_CLIENT_SECRET", "secret-entorno")
    c = dl.ClienteSH.desde_entorno()
    assert c.client_id == "id-entorno"
    assert c.client_secret == "secret-entorno"


# ------------------------------------------- integración batch + sidecar

def test_batch_usa_bbox_del_sidecar(tmp_path, cliente):
    """Sin sidecar no hay escala; con escena.json sí (hectáreas en el informe)."""
    swir, tc, _ = make_scene()
    raiz = tmp_path / "imgs"
    (raiz / "escena").mkdir(parents=True)
    for nombre, arr in (("swir.png", swir), ("truecolor.png", tc)):
        Image.fromarray(arr).save(raiz / "escena" / nombre)
    (raiz / "escena" / "escena.json").write_text(
        json.dumps({"bbox": [-4.85, 40.50, -4.60, 40.70]}), encoding="utf-8")
    scenes, _ = discover_scenes(raiz)
    assert scenes[0].geo is not None
    results = run_batch(scenes, tmp_path / "out", debug=False, log=lambda *a, **k: None)
    a = results[0].informe["area_quemada"]
    assert "hectareas" in a and a["hectareas"] > 0


# ----------------------------------------------------------- evalscripts (tono)

def test_evalscript_truecolor_aplica_gamma_srgb():
    """El Process API devuelve reflectancia lineal; el navegador de Copernicus la
    codifica en sRGB (gamma ~2.2). Sin esa gamma la descarga sale ~2x más oscura
    que la imagen de referencia, así que el evalscript de True Color debe aplicarla."""
    assert "1/2.2" in dl.EVALSCRIPT_TRUECOLOR
    assert "Math.pow(rgb[0]" in dl.EVALSCRIPT_TRUECOLOR
    # Y no la aplicamos a SWIR ni a Wildfire: esos ya coinciden con el navegador.
    assert "Math.pow" not in dl.EVALSCRIPT_SWIR
    assert "Math.pow" not in dl.EVALSCRIPT_WILDFIRE


def test_descarga_pide_truecolor_con_gamma(tmp_path):
    session = FakeSession()
    c = dl.ClienteSH("id", "secret", session=session)
    dl.descargar_escena(dl.Escena("s", (-4.0, 40.0, -3.9, 40.1), "2026-07-21"), tmp_path, c)
    truecolor = [r for r in session.solicitudes if _cual(r["evalscript"]) == "truecolor"]
    assert len(truecolor) == 1
    assert "1/2.2" in truecolor[0]["evalscript"]
