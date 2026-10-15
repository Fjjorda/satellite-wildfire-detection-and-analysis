"""Representación de resultados sobre la imagen original.

Accesibilidad desde el principio (WCAG 1.4.1 y 1.4.11):
- Paleta Okabe-Ito, distinguible con daltonismo.
- Nunca se distingue solo por color: el área quemada lleva trama diagonal,
  el humo un contorno discontinuo y los focos son triángulos (forma propia).
- Contornos con halo blanco para mantener contraste sobre cualquier fondo.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Okabe-Ito (RGB)
VERMILION = (213, 94, 0)
YELLOW = (240, 228, 66)
ORANGE = (230, 159, 0)
BLUE = (0, 114, 178)
SKY = (86, 180, 233)
PURPLE = (204, 121, 167)   # Okabe-Ito 'reddish purple'
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


def _font(size: int):
    try:
        import matplotlib
        path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _contours(mask):
    res = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return res[0] if len(res) == 2 else res[1]


def _hatch(shape, spacing, thickness):
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    return ((xx + yy) % spacing) < thickness


def _dashed(img, contours, color, thickness, dash, gap):
    for c in contours:
        pts = c[:, 0, :]
        if len(pts) < 2:
            continue
        seg = np.vstack([pts, pts[:1]])
        d = np.hypot(*np.diff(seg.astype(float), axis=0).T)
        acc, on = 0.0, True
        for i in range(len(seg) - 1):
            if on:
                cv2.line(img, tuple(int(v) for v in seg[i]), tuple(int(v) for v in seg[i + 1]),
                         color, thickness, cv2.LINE_AA)
            acc += d[i]
            if acc >= (dash if on else gap):
                acc, on = 0.0, not on


def _triangle(img, cx, cy, size, fill, border, bw):
    pts = np.array([[cx, cy - size], [cx - size * 0.9, cy + size * 0.6], [cx + size * 0.9, cy + size * 0.6]],
                   dtype=np.int32)
    cv2.fillPoly(img, [pts], fill, cv2.LINE_AA)
    cv2.polylines(img, [pts], True, border, bw, cv2.LINE_AA)


def render_overlay(base: np.ndarray, burn, fire, smoke: np.ndarray | None, summary_lines: list[str],
                   show_legend: bool = True, smoke_low: np.ndarray | None = None, front=None) -> np.ndarray:
    """Devuelve la imagen base con el perímetro, el área quemada, el humo y los focos."""
    img = np.clip(base, 0, 255).astype(np.uint8).copy()
    h, w = img.shape[:2]
    diag = float(np.hypot(h, w))
    t = max(2, int(round(diag / 700)))

    # 1) Relleno semitransparente + trama del área quemada
    area = burn.burned
    if area.any():
        fill = img.astype(np.float32)
        fill[area] = 0.70 * fill[area] + 0.30 * np.array(VERMILION, np.float32)
        hatch = _hatch((h, w), spacing=max(6, 4 * t), thickness=max(1, t // 2 + 1)) & area
        fill[hatch] = np.array(VERMILION, np.float32)
        img = fill.astype(np.uint8)

    # 1b) Quemado PROBABLE bajo el humo: relleno púrpura con trama cruzada y contorno punteado
    us = burn.under_smoke
    if us is not None and us.any():
        f = img.astype(np.float32)
        f[us] = 0.75 * f[us] + 0.25 * np.array(PURPLE, np.float32)
        yy, xx = np.mgrid[:h, :w]
        cross = ((xx - yy) % max(6, 4 * t)) < max(1, t // 2 + 1)
        f[cross & us] = np.array(PURPLE, np.float32)
        img = f.astype(np.uint8)
        cu = _contours(us)
        cv2.drawContours(img, cu, -1, WHITE, t + 2, cv2.LINE_AA)
        _dashed(img, cu, PURPLE, t, dash=2 * t, gap=2 * t)

    # 2) Agua dentro del perímetro (excluida del área)
    if burn.water_inside.any():
        f = img.astype(np.float32)
        f[burn.water_inside] = 0.4 * f[burn.water_inside] + 0.6 * np.array(BLUE, np.float32)
        img = f.astype(np.uint8)

    # 3) Humo: contorno discontinuo azul cielo (zona donde el visible está ocluido)
    if smoke is not None and smoke.any():
        cs = _contours(smoke)
        _dashed(img, cs, WHITE, t + 2, dash=6 * t, gap=4 * t)
        _dashed(img, cs, SKY, t, dash=6 * t, gap=4 * t)

    # 3b) Humo de BAJA confianza (pequeño/suelto: posible error): línea fina punteada gris, sin relleno
    if smoke_low is not None and smoke_low.any():
        cl = _contours(smoke_low)
        _dashed(img, cl, WHITE, t + 3, dash=t + 2, gap=2 * t + 2)
        _dashed(img, cl, (40, 40, 40), t, dash=t + 2, gap=2 * t + 2)

    # 4) Perímetro: halo blanco + línea bermellón
    cfp = _contours(burn.footprint)
    cv2.drawContours(img, cfp, -1, WHITE, t + 3, cv2.LINE_AA)
    cv2.drawContours(img, cfp, -1, VERMILION, t, cv2.LINE_AA)

    # 4c) Dirección del humo: flecha desde el origen del penacho
    if front is not None and front.smoke_vector is not None and front.smoke_origin_xy is not None:
        ox, oy = front.smoke_origin_xy
        L = 0.10 * diag
        tip = (int(ox + front.smoke_vector[0] * L), int(oy + front.smoke_vector[1] * L))
        cv2.arrowedLine(img, (int(ox), int(oy)), tip, BLACK, t + 5, cv2.LINE_AA, tipLength=0.25)
        cv2.arrowedLine(img, (int(ox), int(oy)), tip, SKY, t + 1, cv2.LINE_AA, tipLength=0.25)

    # 5) Focos activos: píxeles calientes (amarillo con halo negro) + triángulo por foco.
    #    Confianza alta = triángulo relleno; media = triángulo hueco (forma, no solo color).
    acc = fire.accepted
    if acc.any():
        halo = cv2.dilate(acc.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
        img[halo] = BLACK
        img[acc] = ORANGE

    # Frente activo encima de los píxeles de fuego: halo blanco + cinta negro/amarillo
    if front is not None:
        for seg in front.segments:
            pts = seg.points.reshape(-1, 1, 2).astype(np.int32)
            cv2.polylines(img, [pts], False, WHITE, t + 5, cv2.LINE_AA)
            cv2.polylines(img, [pts], False, BLACK, t + 3, cv2.LINE_AA)
            _dashed_open(img, seg.points, YELLOW, t, dash=2 * t + 2, gap=2 * t + 2)
    size_by = {"baja": 2 * t + 3, "media": 3 * t + 3, "alta": 4 * t + 3}
    for c in fire.clusters:
        if c.confidence == "baja":
            continue
        cx, cy = c.centroid_xy
        sz = size_by[c.intensity]
        if c.confidence == "alta":
            _triangle(img, int(cx), int(cy) - sz, sz, YELLOW, BLACK, max(1, t - 1))
        else:
            _triangle_hollow(img, int(cx), int(cy) - sz, sz, t)

    if show_legend:
        present = set()
        if burn.burned.any(): present.add("area")
        if burn.footprint.any(): present.add("perim")
        if us is not None and us.any(): present.add("under")
        if acc.any(): present.add("fire_px")
        if any(c.confidence == "alta" for c in fire.clusters): present.add("fire_hi")
        if any(c.confidence == "media" for c in fire.clusters): present.add("fire_md")
        if front is not None and front.segments: present.add("front")
        if front is not None and front.smoke_vector is not None: present.add("wind")
        if burn.water_inside.any(): present.add("water")
        if smoke is not None and smoke.any(): present.add("smoke")
        if smoke_low is not None and smoke_low.any(): present.add("smoke_low")
        img = _legend_below(img, summary_lines, t, present)
    return img


def _dashed_open(img, pts, color, thickness, dash, gap):
    """Línea discontinua sobre una polilínea abierta."""
    if len(pts) < 2:
        return
    d = np.hypot(*np.diff(pts.astype(float), axis=0).T)
    acc, on = 0.0, True
    for i in range(len(pts) - 1):
        if on:
            cv2.line(img, tuple(int(v) for v in pts[i]), tuple(int(v) for v in pts[i + 1]), color, thickness, cv2.LINE_AA)
        acc += d[i]
        if acc >= (dash if on else gap):
            acc, on = 0.0, not on


def _triangle_hollow(img, cx, cy, size, t):
    pts = np.array([[cx, cy - size], [cx - size * 0.9, cy + size * 0.6], [cx + size * 0.9, cy + size * 0.6]],
                   dtype=np.int32)
    cv2.polylines(img, [pts], True, BLACK, t + 2, cv2.LINE_AA)
    cv2.polylines(img, [pts], True, YELLOW, max(1, t), cv2.LINE_AA)


def _legend_below(img: np.ndarray, lines: list[str], t: int, present: set | None = None) -> np.ndarray:
    """Leyenda en una franja bajo la imagen, para no tapar datos."""
    h, w = img.shape[:2]
    fs = max(13, int(w / 70))
    font = _font(fs)
    items = [("area", "Área quemada"), ("perim", "Perímetro"), ("under", "Quemado probable bajo humo"), ("fire_px", "Píxeles en llamas"), ("fire_hi", "Foco activo (confianza alta)"),
             ("fire_md", "Foco activo (confianza media)"), ("water", "Agua (excluida)"),
             ("smoke", "Humo (visible ocluido)"), ("smoke_low", "Humo de baja confianza (posible error)"),
             ("front", "Frente activo"), ("wind", "Dirección del humo")]
    if present is not None:
        items = [it for it in items if it[0] in present] or [("perim", "Sin incendio detectado")]
    pad, row = fs // 2 + 2, int(fs * 1.6)
    sw = int(fs * 1.6)
    col_w = int(max(font.getlength(lbl) for _, lbl in items) + sw + 3 * pad)
    ncols = max(1, (w - 2 * pad) // col_w)
    nrows = -(-len(items) // ncols)
    extra = [ln for ln in lines if ln]
    band_h = pad * 2 + row * (nrows + (1 if extra else 0))
    canvas = Image.new("RGB", (w, h + band_h), WHITE)
    canvas.paste(Image.fromarray(img), (0, 0))
    d = ImageDraw.Draw(canvas)
    d.line([0, h, w, h], fill=BLACK, width=1)
    for k, (kind, label) in enumerate(items):
        sx = pad + (k % ncols) * col_w
        y = h + pad + (k // ncols) * row
        sy = y + 2
        mid = sy + fs // 2
        if kind == "area":
            d.rectangle([sx, sy, sx + sw, sy + fs], fill=(234, 175, 128))
            for q in range(-fs, sw, 5):
                x_a, y_a = sx + max(q, 0), sy + fs - max(0, -q)
                x_b, y_b = sx + min(q + fs, sw), sy + fs - (min(q + fs, sw) - q)
                d.line([x_a, y_a, x_b, y_b], fill=VERMILION, width=1)
            d.rectangle([sx, sy, sx + sw, sy + fs], outline=VERMILION, width=2)
        elif kind == "under":
            d.rectangle([sx, sy, sx + sw, sy + fs], fill=(226, 190, 208))
            for q in range(-fs, sw, 5):
                d.line([sx + max(q, 0), sy + max(0, -q), sx + min(q + fs, sw), sy + min(fs, sw - q)], fill=PURPLE, width=1)
            for q in range(0, sw, 6):
                d.line([sx, sy + fs, sx + sw, sy + fs], fill=PURPLE, width=1)
            d.rectangle([sx, sy, sx + sw, sy + fs], outline=PURPLE, width=2)
        elif kind == "perim":
            d.line([sx, mid, sx + sw, mid], fill=(90, 90, 90), width=t + 4)
            d.line([sx, mid, sx + sw, mid], fill=VERMILION, width=t + 1)
        elif kind in ("fire_hi", "fire_md"):
            c, s_ = sx + sw // 2, fs // 2 + 2
            tri = [(c, mid - s_), (c - s_, mid + s_ * 0.7), (c + s_, mid + s_ * 0.7)]
            if kind == "fire_hi":
                d.polygon(tri, fill=YELLOW, outline=BLACK, width=2)
            else:
                d.polygon(tri, outline=BLACK, width=4)
                d.polygon(tri, outline=YELLOW, width=2)
        elif kind == "water":
            d.rectangle([sx, sy, sx + sw, sy + fs], fill=BLUE, outline=BLACK)
        elif kind == "smoke":
            for q in range(0, sw, 8):
                d.line([sx + q, mid, sx + min(q + 5, sw), mid], fill=(90, 90, 90), width=t + 4)
                d.line([sx + q, mid, sx + min(q + 5, sw), mid], fill=SKY, width=t + 1)
        elif kind == "fire_px":
            d.rectangle([sx + sw // 2 - 6, sy + 1, sx + sw // 2 + 6, sy + fs - 1], fill=ORANGE, outline=BLACK, width=2)
        elif kind == "front":
            d.line([sx, mid, sx + sw, mid], fill=(200, 200, 200), width=t + 5)
            d.line([sx, mid, sx + sw, mid], fill=BLACK, width=t + 3)
            for q in range(0, sw, 2 * (2 * t + 2)):
                d.line([sx + q, mid, sx + min(q + 2 * t + 2, sw), mid], fill=YELLOW, width=t)
        elif kind == "wind":
            d.line([sx, mid, sx + sw - 4, mid], fill=BLACK, width=t + 4)
            d.polygon([(sx + sw, mid), (sx + sw - 9, mid - 6), (sx + sw - 9, mid + 6)], fill=SKY, outline=BLACK)
            d.line([sx, mid, sx + sw - 8, mid], fill=SKY, width=t)
        elif kind == "smoke_low":
            for q in range(0, sw, 5):
                d.line([sx + q, mid, sx + min(q + 2, sw), mid], fill=(40, 40, 40), width=t + 1)
        d.text((sx + sw + pad, y), label, fill=BLACK, font=font)
    if extra:
        d.text((pad, h + pad + nrows * row), "   ·   ".join(extra), fill=BLACK, font=font)
    return np.asarray(canvas)


def render_debug(path: Path, swir, score, water, smoke, cloud, burn, fire, overlay, smoke_low=None, front=None,
                 validation=None):
    """Panel de diagnóstico para revisar cada paso (útil para ajustar umbrales)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(2, 3, figsize=(18, 10))
    ax = ax.ravel()
    ax[0].imshow(np.clip(swir, 0, 255).astype(np.uint8)); ax[0].set_title("Imagen de entrada")
    im = ax[1].imshow(score, cmap="viridis", vmin=0, vmax=1); ax[1].set_title("Puntuación de quemado")
    fig.colorbar(im, ax=ax[1], fraction=0.035)
    veto = np.zeros(swir.shape[:2] + (3,), np.uint8)
    veto[water] = BLUE
    if smoke is not None:
        veto[smoke & ~water] = SKY
    if smoke_low is not None:
        veto[smoke_low & ~water] = (150, 150, 150)
    veto[cloud] = (200, 200, 200)
    ax[2].imshow(veto); ax[2].set_title("Vetos: agua (azul), humo (celeste), humo dudoso/nube (gris)")
    st = np.zeros(swir.shape[:2] + (3,), np.uint8)
    st[burn.footprint] = (120, 120, 120)
    st[burn.burned] = VERMILION
    st[burn.burned_strict] = (130, 50, 0)
    if burn.under_smoke is not None:
        st[burn.under_smoke] = PURPLE
    st[burn.islands] = (0, 158, 115)
    ax[3].imshow(st); ax[3].set_title("Quemado (bermellón), estricto (oscuro), islas (verde), bajo humo (púrpura)")
    fc = np.clip(swir, 0, 255).astype(np.uint8) // 3
    fc[fire.edge_ring] = (80, 80, 80)
    fc[fire.candidates] = (150, 150, 150)
    if front is not None and front.active_zone is not None:
        fc[front.active_zone] = (fc[front.active_zone] * 0.4 + np.array(ORANGE) * 0.6).astype(np.uint8)
    if validation is not None:
        fc[validation.truth_rejected] = (0, 200, 255)
    fc[fire.accepted] = YELLOW
    if front is not None:
        for seg in front.segments:
            cv2.polylines(fc, [seg.points.reshape(-1, 1, 2).astype(np.int32)], False, (255, 255, 255), 2)
    ax[4].imshow(fc); ax[4].set_title("Fuego: focos (amarillo), zona activa (naranja), frente (blanco),\nborde (gris), naranja de referencia descartado (cian)")
    ax[5].imshow(overlay); ax[5].set_title("Resultado")
    for a in ax:
        a.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def render_validation(swir: np.ndarray, fire, val) -> np.ndarray:
    """Imagen de validación: SWIR con la llama real de la referencia y lo detectado por el modelo.

    - Anillo BLANCO con trama: llama real (referencia) ENCONTRADA por el modelo.
    - Cuadro BERMELLÓN: llama real NO encontrada (fallo).
    - Rombo AZUL: foco aceptado por el modelo sin llama en la referencia (falso positivo).
    - Triángulo AMARILLO: foco aceptado que coincide con la referencia (acierto).
    Distinguibles por forma, no solo por color.
    """
    img = np.clip(swir, 0, 255).astype(np.uint8).copy()
    h, w = img.shape[:2]
    t = max(2, int(round(min(h, w) / 450)))
    pad = max(8, int(round(min(h, w) / 90)))
    # píxeles de referencia en cian tenue para ver la forma real
    img[val.truth] = (0.45 * img[val.truth] + 0.55 * np.array((0, 255, 255))).astype(np.uint8)

    def box(cx, cy, r, color):
        cv2.rectangle(img, (int(cx - r), int(cy - r)), (int(cx + r), int(cy + r)), BLACK, t + 2)
        cv2.rectangle(img, (int(cx - r), int(cy - r)), (int(cx + r), int(cy + r)), color, t)

    for m in val.hits:
        cx, cy = m["centroide_xy"]
        cv2.circle(img, (int(cx), int(cy)), pad, BLACK, t + 2)
        cv2.circle(img, (int(cx), int(cy)), pad, WHITE, t)
    for m in val.misses:
        box(*m["centroide_xy"], pad, VERMILION)
    for m in val.false_pos:
        cx, cy = m["centroide_xy"]
        pts = np.array([[cx, cy - pad], [cx + pad, cy], [cx, cy + pad], [cx - pad, cy]], np.int32)
        cv2.polylines(img, [pts], True, BLACK, t + 2)
        cv2.polylines(img, [pts], True, BLUE, t)
    for c in fire.clusters:
        if c.confidence == "baja":
            continue
        cx, cy = c.centroid_xy
        _triangle(img, cx, cy - pad - 2 * t, max(5, pad // 2), YELLOW, BLACK, 1)
    m = val.metrics
    pc = lambda x: "—" if x is None else f"{round(100 * x)} %"
    lines = [f"Validación vs Wildfire: recall {pc(m['recall'])} · precisión {pc(m['precision'])}",
             f"Aciertos {m['aciertos']} · fallos {m['fallos_no_detectados']} · falsos positivos {m['falsos_positivos']}"]
    legend = ["Círculo blanco = llama real encontrada", "Cuadro bermellón = llama real NO encontrada",
              "Rombo azul = foco sin llama real (falso +)", "Triángulo amarillo = foco aceptado por el modelo",
              "Cian = píxeles de llama de la referencia"]
    fs = max(13, int(w / 70))
    font = _font(fs)
    row = int(fs * 1.55)
    band = Image.new("RGB", (w, row * (len(lines) + len(legend)) + fs), WHITE)
    d = ImageDraw.Draw(band)
    y = fs // 2
    for ln in lines + legend:
        d.text((fs // 2, y), ln, fill=BLACK, font=font)
        y += row
    return np.vstack([img, np.asarray(band)])
