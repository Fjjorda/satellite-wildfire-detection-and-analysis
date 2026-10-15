# firewatch · Detección de incendios en imágenes Sentinel-2

III Malackathon 2026 · Reto INDRA Espacio — *Detección y caracterización de un incendio mediante imágenes de satélite.*

Programa en Python que, a partir de las composiciones renderizadas de Sentinel-2 (PNG/JPG), **delimita el área
quemada** (reto básico) y **detecta los focos de fuego activo** (reto avanzado), devolviendo la misma imagen
con el perímetro y los focos dibujados. No entrena ningún modelo: usa reglas basadas en la física de la banda
SWIR, con umbrales relativos a cada escena para generalizar a imágenes nuevas.

**Estado: motor de análisis por línea de comandos, con modo lote `--batch` e índice HTML (sin interfaz web todavía).**

## Qué hace

- **Área quemada y perímetro**, con las masas de agua excluidas de la superficie y las islas interiores sin quemar conservadas.
- **Fuego activo:** focos con confianza (alta/media) e intensidad, validados por su contexto (borde del quemado, humo, nubes).
- **Quemado probable bajo el humo:** estimación aparte, claramente marcada como menos fiable que el área confirmada.
- **Humo de baja confianza:** los fragmentos de humo pequeños o sueltos se marcan como posibles errores de detección y no se usan en el análisis.
- **Superficie con rango** (estricto–permisivo), perímetro en km, GeoJSON, informe JSON y descripción en lenguaje natural.
- **Modo lote:** `--batch` analiza todas las escenas de una carpeta de una vez (emparejamiento por nombre de fichero) y genera `resumen.html` con todas las escenas en una tabla revisable.
- **Accesibilidad de base:** paleta apta para daltonismo (Okabe-Ito), tramas y formas distintas (nunca solo color), leyenda fuera de la imagen y texto alternativo generado automáticamente.

## Instalación

Requiere Python 3.10 o superior. No necesita GPU.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```bash
python -m firewatch --swir images/avila/swir.jpg --truecolor images/avila/truecolor.jpg --out resultados/avila
python -m firewatch --swir images/niebla/swir.jpg --truecolor images/niebla/truecolor.jpg \
                    --wildfire images/niebla/wildfire.jpg --out resultados/niebla
```

**Lote:** para analizar varias escenas de una vez, en lugar de ir de una en una:

```bash
python -m firewatch --batch images/ --out resultados/lote
```

Recorre la carpeta (también subcarpetas) y empareja las composiciones **por nombre de fichero**: en cada
carpeta, el que contiene `swir` es la SWIR, el que contiene `truecolor` (o se llama `tc`) el True Color y el
que contiene `wildfire` (también sirve `…_Wildfires.jpg`) la Wildfire. Una carpeta con SWIR = una escena;
sus salidas van a `--out/<nombre_de_la_carpeta>/`. Las carpetas sin SWIR se omiten con aviso, una imagen
rota no detiene el lote, y al final se genera `--out/resumen.html` con una fila por escena (miniatura,
superficie, focos, avisos y estado).

