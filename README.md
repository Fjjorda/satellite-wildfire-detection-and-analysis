# firewatch · Detección de incendios en imágenes Sentinel-2

III Malackathon 2026 · Reto INDRA Espacio — *Detección y caracterización de un incendio mediante imágenes de satélite.*

Programa en Python que, a partir de las composiciones renderizadas de Sentinel-2 (PNG/JPG), **delimita el área
quemada** (reto básico) y **detecta los focos de fuego activo** (reto avanzado), devolviendo la misma imagen
con el perímetro y los focos dibujados. No entrena ningún modelo: usa reglas basadas en la física de la banda
SWIR, con umbrales relativos a cada escena para generalizar a imágenes nuevas.

**Estado: motor de análisis por línea de comandos y aplicación web local con React.**

## Qué hace

- **Área quemada y perímetro**, con las masas de agua excluidas de la superficie y las islas interiores sin quemar conservadas.
- **Fuego activo (reto avanzado), diferenciado de la superficie quemada sin actividad:**
  - **Focos** con confianza (alta/media) e intensidad, validados por contraste local y por contexto (borde del quemado, humo, nubes).
  - **Frente activo:** tramos del perímetro que arden ahora, como líneas con su longitud (km), y qué fracción del perímetro está activa.
  - **Superficie con y sin actividad:** hectáreas del área quemada a menos de 250 m de un foco frente a las ya apagadas.
  - **Dirección del humo** y parte del frente que mira hacia ella (probable cabeza del incendio).
- **Quemado probable bajo el humo:** estimación aparte, claramente marcada como menos fiable que el área confirmada.
- **Humo de baja confianza:** los fragmentos de humo pequeños o sueltos se marcan como posibles errores de detección y no se usan en el análisis.
- **Superficie con rango** (estricto–permisivo), perímetro en km, GeoJSON, informe JSON y descripción en lenguaje natural.
- **Accesibilidad de base:** paleta apta para daltonismo (Okabe-Ito), tramas y formas distintas (nunca solo color), leyenda fuera de la imagen y texto alternativo generado automáticamente.

## Instalación

Requiere Python 3.10 o superior. No necesita GPU.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

### Aplicación web local

La interfaz usa React, TypeScript y Bootstrap. El análisis se ejecuta en una API Python local.
Trabaja con imágenes y medidas en píxeles. Incluye:

- Español e inglés (`frontend/src/locales/es.json` y `en.json`), con preferencia guardada.
- Tema claro, oscuro, automático y alto contraste.
- Carga por botón o arrastre, sugerencia del tipo de composición y corrección manual.
- Progreso por etapas reales, avisos accesibles y navegación por teclado.
- Capas independientes, opacidad, zoom y comparador original/resultado.
- Simulación visual aproximada de protanopia, deuteranopia y tritanopia.
- Métricas, tabla de focos aceptados y descartados, y descripción determinista.
- PNG con leyenda, informe PDF etiquetado, JSON, CSV, TXT y máscaras binarias.

Requiere Python 3.10+ y Node.js 22.12+ (preferentemente una versión LTS).

**Terminal 1, desde la raíz del repositorio (PowerShell):**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
.\.venv\Scripts\python.exe -B -m uvicorn firewatch.web.app:app --host 127.0.0.1 --port 8000
```

En Linux/macOS, usa `.venv/bin/python` en lugar de `.\.venv\Scripts\python.exe`.
Si todavía no existe el entorno, créalo con `python -m venv .venv`.

**Terminal 2:**

```bash
cd frontend
npm ci
npm run dev
```

Abre **http://127.0.0.1:5173**. La documentación de la API está en **http://127.0.0.1:8000/docs**.
Sube una composición SWIR y, si está disponible, True Color de la misma escena.
Las sugerencias de composición son orientativas: revísalas antes de analizar.

#### Comportamiento de la versión web

- Acepta PNG/JPG de hasta 100 MiB y 100 millones de píxeles por archivo.
- Los archivos y resultados se guardan en `out/web/`. Cada análisis usa una carpeta distinta.
- La cola ejecuta un análisis cada vez. Sus referencias viven en memoria: al reiniciar la API, vuelve a subir las imágenes.
- Las bases, máscaras y coordenadas de focos usan la resolución de análisis, con un lado mayor máximo de 2500 px.
- El frente mostrado son sectores estimados del borde próximos a focos aceptados, no una línea de avance medida.
- El comparador muestra la imagen original y sus capas de análisis. No compara fechas diferentes.
- La simulación de daltonismo solo afecta a la vista previa. Las descargas conservan la paleta Okabe-Ito.
- El PNG usa las capas visibles. JSON y PDF conservan las métricas del análisis completo.
- El PDF incorpora idioma, estructura de lectura, fuentes incrustadas y texto alternativo para la imagen.
  Los focos aparecen como una lista textual etiquetada. La tabla interactiva tiene su alternativa HTML accesible.

#### Configuración y publicación del frontend

`frontend/.env.example` contiene las opciones disponibles. Usa `frontend/.env.local` para ajustes locales.

- `VITE_API_BASE_URL`: origen de la API. Vacío usa el proxy local de Vite.
- `API_PROXY_TARGET`: destino del proxy de desarrollo, por defecto `http://127.0.0.1:8000`.
- `FIREWATCH_DATA_DIR`: carpeta de datos de la API, por defecto `out/web`.
- `FIREWATCH_ORIGINS`: orígenes permitidos por la API, separados por comas. Por defecto admite los puertos locales 5173.

