"""Tests del modo lote: descubrimiento de escenas, ejecución y resumen HTML.

Ejecutar:  python -m pytest -q
"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from firewatch.batch import discover_scenes, run_batch, write_html_index
from firewatch.cli import main

from synthetic import make_scene


def _img(path: Path, arr):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)
    return path


def _mini(path):
    """Imagen diminuta: suficiente para comprobar el pareado."""
    return _img(path, np.zeros((4, 4, 3), "uint8"))


# ------------------------------------------------------- descubrimiento

def test_discover_pairs_by_filename(tmp_path):
    a = tmp_path / "avila"
    _mini(a / "swir.png")
    _mini(a / "truecolor.jpg")
    _mini(a / "wildfire.png")
    scenes, avisos = discover_scenes(tmp_path)
    assert len(scenes) == 1 and not avisos
    sc = scenes[0]
    assert sc.nombre == "avila"
    assert sc.swir.name == "swir.png"
    assert sc.truecolor.name == "truecolor.jpg"
    assert sc.wildfire.name == "wildfire.png"


def test_discover_tc_short_name_and_long_wildfire_name(tmp_path):
    b = tmp_path / "mierla"
    _mini(b / "swir.png")
    _mini(b / "tc.jpg")
    _mini(b / "2026-07-21-00_00_2026-07-21-23_59_Sentinel-2_L2A_Wildfires.jpg")
    scenes, avisos = discover_scenes(tmp_path)
    assert len(scenes) == 1 and not avisos
    assert scenes[0].truecolor.name == "tc.jpg"
    assert scenes[0].wildfire.name.endswith("_Wildfires.jpg")


def test_discover_folder_without_swir_is_skipped_with_warning(tmp_path):
    _mini(tmp_path / "niebla" / "swir.png")
    _mini(tmp_path / "solo_tc" / "truecolor.png")
    scenes, avisos = discover_scenes(tmp_path)
    assert [s.nombre for s in scenes] == ["niebla"]
    assert len(avisos) == 1 and "solo_tc" in avisos[0] and "SWIR" in avisos[0]


def test_discover_unrelated_files_are_ignored(tmp_path):
    _mini(tmp_path / "x" / "swir.png")
    _img(tmp_path / "x" / "mapa.png", np.zeros((4, 4, 3), "uint8"))
    scenes, avisos = discover_scenes(tmp_path)
    assert len(scenes) == 1 and not avisos


def test_discover_several_swir_warns_and_picks_first(tmp_path):
    d = tmp_path / "d"
    _mini(d / "b_swir.png")
    _mini(d / "a_swir.png")
    scenes, avisos = discover_scenes(tmp_path)
    assert len(scenes) == 1
    assert scenes[0].swir.name == "a_swir.png"      # determinista: orden alfabético
    assert any("varias imágenes SWIR" in w for w in avisos)


def test_discover_nested_folders_get_unique_names(tmp_path):
    _mini(tmp_path / "a" / "escena" / "swir.png")
    _mini(tmp_path / "b" / "escena" / "swir.png")
    scenes, _ = discover_scenes(tmp_path)
    assert sorted(s.nombre for s in scenes) == ["a-escena", "b-escena"]


def test_discover_missing_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        discover_scenes(tmp_path / "no_existe")


# ------------------------------------------------------------ ejecución

@pytest.fixture
def lote(tmp_path):
    """Dos escenas sintéticas en carpetas distintas, listas para el lote."""
    root = tmp_path / "images"
    swir, tc, _ = make_scene()
    _img(root / "escena1" / "swir.png", swir)
    _img(root / "escena1" / "truecolor.png", tc)
    swir2, tc2, _ = make_scene(seed=1, burned=False, edge_fire=False, isolated_hot=False)
    _img(root / "escena2" / "swir.png", swir2)
    _img(root / "escena2" / "truecolor.png", tc2)
    return root


def test_run_batch_outputs_and_html_index(lote, tmp_path):
    scenes, avisos = discover_scenes(lote)
    assert len(scenes) == 2
    out = tmp_path / "out"
    results = run_batch(scenes, out, debug=False, log=lambda *a, **k: None)
    assert [r.estado for r in results] == ["ok", "ok"]
    assert all((out / s.nombre / "resultado.png").exists() for s in scenes)
    assert all((out / s.nombre / "informe.json").exists() for s in scenes)

    index = write_html_index(out, results, avisos)
    doc = index.read_text(encoding="utf-8")
    assert "<table" in doc and 'lang="es"' in doc
    assert "escena1" in doc and "escena2" in doc
    assert '<img src="escena1/resultado.png"' in doc
    # Texto alternativo = descripción en lenguaje natural de la escena
    desc = (out / "escena1" / "descripcion.txt").read_text(encoding="utf-8").strip()
    assert f'alt="{desc[:300]}"' in doc


def test_run_batch_continues_after_error(lote, tmp_path):
    rota = lote / "aaa_rota" / "swir.png"
    rota.parent.mkdir(parents=True)
    rota.write_bytes(b"esto no es una imagen")
    scenes, _ = discover_scenes(lote)
    assert scenes[0].nombre == "aaa_rota"
    out = tmp_path / "out"
    results = run_batch(scenes, out, debug=False, log=lambda *a, **k: None)
    assert results[0].estado == "error" and results[0].error
    assert [r.estado for r in results[1:]] == ["ok", "ok"]
    index = write_html_index(out, results)
    assert "ERROR" in index.read_text(encoding="utf-8")


# ------------------------------------------------------------------ CLI

def test_cli_batch_ok(lote, tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["--batch", str(lote), "--out", str(out), "--no-debug"])
    assert code == 0
    assert (out / "resumen.html").exists()
    assert (out / "escena1" / "resultado.png").exists()
    assert "2/2 escenas analizadas" in capsys.readouterr().out


def test_cli_batch_no_scenes_returns_2(tmp_path, capsys):
    _mini(tmp_path / "cosas" / "truecolor.png")
    assert main(["--batch", str(tmp_path), "--out", str(tmp_path / "out")]) == 2
    assert "ninguna escena" in capsys.readouterr().out


def test_cli_batch_missing_folder_returns_2(tmp_path, capsys):
    assert main(["--batch", str(tmp_path / "nope"), "--out", str(tmp_path / "out")]) == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_requires_exactly_one_mode(tmp_path):
    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path / "out")])              # ni --swir ni --batch
    with pytest.raises(SystemExit):
        main(["--swir", "a.png", "--batch", "images", "--out", str(tmp_path / "out")])