En Windows usa `\` en las rutas (`images\avila\swir.jpg`) y ponlas entre comillas si tienen espacios.

| Opción | Obligatoria | Para qué |
|---|---|---|
| `--swir` | **sí** (o `--batch`) | Composición SWIR (B12-B8A-B4). Contiene la información clave; sin ella no funciona. |
| `--batch CARPETA` | **sí** (o `--swir`) | Analiza todas las escenas de la carpeta, emparejando por nombre de fichero. Excluyente con `--swir`. |
| `--truecolor` | recomendada | Detecta mejor el humo y confirma el agua. Sin ella el resultado es casi igual, algo menos preciso. |
| `--wildfire` | opcional | Añade los píxeles calientes de la composición Wildfire (experimental). |
| `--out` | sí | Carpeta de salida (se crea sola). Con `--batch`, una subcarpeta por escena. |
| `--base swir\|truecolor\|wildfire` | no | Imagen sobre la que se dibuja el resultado (por defecto SWIR). |
| `--pixel-size 10` | no | Metros por píxel **de la imagen original**, para obtener hectáreas y km. |
| `--bbox LON_MIN LAT_MIN LON_MAX LAT_MAX` | no | Extensión geográfica WGS84: da hectáreas y coordenadas reales en el GeoJSON. |
| `--max-side 2500` | no | Lado mayor máximo (px) con el que se analiza. Las imágenes mayores se reducen y la superficie se corrige. `0` = tamaño original (lento). |
| `--no-smoke-recovery` | no | No estima el quemado probable bajo el humo. |
| `--no-debug` | no | No genera el panel de diagnóstico (ahorra tiempo). |

Con `--batch`, `--base`, `--pixel-size`, `--bbox`, `--max-side`, `--no-smoke-recovery` y `--no-debug`
se aplican a **todas** las escenas del lote. Códigos de salida: `0` todo correcto, `1` alguna escena ha
fallado (el resto se analiza igualmente), `2` no se ha encontrado ninguna escena con SWIR.

**Sobre la escala:** si la imagen es una captura o está reescalada, el tamaño de píxel ya no es 10/20 m. Sin
`--pixel-size` ni `--bbox` el resultado se da en píxeles y % de la imagen.

**Sobre el rendimiento:** con `--max-side 2500` (por defecto) una imagen de ~48 megapíxeles tarda unos 15 s en
una prueba sintética. Las escenas reales con mucho detalle pueden tardar más. La reducción no afecta a las
hectáreas, que se ajustan por el factor de reducción, pero `resultado.png` sale a la resolución reducida.

## Salidas (en la carpeta `--out`)

| Fichero | Contenido |
|---|---|
| `resultado.png` | Imagen con perímetro, área quemada (trama), quemado probable bajo humo, agua, humo, focos y leyenda. |
| `diagnostico.png` | Panel de 6 vistas con cada paso intermedio. **El más útil para revisar y ajustar umbrales.** |
| `informe.json` | Métricas, polígonos con confianza, focos aceptados y descartados (con motivo) y parámetros usados. |
| `descripcion.txt` | Resumen en lenguaje natural (base del texto alternativo accesible). |
| `resultado.geojson` | Área quemada (con islas), quemado probable bajo humo y focos. Abrible en QGIS o geojson.io. |
| `mascara_quemado.png`, `mascara_bajo_humo.png`, `mascara_focos.png` | Máscaras binarias. |
| `resumen.html` | *(solo `--batch`)* índice con una fila por escena: miniatura enlazada, superficie, perímetro, focos, avisos, tiempo y estado. |

### Cómo leer `resultado.png`

| Elemento | Representación |
|---|---|
| Área quemada confirmada | Relleno bermellón con trama diagonal y perímetro con doble trazo |
| Quemado probable bajo humo | Relleno púrpura con trama cruzada y contorno punteado |
| Foco activo, confianza alta / media | Triángulo relleno / triángulo hueco, con los píxeles calientes en amarillo |
| Agua dentro del perímetro | Azul (excluida de la superficie) |
| Humo | Contorno discontinuo celeste |
| Humo de baja confianza (posible error) | Línea fina punteada oscura |

## Tests

```bash
pip install -r requirements.txt   # incluye pytest
python -m pytest -q
```

26 tests sobre escenas sintéticas con verdad conocida + 10 tests del modo lote (pareado por nombre,
continuidad ante errores, resumen HTML y códigos de salida del CLI). Entre ellos:

- un lago y un río fuera del incendio **nunca** se marcan como quemados;
- un lago dentro del incendio queda dentro del perímetro pero **no** suma superficie;
- una isla sin quemar dentro del incendio se conserva;
- el frente activo en el borde se detecta con confianza alta;
- un tejado caliente aislado y una nube se rechazan como fuego;
- una escena sin incendio devuelve "no detectado";
- el quemado bajo el humo se recupera como categoría aparte y no inventa área si bajo el humo solo hay vegetación;
- los fragmentos de humo pequeños o lejanos se marcan como baja confianza;
- robustez frente a brillo ±20 %, compresión JPEG, reescalado ×0,5, marcos de captura y True Color de tamaño ligeramente distinto.

## Cómo funciona

```
SWIR (+ True Color, + Wildfire)
  │
  ├─ 0. Carga: reducción a --max-side, zonas sin datos (transparencia, marcos),
  │     autodetección del tipo de composición (aviso si los ficheros están cambiados)
  │
  ├─ 1. Máscaras de veto
  │     • Agua:  muy oscura en SWIR y SIN dominante roja (+ verde/azul oscura en True Color)
  │     • Humo:  en True Color, brillante, poco saturado y azulado (B ≥ R); respaldo en SWIR
  │     • Nubes: blancas también en SWIR (el humo no lo es)
  │
  ├─ 2. Área quemada (BÁSICO)
  │     • Pseudo-NBR = (G−R)/(G+R) en SWIR ≈ NBR real (B8A vs B12); la ganancia se cancela
  │     • Puntuación = pseudo-NBR × (oscuridad relativa + saturación + oscuridad en True Color)
  │     • Histéresis: crecen solo regiones con un núcleo muy seguro
  │     • Huecos: agua → dentro del perímetro, fuera del área; huecos grandes → islas sin quemar
  │
  ├─ 2b. Confianza del humo y quemado bajo el humo
  │     • El humo grande, compacto o pegado al incendio es de confianza alta; el resto, baja
  │     • Dentro del humo de confianza alta se separa lo oscuro (B8A bajo) de lo claro con Otsu;
  │       solo se conserva si forma una clase distinta y toca el área confirmada
  │
  ├─ 3. Fuego activo (AVANZADO)
  │     • Candidatos: rojo (B12) saturado y muy superior al verde (B8A), fuera de nubes
  │     • (+ píxeles calientes de la imagen Wildfire, si se aporta)
  │     • Contexto: borde del quemado → frente (alta); interior → rescoldo (media);
  │       junto a un polígono pequeño y separado → media; aislado → descartado
  │
  └─ 4. Salidas: perímetro, superficie con rango, confianza, imagen anotada accesible,
        GeoJSON, informe y descripción