Para Vercel, selecciona `frontend` como raíz del proyecto y configura `VITE_API_BASE_URL` con la URL HTTPS de la API desplegada.
Autoriza ese origen web mediante `FIREWATCH_ORIGINS` en el servidor Python.
El frontend y el motor se despliegan como servicios separados. El dominio se puede gestionar desde Cloudflare con los registros indicados por Vercel.

```bash
npm run typecheck
npm run build
```

`npm run preview` permite revisar la compilación. El proxy pertenece al servidor de desarrollo:
para probar la compilación local, define la URL de la API y autoriza el origen del servidor de vista previa antes de compilar.

### Línea de comandos

```bash
python -m firewatch --swir images/avila/swir.jpg --truecolor images/avila/truecolor.jpg --out resultados/avila
python -m firewatch --swir images/niebla/swir.jpg --truecolor images/niebla/truecolor.jpg \
                    --wildfire images/niebla/wildfire.jpg --out resultados/niebla
```

Con **solo la composición Wildfire** también funciona, en modo reducido: detecta los focos (llamas), su longitud
y el humo, pero no el área quemada, que necesita la SWIR:

```bash
python -m firewatch --wildfire images/niebla/wildfire.jpg --out resultados/niebla_wf
```

En Windows usa `\` en las rutas (`images\avila\swir.jpg`) y ponlas entre comillas si tienen espacios.

| Opción | Obligatoria | Para qué |
|---|---|---|
| `--swir` | **sí** (salvo modo solo Wildfire) | Composición SWIR (B12-B8A-B4). Necesaria para el área quemada y el perímetro. |
| `--truecolor` | recomendada | Detecta mejor el humo y confirma el agua. Sin ella el resultado es casi igual, algo menos preciso. |
| `--wildfire` | opcional | Composición Wildfire: añade sus llamas validadas y fija la intensidad de los focos. Sola, activa el modo reducido. |
| `--out` | sí | Carpeta de salida (se crea sola). |
| `--base swir\|truecolor\|wildfire` | no | Imagen sobre la que se dibuja el resultado (por defecto SWIR). |
| `--pixel-size 10` | no | Metros por píxel **de la imagen original**, para obtener hectáreas y km. |
| `--bbox LON_MIN LAT_MIN LON_MAX LAT_MAX` | no | Extensión geográfica WGS84: da hectáreas y coordenadas reales en el GeoJSON. |
| `--max-side 2500` | no | Lado mayor máximo (px) con el que se analiza. Las imágenes mayores se reducen y la superficie se corrige. `0` = tamaño original (lento). |
| `--no-smoke-recovery` | no | No estima el quemado probable bajo el humo. |
| `--no-debug` | no | No genera el panel de diagnóstico (ahorra tiempo). |

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
| `resultado.geojson` | Área quemada (con islas), quemado probable bajo humo, focos (puntos) y frente activo (líneas). Abrible en QGIS o geojson.io. |
| `mascara_quemado.png`, `mascara_bajo_humo.png`, `mascara_focos.png`, `mascara_zona_activa.png` | Máscaras binarias. |

En `informe.json`, el bloque `fuego_activo` resume el reto avanzado: número de focos, tramos y km de frente activo,
fracción del perímetro activa, hectáreas con y sin actividad, dirección del humo y fracción del frente a favor del humo.
Cada foco incluye su contraste local, intensidad, nivel en la imagen Wildfire (si se aporta), los motivos de su
confianza y, con `--bbox`, sus coordenadas.

### Cómo leer `resultado.png`

| Elemento | Representación |
|---|---|
| Área quemada confirmada | Relleno bermellón con trama diagonal y perímetro con doble trazo |
| Quemado probable bajo humo | Relleno púrpura con trama cruzada y contorno punteado |
| Píxeles en llamas | Naranja con borde negro |
| Foco activo, confianza alta / media | Triángulo amarillo relleno / triángulo hueco |
| Frente activo | Línea tipo cinta de balizamiento negro/amarillo sobre el perímetro |
| Dirección del humo | Flecha celeste desde el origen del penacho |
| Agua dentro del perímetro | Azul (excluida de la superficie) |
| Humo | Contorno discontinuo celeste |
| Humo de baja confianza (posible error) | Línea fina punteada oscura |

La leyenda solo muestra los elementos presentes en cada imagen.

## Tests

```bash
pip install -r requirements.txt   # incluye pytest
python -m pytest -q
```

Pruebas sobre escenas sintéticas con verdad conocida. Entre ellas:

- un lago y un río fuera del incendio **nunca** se marcan como quemados;
- un lago dentro del incendio queda dentro del perímetro pero **no** suma superficie;
- una isla sin quemar dentro del incendio se conserva;
- el frente activo en el borde se detecta con confianza alta;
- un tejado caliente aislado y una nube se rechazan como fuego;
- una escena sin incendio devuelve "no detectado";
- el quemado bajo el humo se recupera como categoría aparte y no inventa área si bajo el humo solo hay vegetación;
- los fragmentos de humo pequeños o lejanos se marcan como baja confianza;
- el frente activo se traza sobre el borde que arde y no aparece si no hay fuego;
- la superficie con y sin actividad suma el área confirmada;
- un punto rojizo que no destaca de su entorno se descarta por falta de contraste;
- la dirección del humo y la cabeza del incendio se identifican correctamente;
- los niveles de la imagen Wildfire fijan la intensidad de los focos;
- en la Wildfire, el suelo salmón sin núcleo amarillo no se toma por fuego, y el modo solo Wildfire funciona;
- robustez frente a brillo ±20 %, compresión JPEG, reescalado ×0,5, marcos de captura y True Color de tamaño ligeramente distinto.

Las pruebas de la API requieren `requirements-web.txt` y usan el motor real con archivos temporales:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q
.\.venv\Scripts\python.exe -B -m pytest -q tests/test_web.py
```

