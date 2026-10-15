"""Tests del conversor GeoJSON -> fila del manifiesto.

Ningún test toca la red: solo transforma geometrías y lee/escribe CSV en tmp_path.

Ejecutar:  python -m pytest -q
"""
import io
import json

import pytest

from firewatch import geojson_csv as gc
from firewatch.download import leer_manifiesto

# El polígono que copia el navegador de Copernicus al dibujar un rectángulo.
POLIGONO = {
    "type": "Polygon",
    "coordinates": [[
        [-4.872405, 40.152925],
        [-4.872405, 40.511843],
        [-4.114238, 40.511843],
        [-4.114238, 40.152925],
        [-4.872405, 40.152925],
    ]],
}
BBOX = (-4.872405, 40.152925, -4.114238, 40.511843)


# ------------------------------------------------------------------ bbox_de_geojson

def test_bbox_del_poligono_del_navegador():
    assert gc.bbox_de_geojson(POLIGONO) == BBOX


def test_bbox_no_depende_del_orden_del_anillo():
    """El anillo puede empezar por cualquier esquina (o ir al revés): manda min/max."""
    anillo = POLIGONO["coordinates"][0]
    for giro in range(len(anillo) - 1):
        rotado = anillo[giro:-1] + anillo[:giro] + [anillo[giro]]
        assert gc.bbox_de_geojson({"type": "Polygon", "coordinates": [rotado]}) == BBOX
    assert gc.bbox_de_geojson({"type": "Polygon", "coordinates": [anillo[::-1]]}) == BBOX


def test_acepta_feature_y_featurecollection():
    feature = {"type": "Feature", "properties": {"name": "Ávila 40.5"}, "geometry": POLIGONO}
    coleccion = {"type": "FeatureCollection", "features": [feature]}
    assert gc.bbox_de_geojson(feature) == BBOX
    assert gc.bbox_de_geojson(coleccion) == BBOX


def test_acepta_coordinates_pelado():
    """Si solo pegas el array de coordenadas (sin el objeto envoltorio), también vale."""
    assert gc.bbox_de_geojson(POLIGONO["coordinates"]) == BBOX


def test_acepta_multipoligono():
    trozo = [[-4.872405, 40.152925], [-4.114238, 40.511843], [-4.5, 40.3]]
    assert gc.bbox_de_geojson({"type": "MultiPolygon", "coordinates": [[trozo]]}) == (
        -4.872405, 40.152925, -4.114238, 40.511843)


def test_ignora_properties_con_numeros():
    """`properties` no son coordenadas aunque lleve números grandes."""
    feature = {"type": "Feature", "properties": {"lon": 999, "lat": -999}, "geometry": POLIGONO}
    assert gc.bbox_de_geojson(feature) == BBOX


@pytest.mark.parametrize("obj", [
    {"type": "Polygon", "coordinates": [[["a", "b"], ["c", "d"]]]},  # no numérico
    {"type": "Polygon", "coordinates": []},                          # sin coordenadas
    {"type": "Feature", "geometry": None},                            # sin geometría
    "no soy un geojson",                                              # texto
])
def test_errores_sin_coordenadas(obj):
    with pytest.raises(gc.ErrorZona):
        gc.bbox_de_geojson(obj)


def test_error_area_degenerada():
    """Un solo punto (o una línea) no tiene área."""
    with pytest.raises(gc.ErrorZona):
        gc.bbox_de_geojson({"type": "Point", "coordinates": [-4.5, 40.2]})
    linea = {"type": "LineString", "coordinates": [[-4.5, 40.2], [-4.5, 40.5]]}
    with pytest.raises(gc.ErrorZona):
        gc.bbox_de_geojson(linea)


def test_error_fuera_de_rango_y_pista_de_orden():
    with pytest.raises(gc.ErrorZona) as exc:
        gc.bbox_de_geojson({"type": "Polygon", "coordinates": [[[-4.5, 200], [-4.1, 200]]]})
    assert "latitud" in str(exc.value)

    # Si la latitud se pasa de 90 pero la "otra" coordenada es válida, sugiere que está al revés
    # (p. ej. un sitio con longitud 100 y latitud 40: al revés, [40, 100], la 2ª se sale de -90..90).
    with pytest.raises(gc.ErrorZona) as exc:
        gc.bbox_de_geojson({"type": "Polygon",
                            "coordinates": [[[40.1, 100.1], [40.5, 100.5], [40.5, 100.1]]]})
    assert "latitud" in str(exc.value)
    assert "latitud, longitud" in str(exc.value)


def test_error_demasiada_longitud():
    """Un área de más de 180° de longitud huele a meridiano 180 cruzado o CRS equivocado."""
    obj = {"type": "Polygon", "coordinates": [[[-170, 0], [170, 0], [170, 10], [-170, 10]]]}
    with pytest.raises(gc.ErrorZona) as exc:
        gc.bbox_de_geojson(obj)
    assert "longitud" in str(exc.value)


# ------------------------------------------------------------------ a_fila_csv

def test_fila_csv_exacta():
    fila = gc.a_fila_csv("avila-26jul", BBOX, "2026-07-26")
    assert fila == "avila-26jul,-4.872405,40.152925,-4.114238,40.511843,2026-07-26,2026-07-26"


def test_fila_csv_hasta_distinto():
    fila = gc.a_fila_csv("avila", BBOX, "2026-07-01", "2026-07-31")
    assert fila.endswith(",2026-07-01,2026-07-31")


def test_fila_csv_sin_fechas_deja_marcadores():
    fila = gc.a_fila_csv("avila", BBOX)
    assert fila == "avila,-4.872405,40.152925,-4.114238,40.511843,desde,hasta"