```

Todos los umbrales están en `firewatch/config.py`, documentados. Son relativos a la escena o tienen
justificación física, y son los candidatos a sliders en la futura interfaz.

## Estructura

```
firewatch/
  config.py       parámetros (todos los umbrales)
  io.py           carga con reducción, marcos, alineación de tamaños
  features.py     luminancia, saturación, pseudo-NBR
  composition.py  autodetección SWIR / True Color / Wildfire
  masks.py        agua, humo, nubes y confianza del humo
  burned.py       área quemada (reto básico) y quemado bajo el humo
  fire.py         focos activos (reto avanzado)
  geometry.py     polígonos, perímetro, hectáreas, GeoJSON
  render.py       imagen anotada (Okabe-Ito, tramas, formas) y panel de diagnóstico
  pipeline.py     orquestación, informe y descripción textual
  batch.py        modo lote: descubrimiento de escenas por nombre, ejecución en serie e índice HTML
  cli.py          línea de comandos (una escena o --batch)
tests/            escenas sintéticas y tests
images/           (no versionada) imágenes de entrada
resultados/       (no versionada) salidas generadas
```

## Limitaciones conocidas

- **Humo denso:** si el humo es muy grueso, la SWIR queda contaminada y no hay señal del suelo. El programa no
  inventa área ahí: avisa de que la superficie puede estar infraestimada.
- **Quemado bajo el humo:** es una estimación validada visualmente y con escenas sintéticas, no medida. Se marca
  aparte y entra solo en el límite superior del rango de superficie.
- **Suelos rojizos y brillantes** (minería, como Riotinto cerca de Niebla) pueden aparecer como polígonos
  secundarios; sus focos se marcan con confianza media.
- **Sin True Color** el humo y el agua son menos fiables, y puede haber más zonas de humo de baja confianza.
- **Solo True Color no es válido:** toda la lógica se apoya en la composición SWIR.
- **Wildfire:** la decodificación está implementada pero aún no se ha probado con una imagen real.
- **Bandas crudas (GeoTIFF multibanda):** todavía no se aceptan; sería el "modo experto" con índices reales (NBR, MNDWI).
- **Validación:** hasta ahora, sobre una captura real de Ávila y escenas sintéticas. Falta contrastar con
  delimitaciones oficiales (Copernicus EMS) de las mismas fechas.

## Próximos pasos

1. Ajustar umbrales con las escenas reales del reto (Ávila, Niebla).
2. Trazar el frente activo como línea y calcular su longitud.
3. Admitir bandas crudas (GeoTIFF) para usar índices espectrales reales.
4. Interfaz web accesible (WCAG 2.2): subida de imágenes, capas conmutables, vista True Color/SWIR/Wildfire
   y sliders de sensibilidad.