Las pruebas del navegador arrancan una API y Vite en los puertos 8001 y 5174.
Generan escenas reproducibles en `out/browser-tests/`, sin depender de imágenes privadas.
Desde `frontend/`:

```bash
npx playwright install chromium
npm test
```

`FIREWATCH_PYTHON` permite seleccionar otro intérprete para estas pruebas. Por defecto usan la `.venv` de la raíz.
Cubren carga real, errores, capas, comparador por teclado, exportaciones, cambio de idioma, temas y tamaños móviles.
Las comprobaciones automáticas de accesibilidad usan axe. Los PDF se comprueban para idioma, etiquetas y fuentes incrustadas.

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
  │     • (+ llamas de la imagen Wildfire, si se aporta: naranja con núcleo amarillo, nivel rojo/naranja/amarillo)
  │     • Contraste local: el foco debe destacar sobre su entorno inmediato (como MODIS/VIIRS)
  │     • Contexto: borde del quemado → frente (alta); interior → rescoldo (media);
  │       junto a un polígono pequeño y separado → media; aislado → descartado
  │
  ├─ 3b. Caracterización del frente
  │     • Frente activo = tramos del perímetro a menos de un umbral de un foco (polilíneas suavizadas)
  │     • Zona con actividad = quemado a menos de 250 m de un foco; el resto, sin actividad
  │     • Dirección del humo = del borde del incendio al centro del penacho principal;
  │       tramos cuyo lado exterior mira hacia el humo → probable cabeza del incendio
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
  front.py        frente activo, zona con actividad y dirección del humo
  geometry.py     polígonos, perímetro, hectáreas, GeoJSON
  render.py       imagen anotada (Okabe-Ito, tramas, formas) y panel de diagnóstico
  pipeline.py     orquestación, informe y descripción textual
  cli.py          línea de comandos
  web/            API local y capas independientes en coordenadas de imagen
frontend/         React, idiomas, visor, exportaciones y pruebas de navegador
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
- **Solo True Color no es válido:** toda la lógica se apoya en la composición SWIR (o, para focos, en la Wildfire).
- **Wildfire:** validada con la imagen real de Niebla. El naranja solo se acepta como fuego si tiene núcleo amarillo
  (o es un fragmento pegado a uno que lo tiene), porque el fondo del script también pinta de salmón los suelos
  brillantes. Un frente muy débil, sin ningún píxel amarillo, se descartaría.
- **Modo solo Wildfire:** sin área quemada. "Frente" es la longitud de la línea de llamas y el contexto de los focos
  es "parte del incendio principal" en lugar de "borde del área quemada".
- **Dirección del humo:** supone la imagen orientada al norte. La "cabeza del incendio" es una inferencia
  (el fuego avanza a favor del viento), no una medición.
- **Zona con actividad:** el radio de 250 m es un criterio operativo ajustable (`zone_radius_m` en `config.py`).
- **Bandas crudas (GeoTIFF multibanda):** todavía no se aceptan; sería el "modo experto" con índices reales (NBR, MNDWI).
- **Validación:** hasta ahora, sobre una captura real de Ávila y escenas sintéticas. Falta contrastar con
  delimitaciones oficiales (Copernicus EMS) de las mismas fechas.

## Próximos pasos

1. Ajustar umbrales con las escenas reales del reto (Ávila, Niebla).
2. Validación cruzada de focos con NASA FIRMS (VIIRS/MODIS) cuando haya coordenadas.
3. Admitir bandas crudas (GeoTIFF) para usar índices espectrales reales.
4. Ampliar la validación de accesibilidad con lectores de pantalla y escenas reales en la interfaz web.
