# FireSentinel · frontend

Desde esta carpeta:

```bash
npm ci
npm run dev
npm run build
npm test
```

El desarrollo usa `http://127.0.0.1:5173` y el proxy `/api` definido en `vite.config.ts`.
Las pruebas de navegador arrancan su propia API en el puerto 8001 y Vite en el 5174.

## Contrato de entrada

- La interfaz acepta SWIR, True Color o ambas. Wildfire es una referencia opcional y no sirve como única entrada.
- Se admiten de una a tres imágenes, con un tipo diferente para cada una.
- Nombres como `swir.jpg`, `true_color.jpg` y `wildfire.jpg` fijan la selección inicial.
  Para nombres sin un tipo claro, se utiliza la sugerencia visual de la API.
- La selección manual siempre prevalece. Las sugerencias duplicadas requieren corrección y nunca descartan una imagen automáticamente.
- Cada composición conserva su tipo en la petición. True Color nunca se envía como SWIR.
- La única opción editable es `max_side`. Las peticiones siempre incluyen `smoke_recovery: true` y omiten `sensitivity`.
- La API admite True Color sin SWIR. Las pruebas de navegador comprueban ese flujo con el motor real.
- La referencia Wildfire solo interviene en la validación posterior. La web usa el frente calculado por el motor.

## Pruebas con imágenes reales

`npm test` incluye las pruebas sintéticas y omite las que necesitan imágenes locales.
Para comprobar las escenas de `images/avila/`, `images/mierla/` e `images/niebla/`, ejecuta desde esta carpeta (PowerShell):

```powershell
$env:FIREWATCH_REAL_IMAGES = '1'
npx playwright test real-scenes.spec.ts
Remove-Item Env:FIREWATCH_REAL_IMAGES
```

Esta matriz comprueba 1000, 1600 y 2500 px con SWIR, True Color y la referencia Wildfire.
Verifica las asignaciones, los identificadores enviados y la generación de resultados con la API real.
Si la API rechaza un trabajo, el frontend muestra las imágenes enviadas, su resolución y el identificador del análisis.

## Descargas

- El selector junto al título de exportación elige el idioma de los archivos sin cambiar la pantalla.
- El ZIP incluye los informes localizados, PNG, PDF, CSV, TXT y todos los recursos disponibles del análisis.
- `compositions/` contiene las bases a resolución de análisis. `layers/` incluye también las capas ocultas en el visor.
- `masks/` contiene PNG binarios: blanco (255) identifica la categoría y negro (0) identifica el resto.
- El PNG del resultado conserva la base y las capas visibles al iniciar la descarga.
- Si falta un recurso, la descarga ZIP falla completa. Se puede reintentar desde el mismo botón.
