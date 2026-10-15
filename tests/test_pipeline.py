"""Tests de comportamiento sobre escenas sintéticas con verdad conocida.

Ejecutar:  python -m pytest -q
"""
import io

import numpy as np
import pytest
from PIL import Image

from firewatch.composition import guess_composition
from firewatch.geometry import GeoRef
from firewatch.pipeline import analyze

from synthetic import make_scene


def _save(tmp_path, name, arr, fmt="PNG", **kw):
    p = tmp_path / name
    Image.fromarray(arr).save(p, format=fmt, **kw)
    return str(p)


def _run(tmp_path, swir, tc=None, **kw):
    sp = _save(tmp_path, "swir.png", swir)
    tp = _save(tmp_path, "tc.png", tc) if tc is not None else None
    return analyze(sp, tp, **kw)


def iou(a, b):
    u = (a | b).sum()
    return 1.0 if u == 0 else (a & b).sum() / u


@pytest.fixture
def scene():
    return make_scene()


# ---------------------------------------------------------------- básico

def test_detects_burned_area(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    assert an.report["incendio_detectado"]
    assert iou(an.burn.burned, truth["burned"]) > 0.85


def test_works_without_truecolor(tmp_path, scene):
    swir, _, truth = scene
    an = _run(tmp_path, swir)
    assert iou(an.burn.burned, truth["burned"]) > 0.85


def test_lake_outside_is_never_burned(tmp_path, scene):
    """El requisito clave: agua negra en SWIR != área quemada."""
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    outside_water = truth["water"] & ~an.burn.footprint
    assert outside_water.any()
    assert not (an.burn.burned & truth["water"]).any(), "Se ha marcado agua como quemada"
    assert an.water[truth["water"]].mean() > 0.95, "El agua no se ha identificado"


def test_lake_inside_fire_is_in_perimeter_but_not_in_area(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    lake_in = truth["water"] & (np.arange(swir.shape[1])[None, :] < 300)
    lake_in &= (np.arange(swir.shape[0])[:, None] > 150)
    assert an.burn.footprint[lake_in].mean() > 0.95, "El lago interior debe quedar dentro del perímetro"
    assert an.burn.burned[lake_in].mean() < 0.05, "El lago interior no debe sumar superficie quemada"


def test_unburned_island_is_kept(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    assert an.report["islas_no_quemadas"] >= 1


def test_no_fire_scene_abstains(tmp_path):
    swir, tc, _ = make_scene(burned=False, edge_fire=False)
    an = _run(tmp_path, swir, tc)
    assert not an.report["incendio_detectado"]
    assert an.burn.burned.sum() == 0
    assert len(an.report["focos_activos"]) == 0


def test_border_bars_are_ignored(tmp_path):
    swir, tc, truth = make_scene(border_rows=6)
    an = _run(tmp_path, swir, tc)
    assert not an.valid[:6].any() and not an.valid[-6:].any()
    assert not an.water[:6].any()
    assert iou(an.burn.burned, truth["burned"]) > 0.85


# -------------------------------------------------------------- avanzado

def test_edge_fire_detected_with_high_confidence(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    hi = [c for c in an.fire.clusters if c.confidence == "alta"]
    assert hi, "No se ha detectado el frente activo"
    recall = an.fire.accepted[truth["fire"]].mean()
    assert recall > 0.7
    assert all(c.location == "frente" for c in hi)


def test_isolated_hot_spot_is_rejected(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    assert not an.fire.accepted[truth["isolated_hot"]].any(), "Un tejado aislado se ha tomado como fuego"
    assert any(c.location == "aislado" and c.confidence == "baja" for c in an.fire.clusters)


def test_cloud_is_not_fire(tmp_path):
    swir, tc, truth = make_scene(cloud=True)
    an = _run(tmp_path, swir, tc)
    assert not an.fire.accepted[truth["cloud"]].any()
    assert not an.burn.burned[truth["cloud"]].any()


def test_burned_area_does_not_count_fire_as_water(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc)
    assert not (an.water & truth["fire"]).any()


# ------------------------------------------------------------ robustez

@pytest.mark.parametrize("gain", [0.8, 1.15])
def test_brightness_invariance(tmp_path, scene, gain):
    swir, tc, truth = scene
    s2 = np.clip(swir.astype(float) * gain, 0, 255).astype(np.uint8)
    t2 = np.clip(tc.astype(float) * gain, 0, 255).astype(np.uint8)
    an = _run(tmp_path, s2, t2)
    assert iou(an.burn.burned, truth["burned"]) > 0.8


def test_jpeg_compression(tmp_path, scene):
    swir, tc, truth = scene
    sp = _save(tmp_path, "swir.jpg", swir, fmt="JPEG", quality=70)
    tp = _save(tmp_path, "tc.jpg", tc, fmt="JPEG", quality=70)
    an = analyze(sp, tp)
    assert iou(an.burn.burned, truth["burned"]) > 0.8
    assert not (an.burn.burned & truth["water"]).any()


def test_downscaled_input(tmp_path, scene):
    swir, tc, truth = scene
    h, w = swir.shape[:2]
    s2 = np.asarray(Image.fromarray(swir).resize((w // 2, h // 2), Image.BILINEAR))
    t2 = np.asarray(Image.fromarray(tc).resize((w // 2, h // 2), Image.BILINEAR))
    an = _run(tmp_path, s2, t2)
    gt = np.asarray(Image.fromarray(truth["burned"].astype(np.uint8) * 255).resize((w // 2, h // 2))) > 127
    assert iou(an.burn.burned, gt) > 0.8


def test_truecolor_of_different_size_is_aligned(tmp_path, scene):
    swir, tc, truth = scene
    t2 = np.asarray(Image.fromarray(tc).resize((tc.shape[1] + 3, tc.shape[0] + 2)))
    an = _run(tmp_path, swir, t2)
    assert iou(an.burn.burned, truth["burned"]) > 0.85


# ------------------------------------------------------------ utilidades

def test_hectares_with_pixel_size(tmp_path, scene):
    swir, tc, truth = scene
    an = _run(tmp_path, swir, tc, geo=GeoRef(pixel_size_m=20))
    ha = an.report["area_quemada"]["hectareas"]
    expected = truth["burned"].sum() * 400 / 1e4
    assert abs(ha - expected) / expected < 0.15
    lo, hi = an.report["area_quemada"]["hectareas_rango"]
    assert lo <= hi


def test_geojson_and_description(tmp_path, scene):
    swir, tc, _ = scene
    an = _run(tmp_path, swir, tc, geo=GeoRef(bbox=(-4.5, 40.2, -4.0, 40.5)))
    kinds = {f["properties"]["kind"] for f in an.geojson["features"]}
    assert kinds == {"area_quemada", "foco_activo", "frente_activo"}
    fronts = [feature for feature in an.geojson["features"] if feature["properties"]["kind"] == "frente_activo"]
    assert fronts
    assert all(feature["geometry"]["type"] == "LineString" for feature in fronts)
    assert all(feature["properties"]["longitud_px"] > 0 for feature in fronts)
    lon, lat = an.geojson["features"][0]["geometry"]["coordinates"][0][0]
    assert -4.5 <= lon <= -4.0 and 40.2 <= lat <= 40.5
    assert "Área quemada" in an.report["descripcion"]


def test_composition_guess(scene):
    swir, tc, _ = scene
    assert guess_composition(swir.astype(np.float32))[0] == "swir"
    real_tc = np.zeros_like(tc) + np.array([175, 150, 115], np.uint8)  # suelo ocre típico del True Color
    assert guess_composition(real_tc.astype(np.float32))[0] == "truecolor"


# ---------------------------------------------------------- bajo el humo

def test_burned_under_smoke_is_recovered_separately(tmp_path):
    swir, tc, truth = make_scene(smoke=True, isolated_hot=False)
    an = _run(tmp_path, swir, tc)
    us = an.burn.under_smoke
    gt = truth["burned_under_smoke"]
    assert us is not None and us.any(), "No se ha estimado nada bajo el humo"
    assert (us & gt).sum() / gt.sum() > 0.5, "Recupera poco del quemado oculto"
    # No debe invadir la vegetación bajo el humo
    assert (us & truth["veg_under_smoke"]).sum() / max(us.sum(), 1) < 0.15
    # Es una categoría aparte: no se mezcla con el confirmado
    assert not (us & an.burn.burned).any()


def test_under_smoke_is_included_only_in_upper_range(tmp_path):
    swir, tc, _ = make_scene(smoke=True, isolated_hot=False)
    an = _run(tmp_path, swir, tc, geo=GeoRef(pixel_size_m=20))
    a = an.report["area_quemada"]
    assert a["hectareas_bajo_humo_probable"] > 0
    assert a["hectareas_rango"][1] == pytest.approx(a["hectareas"] + a["hectareas_bajo_humo_probable"], abs=0.2)
    kinds = {f["properties"]["kind"] for f in an.geojson["features"]}
    assert "area_quemada_probable_bajo_humo" in kinds


def test_smoke_over_vegetation_only_recovers_nothing(tmp_path):
    """Humo sin quemado debajo: no debe inventar área quemada."""
    swir, tc, truth = make_scene(smoke=True, isolated_hot=False)
    # Mueve el humo lejos del quemado, sobre vegetación pura
    h, w = swir.shape[:2]
    swir2, tc2, _ = make_scene(smoke=False, isolated_hot=False)
    yy, xx = np.mgrid[:h, :w]
    sm = (yy - 330) ** 2 + (xx - 90) ** 2 <= 50 ** 2
    swir2[sm] = (85, 172, 142)
    tc2[sm] = (190, 195, 200)
    an = _run(tmp_path, swir2, tc2)
    assert not an.burn.under_smoke[sm].any()


def test_smoke_recovery_can_be_disabled(tmp_path):
    from firewatch.config import Params
    swir, tc, _ = make_scene(smoke=True, isolated_hot=False)
    p = Params(); p.burn.smoke_recovery = False
    an = _run(tmp_path, swir, tc, params=p)
    assert not an.burn.under_smoke.any()
