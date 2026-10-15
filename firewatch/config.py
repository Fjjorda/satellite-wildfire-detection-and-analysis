"""Parámetros del pipeline.

Todos los umbrales viven aquí para poder ajustarlos sin tocar el código
(y, más adelante, exponerlos como sliders en la interfaz).

Los umbrales están expresados sobre magnitudes normalizadas o relativas a
la propia escena (percentiles, medianas), no sobre valores absolutos de
píxel, para que el programa generalice a imágenes nuevas.
"""
from dataclasses import dataclass, field, asdict


@dataclass
class BurnParams:
    # Pseudo-NBR = (G - R) / (G + R) en la composición SWIR (G≈B8A, R≈B12).
    # La ganancia de la composición se cancela en el cociente, así que el
    # valor tiene sentido físico: el NBR real < -0.1 indica quemado.
    pnbr_start: float = 0.02      # por debajo de esto empieza a contar como quemado
    pnbr_span: float = 0.20       # cuánto más bajo hace falta para puntuación máxima
    sat_start: float = 0.25       # saturación a partir de la que suma (lo quemado es rojo intenso)
    sat_span: float = 0.30
    # Umbrales de histéresis sobre la puntuación final [0, 1]:
    # "semillas" seguras (high) que crecen hacia píxeles conectados más débiles (low).
    hyst_high: float = 0.50
    hyst_low: float = 0.30
    smooth_sigma: float = 1.5     # suavizado de la puntuación (px) para evitar ruido sal y pimienta
    open_radius: int = 2
    close_radius: int = 3
    min_area_frac: float = 0.0004  # componentes menores que esta fracción de la imagen se descartan
    island_min_frac: float = 0.0003  # huecos interiores (no agua) mayores que esto se conservan como islas no quemadas
    strict_score: float = 0.60    # máscara "estricta" para dar un rango de superficie
    # Recuperación bajo el humo: el velo de humo tapa el rojo de la cicatriz, pero la banda
    # verde de la SWIR (B8A, NIR) sigue siendo más baja sobre lo quemado que sobre vegetación.
    smoke_recovery: bool = True
    smoke_sigma: float = 2.0       # suavizado de B8A antes de umbralizar (px)
    smoke_min_gap: float = 25.0    # separación mínima (niveles 0-255) entre "oscuro" y "claro" dentro del humo; si no, se abstiene
    smoke_dark_frac_min: float = 0.02  # fracción mínima / máxima de píxeles "oscuros" dentro del humo
    smoke_dark_frac_max: float = 0.60
    smoke_link_frac: float = 0.006  # el candidato debe tocar el área confirmada (distancia, fracción de la diagonal)
    smoke_min_area_frac: float = 0.0003
    # Modo SOLO True Color: lo quemado es oscuro y NO verde (índice ExG bajo). Estimación menos fiable
    # que con SWIR (sombras y bosque denso oscuro se parecen), por eso la oscuridad debe ser clara.
    tc_green_start: float = 0.08    # ExG = (2G-R-B)/(R+G+B): por debajo de esto empieza a contar como "no verde"
    tc_green_span: float = 0.08
    tc_dark_start: float = 0.50     # oscuridad relativa (0.5 = mediana de la escena) a partir de la que suma
    tc_dark_span: float = 0.30


@dataclass
class WaterParams:
    # Agua en SWIR: muy oscura y sin dominante roja (cromaticidad neutra/azulada).
    max_luma: float = 40.0        # luminancia máxima (0-255)
    max_red_excess: float = 12.0  # R - max(G, B) máximo: el quemado oscuro tiene R claramente mayor
    very_dark_luma: float = 15.0  # por debajo de esto es agua aunque haya ruido de color (JPEG); el quemado nunca es tan oscuro
    dilate_px: int = 2            # margen alrededor del agua (bordes mezclados agua/quemado)
    min_area_px: int = 6
    # Solo True Color: agua oscura con dominante azul respecto al rojo (el carbón es neutro/rojizo, B <= R)
    tc_max_luma: float = 85.0
    tc_min_blue_minus_red: float = 4.0


@dataclass
class SmokeParams:
    # Humo en True Color: brillante, poco saturado y neutro/azulado (B >= R).
    tc_min_luma: float = 120.0
    tc_max_sat: float = 0.14
    tc_min_blue_minus_red: float = -6.0
    min_area_frac: float = 0.0005
    # Confianza del humo: un penacho es grande, compacto y nace junto al incendio.
    # Los fragmentos pequeños, sueltos y lejos del quemado se marcan como "baja confianza".
    main_rel_area: float = 0.15    # >= 15 % del humo más grande de la escena => confianza alta
    near_rel_area: float = 0.03    # ...o >= 3 % si además está pegado al área quemada
    near_burn_frac: float = 0.02   # "pegado" = a menos de esta fracción de la diagonal del quemado
    min_solidity: float = 0.35     # un penacho es compacto; formas muy fragmentadas/finas => baja