def test_fila_csv_decimales():
    fila = gc.a_fila_csv("x", BBOX, "2026-07-26", decimales=3)
    assert fila == "x,-4.872,40.153,-4.114,40.512,2026-07-26,2026-07-26"


def test_fila_csv_sin_decimales_no_redondea():
    """Sin --decimales se conserva la precisión original (no se desplaza el bbox)."""
    fila = gc.a_fila_csv("x", (40.123456789, -4.987654321, 40.2, -4.1), "2026-07-26")
    assert "40.123456789" in fila and "-4.987654321" in fila


# ------------------------------------------------------------------ CLI

def _correr(monkeypatch, argv, entrada=None):
    if entrada is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(entrada))
    return gc.main(argv)


def test_cli_por_stdin(monkeypatch, capsys):
    code = _correr(monkeypatch, ["-", "-n", "avila-26jul", "--desde", "2026-07-26"],
                   entrada=json.dumps(POLIGONO))
    assert code == 0
    salida = capsys.readouterr()
    assert salida.out.strip() == (
        "avila-26jul,-4.872405,40.152925,-4.114238,40.511843,2026-07-26,2026-07-26")


def test_cli_por_fichero_con_nombre_por_defecto(tmp_path, monkeypatch, capsys):
    p = tmp_path / "zona avila.geojson"
    p.write_text(json.dumps(POLIGONO), encoding="utf-8")
    assert _correr(monkeypatch, [str(p), "--desde", "2026-07-26"]) == 0
    fila = capsys.readouterr().out.strip()
    assert fila.startswith("zona_avila,")          # el nombre sale del fichero, saneado


def test_cli_sin_desde_avisa_y_deja_marcadores(monkeypatch, capsys):
    code = _correr(monkeypatch, ["-", "-n", "x"], entrada=json.dumps(POLIGONO))
    assert code == 0
    salida = capsys.readouterr()
    assert salida.out.strip().endswith(",desde,hasta")
    assert "rellena las fechas" in salida.err


def test_cli_json_invalido(monkeypatch, capsys):
    assert _correr(monkeypatch, ["-", "-n", "x"], entrada="esto no es json") == 1
    assert "no es JSON" in capsys.readouterr().out


def test_cli_fecha_invalida(monkeypatch, capsys):
    assert _correr(monkeypatch, ["-", "-n", "x", "--desde", "26/07/2026"],
                   entrada=json.dumps(POLIGONO)) == 1
    assert "fecha" in capsys.readouterr().out


def test_cli_nombre_con_coma(monkeypatch, capsys):
    assert _correr(monkeypatch, ["-", "-n", "a,b", "--desde", "2026-07-26"],
                   entrada=json.dumps(POLIGONO)) == 1
    assert "comas" in capsys.readouterr().out


def test_cli_fichero_inexistente(monkeypatch, capsys):
    assert _correr(monkeypatch, ["no_existe.geojson", "-n", "x"]) == 1
    assert "no se ha podido leer" in capsys.readouterr().out


def test_cli_hasta_anterior_a_desde_avisa(monkeypatch, capsys):
    """Un rango vacío no bloquea, pero se avisa: la descarga no encontraría pasadas."""
    code = _correr(monkeypatch, ["-", "-n", "x", "--desde", "2026-07-26", "--hasta", "2026-07-01"],
                   entrada=json.dumps(POLIGONO))
    assert code == 0
    assert "aviso" in capsys.readouterr().err


# ------------------------------------------------------------------ --append

def test_append_crea_manifiesto_con_cabecera(tmp_path, monkeypatch, capsys):
    destino = tmp_path / "mi.csv"
    code = _correr(monkeypatch, ["-", "-n", "avila-26jul", "--desde", "2026-07-26",
                                 "-o", str(destino)], entrada=json.dumps(POLIGONO))
    assert code == 0
    lineas = destino.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == gc.CABECERA
    assert lineas[1] == "avila-26jul,-4.872405,40.152925,-4.114238,40.511843,2026-07-26,2026-07-26"


def test_append_lo_lee_leer_manifiesto(tmp_path, monkeypatch):
    """Integración real: el CSV generado lo entiende el descargador."""
    destino = tmp_path / "mi.csv"
    _correr(monkeypatch, ["-", "-n", "avila-26jul", "--desde", "2026-07-26", "-o",
                          str(destino)], entrada=json.dumps(POLIGONO))
    escenas = leer_manifiesto(destino)
    assert len(escenas) == 1
    assert escenas[0].nombre == "avila-26jul"
    assert escenas[0].bbox == BBOX
    assert escenas[0].desde == "2026-07-26" and escenas[0].hasta == "2026-07-26"


def test_append_no_duplica_salvo_force(tmp_path, monkeypatch, capsys):
    destino = tmp_path / "mi.csv"
    base = ["-", "-n", "avila", "--desde", "2026-07-26", "-o", str(destino)]
    assert _correr(monkeypatch, base, entrada=json.dumps(POLIGONO)) == 0
    # Segunda vez: mismo nombre -> error, no duplica.
    assert _correr(monkeypatch, base, entrada=json.dumps(POLIGONO)) == 1
    assert "ya está" in capsys.readouterr().out
    assert len(leer_manifiesto(destino)) == 1
    # Con --force sí se añade.
    assert _correr(monkeypatch, base + ["--force"], entrada=json.dumps(POLIGONO)) == 0
    assert len(leer_manifiesto(destino)) == 2


def test_append_sin_desde_falla(tmp_path, monkeypatch, capsys):
    destino = tmp_path / "mi.csv"
    code = _correr(monkeypatch, ["-", "-n", "x", "-o", str(destino)], entrada=json.dumps(POLIGONO))
    assert code == 1
    assert "hace falta --desde" in capsys.readouterr().out
    assert not destino.exists()