@dataclass
class CloudParams:
    # Nube: muy brillante y casi blanca en todas las bandas (también en SWIR).
    min_luma: float = 215.0
    max_sat: float = 0.12


@dataclass
class FireParams:
    # Fuego activo en SWIR: canal rojo (B12) saturado o casi y muy superior a G (B8A).
    min_red: float = 200.0
    min_red_green_ratio: float = 1.45
    min_red_minus_blue: float = 90.0
    min_sat: float = 0.40
    cluster_merge_frac: float = 0.004  # une píxeles calientes próximos (fracción de la diagonal) en un mismo foco
    cluster_merge_min_px: int = 2
    # Contexto: un foco cerca del borde del área quemada es muy creíble;
    # uno aislado lejos de cualquier quemado probablemente es un tejado o suelo brillante.
    edge_ring_frac: float = 0.008  # ancho del anillo de borde, como fracción de la diagonal
    edge_ring_min_px: int = 6
    smoke_near_frac: float = 0.02  # distancia al humo para considerarlo "cerca del penacho"
    # Polígonos quemados pequeños y separados del principal son menos fiables
    # (pueden ser suelo rojizo): los focos en su borde bajan a confianza "media".
    secondary_frac: float = 0.05
    # Contraste local (como los algoritmos contextuales de MODIS/VIIRS): un foco real destaca
    # sobre su entorno inmediato. Si el rojo medio del foco no supera al de su anillo en
    # este valor, no es una anomalía térmica (superficie brillante uniforme) => descartado.
    min_contrast: float = 45.0
    context_ring_px: int = 4
    # Frente activo
    front_gap_frac: float = 0.012  # huecos sin foco más cortos que esto (fracción de diagonal) no parten un tramo
    front_min_len_frac: float = 0.006  # tramos más cortos (fracción de la diagonal) se ignoran
    head_cos: float = 0.35         # un tramo está "a favor del humo" si su dirección forma < ~70° con la del humo
    smoke_dir_min_frac: float = 0.03  # el penacho debe alejarse del incendio al menos esto para dar una dirección
    # Superficie con actividad: quemado a menos de este radio de un foco
    zone_radius_m: float = 250.0
    zone_radius_frac: float = 0.012   # si no hay escala, fracción de la diagonal
    # Segunda firma de llama (SWIR): las llamas muy intensas emiten también en B8A (verde), por lo que
    # en la composición salen amarillas/blancas y no cumplen R/G alto. Se aceptan si destacan MUCHO
    # de su entorno regional (no es una superficie brillante extensa) y mantienen B bajo (no es nube).
    hot_min_red: float = 225.0
    hot_min_red_minus_blue: float = 110.0
    hot_min_sat: float = 0.35
    hot_local_contrast: float = 60.0     # R menos la mediana regional de R
    hot_window_frac: float = 0.02        # tamaño de la ventana regional (fracción de la diagonal)
    # Solo True Color: las llamas apenas se ven; se exige más contraste y se limita la confianza a "media"
    tc_hot_local_contrast: float = 75.0
    tc_hot_min_red_green_ratio: float = 1.25
    hot_max_blob_frac: float = 6e-4      # una mancha de llama es pequeña; las extensas son suelo (fracción de la imagen)


@dataclass
class ValidationParams:
    """Validación contra una imagen de referencia (composición Wildfire). NO interviene en la detección.

    El script Wildfire (P. Markuse) solo pinta de AMARILLO los píxeles más calientes (B12+B11 > 2,0).
    El naranja/salmón también aparece en suelos brillantes, así que un grupo naranja solo se acepta
    como fuego real si tiene núcleo amarillo o es un fragmento pequeño pegado a uno que lo tiene.
    """
    core_min_px: int = 2
    attach_frac: float = 0.012     # distancia máxima a un grupo con núcleo (fracción de la diagonal)
    attach_max_frac: float = 8e-5  # tamaño máximo de un fragmento sin núcleo (fracción de la imagen)
    merge_frac: float = 0.004      # une píxeles de referencia próximos en un mismo foco real
    tol_frac: float = 0.004        # tolerancia espacial para dar un acierto (fracción de la diagonal)
    tol_min_px: int = 3


@dataclass
class Params:
    burn: BurnParams = field(default_factory=BurnParams)
    water: WaterParams = field(default_factory=WaterParams)
    smoke: SmokeParams = field(default_factory=SmokeParams)
    cloud: CloudParams = field(default_factory=CloudParams)
    fire: FireParams = field(default_factory=FireParams)
    validation: ValidationParams = field(default_factory=ValidationParams)

    def to_dict(self):
        return asdict(self)
