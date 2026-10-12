# Warzone AutoClips AI

Captura pasiva de pantalla y OCR en CPU para registrar eliminaciones candidatas y pedir a OBS que guarde el Replay Buffer cuando hay **3 kills confirmadas en 15 segundos**. No accede a memoria del juego, no inyecta código ni automatiza controles. **La detección todavía requiere validación real en Warzone.**

## Estructura y reutilización

- `backend/obs_controller.py`: conexión autenticada existente; ahora permite reutilizarla y comprobar que apareció un replay nuevo.
- `backend/kill_detector.py`: OCR EasyOCR existente para imágenes; carga diferida y función adicional para una ROI en memoria. Se conservan el análisis de puntos y confirmación offline.
- `backend/live_events.py`: confirmación, deduplicación y disparo independientes de OCR/OBS.
- `backend/live_detector.py`: captura MSS, bucle de muestreo y registros JSONL de sesión.
- `backend/highlight_detector.py`: agrupación existente y esquema de highlights; se corrigió la ventana móvil cuando las primeras kills caducan antes de completar tres.
- `backend/session_highlights.py`: convierte registros de sesión a eventos y highlights del formato existente.
- `backend/analyzer.py` / `event_detector.py`: extracción y análisis offline existentes.
- `backend/clip_generator.py` / `processor.py`: clips horizontales y verticales existentes, con audio; sin cambios.
- `backend/watcher.py` / `main.py`: registro de archivos y API FastAPI existentes; sin cambios.
- `tests/`: eventos simulados, imágenes sintéticas, OBS simulado y prueba optativa de FFmpeg real.
- `frontend/` y `scripts/`: sin código en el checkout revisado.

## Preparación (PowerShell, desde la raíz)

Si ya existe `.venv`, úsala. Para un entorno nuevo:

```powershell
python -m venv .venv
```

Instalar dependencias originales y las que faltaban para OCR, OBS, FFmpeg y captura:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-live.txt
.\.venv\Scripts\python.exe -m backend.live_detector --prepare-ocr
```

La preparación puede descargar los modelos de EasyOCR. Hazla antes de jugar: el detector en vivo deshabilita las descargas y usa CPU. El backend requiere Python compatible con sus dependencias; las pruebas locales se ejecutaron con Python 3.12.

Configura `OBS_HOST`, `OBS_PORT` y `OBS_PASSWORD` en variables de entorno o en `.env` en la raíz. `.env.example` contiene solo nombres y valores vacíos. Si no tienes `.env`, puedes copiarlo y rellenar la contraseña localmente:

```powershell
Copy-Item .env.example .env
```

No reemplaces tu `.env` actual. Está excluido de Git, al igual que registros, capturas y videos. No se imprimen contraseñas ni detalles de excepciones del SDK. Mantén WebSocket autenticado en `127.0.0.1:4455` y el Replay Buffer iniciado en OBS. Para usar los analizadores originales cómodamente, configura OBS para guardar en `clips/raw`.

## Probar antes de guardar replays

Pruebas automatizadas sin capturar pantalla ni conectar OBS:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m backend.live_detector --simulate tests/fixtures/observations.json
```

La simulación no importa MSS ni OCR y no manda órdenes a OBS. La secuencia incluida confirma cuatro kills candidatas y registra una solicitud simulada. Al iniciar se imprime la ruta JSONL de esta ejecución.

Prueba optativa con FFmpeg real, video generado y tono de audio (no requiere OBS):

```powershell
$env:AUTOCLIPS_MEDIA_TEST = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_media_integration.py -v
Remove-Item Env:AUTOCLIPS_MEDIA_TEST
```

Después, prueba OCR durante una partida de práctica **sin guardar clips**:

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --dry-run
```

La ROI inicial reproduce la región inferior del detector existente: `0.36,0.86,0.62,0.98` (fracciones del monitor). Es un punto de partida, no una coordenada validada para todas las interfaces de Warzone. Ajusta a la región que muestra tu confirmación personal, excluyendo el kill feed de otros jugadores:

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --dry-run --monitor 1 --roi "0.36,0.86,0.62,0.98" --interval 0.5
```

El monitor 1 es el primer monitor físico de MSS; los siguientes usan 2, 3, etc. La captura refleja los píxeles visibles: evita ventanas superpuestas. Comprueba captura en tu modo de pantalla, escala de Windows y resolución.

## Iniciar y detener

Con modelos preparados, ROI calibrada y Replay Buffer iniciado:

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector
```

Detén con **Ctrl+C**. Un highlight que aún espera los cinco segundos finales se cancela y se registra; una orden de guardado ya enviada termina antes de cerrar la conexión. No cierres OBS hasta que finalice.

Configuración por defecto:

| Parámetro | Valor | Comportamiento |
|---|---:|---|
| `--interval` | 0.5 s | Hasta 2 muestras/s, sin recuperar frames atrasados |
| `--threads` | 1 | Un hilo de cómputo para Torch/OpenCV |
| `--confidence` | 0.65 | Descarta cajas OCR de menor confianza |
| `--confirmations` | 2 | Dos lecturas consecutivas del mismo nombre |
| `--dedup` | 8 s | No repetir un enemigo dentro de este intervalo |
| `--cooldown` | 30 s | Intervalo mínimo entre solicitudes, también ante errores |
| `--postroll` | 5 s | Espera después de la tercera kill antes del guardado |

Se requiere `ELIMINADO: nombre`, `ELIMINADA: nombre` o `ELIMINATED: nombre`. Se unen cajas OCR confiables para tolerar que el prefijo y el nombre aparezcan separados. Un banner persistente solo se cuenta una vez; el mismo nombre debe desaparecer al menos dos segundos y superar la deduplicación para rearmarse. Dos lecturas separadas por una pausa excesiva no confirman un evento. OCR inestable en el nombre puede producir errores: necesita calibración con capturas reales.

Se captura únicamente la ROI y se reduce a un máximo de 640 píxeles de ancho antes del OCR. EasyOCR aún tiene un costo de CPU y RAM: estos límites **no garantizan ausencia de pérdida de FPS**. El registro `sample_slow` informa cuando captura y OCR superan el intervalo. Si ocurre, aumenta `--interval` o reduce la ROI, teniendo presente que puedes perder banners breves. No se ejecuta FFmpeg mientras juegas. No se guardan capturas de pantalla por defecto.

OBS usa una conexión persistente y un único trabajador, sin cola de guardados. La orden se considera confirmada solo cuando cambia la ruta del último replay y existe el archivo local. Si hay timeout o desconexión, se registra resultado incierto y no se reenvía la misma orden; puede haber un archivo que OBS haya guardado de todos modos. La siguiente multikill, tras el cooldown, puede reconectar. Evita guardados manuales simultáneos, porque podrían confundirse con el solicitado por el detector.

## Diagnóstico de eliminaciones omitidas

Los registros existentes mostraron muestras de captura + OCR más lentas que los 0,5 segundos configurados (medianas aproximadas de 0,61–0,75 s en las muestras lentas). No contenían texto OCR de cada muestra, por lo que no permiten atribuir las kills omitidas al HUD, a confianza baja o a nombres inestables. Se agregó diagnóstico para distinguir esos casos. **No se cambiaron las dos confirmaciones, el cooldown de 30 s ni la estrategia activa por defecto.**

Desde la raíz, usando tu monitor y ROI, ejecuta esta línea exacta (si `python` ya apunta a la `.venv`, puedes sustituir el ejecutable):

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --diagnostics --dry-run --monitor 2 --roi "0.32,0.85,0.60,0.96" --interval 0.5 --confirmations 2
```

`--diagnostics` implica `--dry-run` incluso si omites este último: ninguna estrategia envía órdenes a OBS. Detén con **Ctrl+C** para cerrar el registro y mostrar el resumen. Se imprime al inicio la ruta exacta de `data/sessions/<sesión>.jsonl`. El OCR se mantiene en CPU (`gpu=False`), un hilo por defecto, una ROI y una sola llamada EasyOCR por captura. Las estrategias adicionales trabajan sobre el texto obtenido; no ejecutan otros modelos ni FFmpeg.

Cada registro `ocr_sample` contiene:

- `reading.ocr_seconds`: duración de redimensionado y OCR, separada de `capture_seconds`.
- `reading.raw_text`, `reading.text`: texto completo y texto después de filtrar confianza.
- `reading.boxes`: texto, confianza, coordenadas relativas al recorte redimensionado y aceptación por umbral de **cada caja**, incluidas las rechazadas. La confianza es la puntuación de EasyOCR, no una probabilidad calibrada de kill.
- `reading.input_shape`, `reading.ocr_shape`: tamaño de ROI y tamaño enviado al OCR.
- `strategies`: motivo de decisión, progreso y confirmaciones requeridas para cada estrategia.
- `filter_effect`: si el umbral quitó un prefijo o una candidata con nombre que sí estaba en el OCR sin filtrar.
- `timing.capture_gap_seconds`: intervalo medido entre inicios de captura; incluye OCR, registro, guardados y espera de la muestra anterior.
- `timing.late_seconds`, `estimated_missed_slots`: retraso sobre el intervalo solicitado y estimación conservadora de intervalos completos adicionales sin otra captura (`floor(gap / intervalo) - 1`, mínimo cero). Un retraso parcial se registra aunque el contador de slots sea cero.

**Los slots estimados no son frames perdidos del juego ni de OBS.** Capturamos muestras del escritorio; no conocemos el contenido entre capturas ni podemos afirmar que allí hubo una kill. Un aviso más breve que el tiempo real entre muestras puede no verse; uno visto una sola vez no se confirma.

Comparación con las mismas lecturas y el mismo umbral:

| Estrategia | Evidencia exigida | Limitación |
|---|---|---|
| `strict_name` | Prefijo, dos puntos y mismo nombre durante N lecturas | Estrategia existente; nombres mal leídos o descartados interrumpen la confirmación |
| `prefix_exact` | Prefijo exacto al inicio, con o sin dos puntos/nombre, durante N lecturas | Cuenta episodios del banner, exige ausencia observada durante 2 s para rearmarse y dedup de 8 s |
| `prefix_ocr_tolerant` | Igual, permitiendo una sustitución en el prefijo, como `ELIMINAD0` | Experimental; tolerar errores puede aumentar falsos positivos |

Las estrategias de prefijo no dependen del nombre y nunca cuentan repetidamente un banner continuo, aunque cambie el nombre. Por ese motivo pueden **subcontar eliminaciones rápidas con banners consecutivos sin separación**. No se activan para guardar replays. Sus eventos y motivos quedan dentro de `ocr_sample`, separados de las kills del detector activo. Más aceptaciones en una estrategia no demuestran mejor precisión: compara sus tiempos con las eliminaciones observadas y los recortes guardados.

Motivos del detector activo:

| `reason` | Explicación |
|---|---|
| `empty_or_low_confidence` | No quedó texto después del filtro; revisar cajas y `raw_text` |
| `invalid_prefix_colon_or_name` | El formato estricto no encontró prefijo con dos puntos y nombre de al menos dos caracteres |
| `waiting_confirmation` | Candidata válida; aún no alcanza N lecturas consecutivas |
| `name_changed` | Cambió el nombre reconocido y el contador volvió a uno |
| `sample_gap` | La pausa entre muestras superó `max_gap`; vuelve a empezar la confirmación |
| `dedup_ttl` | Evento ya contado dentro de la ventana de deduplicación |
| `persistent_banner` | Aviso ya contado que no ha desaparecido lo suficiente |
| `accepted` | Candidata confirmada por esa estrategia |

Una muestra vacía también registra `interrupted_count`, para detectar avisos que desaparecieron antes de completar N lecturas. Las estrategias de prefijo usan `prefix_not_recognized` cuando no aparece su palabra inicial. Los motivos describen el filtro aplicado; no prueban que el mensaje corresponda a una eliminación real.

Para guardar opcionalmente recortes de muestras sin confirmar, incluidos OCR vacío y nombres que cambian:

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --diagnostics --dry-run --monitor 2 --roi "0.32,0.85,0.60,0.96" --interval 0.5 --confirmations 2 --save-failed-images --failed-image-interval 2 --max-failed-images 100
```

Se guardan JPEG solo de la ROI en `data/sessions/<sesión>/failures/sample_<id>.jpg`, enlazados al `sample_id`. Se excluyen muestras aceptadas y banners ya deduplicados. La escritura corre en un hilo con **una imagen en vuelo**, sin cola; se registran `queued`, `writer_busy`, `rate_limited` o `limit_reached`. `diagnostic_image` confirma si se escribió. El límite se aplica a los intentos y la frecuencia mínima admitida es un segundo. No se guardan todas las fallidas: se muestrean para limitar CPU y disco. Los recortes y textos pueden contener nombres de jugadores; siguen excluidos de Git bajo `data/`.

Para medir otra frecuencia en una sesión separada, manteniendo las confirmaciones:

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --diagnostics --dry-run --monitor 2 --roi "0.32,0.85,0.60,0.96" --interval 0.75 --confirmations 2
```

`--interval` admite valores desde 0,2 s y `--confirmations` desde 2. No bajes el intervalo para intentar superar el tiempo de OCR: si tarda 0,75 s, pedir 0,25 s no produce cuatro lecturas por segundo. Más intervalo reduce trabajo por segundo cuando hay margen, pero puede perder avisos breves. Para un ensayo de menor costo de OCR puedes añadir `--ocr-max-width 512` (por defecto 640; rango 128–640), comparando reconocimiento y duración; reducir texto pequeño puede empeorar la detección. Reducir la ROI después de inspeccionar recortes evita procesar áreas irrelevantes. El diagnóstico añade registro en disco y las imágenes añaden codificación JPEG: mide también FPS/frametimes durante práctica, no se garantiza impacto cero.

Consultar el resumen después de jugar (sustituye `TU_SESION` por la ruta impresa):

```powershell
.\.venv\Scripts\python.exe -m backend.diagnostics "data/sessions/TU_SESION.jsonl"
```

El resumen muestra mediana, percentil 95 y máximo de tiempos OCR y entre capturas, slots estimados, cajas de baja confianza, aceptaciones y motivos por estrategia. Los registros anteriores sin `ocr_sample` no se pueden reconstruir.

Reevaluar **las mismas lecturas** offline con tres confirmaciones o menor umbral de confianza, sin cambiar la configuración activa ni volver a ejecutar OCR:

```powershell
.\.venv\Scripts\python.exe -m backend.diagnostics "data/sessions/TU_SESION.jsonl" --recheck-confirmations 3
.\.venv\Scripts\python.exe -m backend.diagnostics "data/sessions/TU_SESION.jsonl" --recheck-confirmations 2 --recheck-confidence 0.55
```

`recheck.accepted_seconds` permite revisar cada candidata de la comparación. Esta reevaluación no recrea capturas que no ocurrieron; para comparar intervalos necesitas sesiones separadas. No promociona una estrategia ni altera dedup/cooldown. No se permiten menos de dos confirmaciones.

Pruebas y diagnóstico simulado sin Warzone, pantalla ni OBS:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m backend.live_detector --diagnostics --simulate tests/fixtures/diagnostic_observations.json --interval 0.5 --confirmations 2
```

Las pruebas cubren prefijos con y sin nombre/dos puntos, nombres inestables, nombres distintos, avisos persistentes, desaparición tras una lectura, huecos de muestreo, rearme, confianza, tres confirmaciones y límites de imágenes. La simulación registra tiempos virtuales; no mide el costo real de OCR ni FPS.

## Investigar abatimiento y remate con `--debug-ocr`

La revisión del código y las pruebas no encuentran una dependencia de proximidad entre `ABATIDO` y `ELIMINADO`: **no se almacena el abatimiento como kill ni como primera confirmación**. Dos lecturas válidas de eliminación confirman la candidata aunque el remate llegue 0,5, 3, 8 o 30 segundos después, o aunque nunca se haya visto un abatimiento. Un abatimiento no inserta el enemigo en deduplicación. El intervalo de 15 s agrupa **kills ya confirmadas** para highlights, y el cooldown limita guardados, no el registro de kills.

En el registro real disponible `20261012T200625Z_27005582.jsonl` se observaron otras causas comprobables:

- Cerca de 365,5 s aparecieron cajas confiables con texto extra antes de `ELIMINADO:`; el parser estricto, que exige ese prefijo al inicio del texto concatenado, rechazó la muestra. Las siguientes lecturas del aviso estaban por debajo del umbral.
- Cerca de 453,7 s hubo una candidata válida, pero las lecturas siguientes perdieron el nombre/aviso por baja confianza y no completaron las dos confirmaciones.
- OCR tardó aproximadamente 0,78 s de mediana y los inicios de captura estuvieron separados aproximadamente 0,80 s, por encima de los 0,5 s solicitados.
- No había texto OCR de abatimiento reconocido en la ROI de ese registro. **Esto no demuestra que no haya habido abatimientos**; la región puede excluir el aviso, o el OCR no haberlo reconocido.

Estos datos explican descartes concretos, pero no permiten identificar que correspondan a remates tardíos ni demostrar que la demora modifique el HUD. No se relajaron los filtros, la confianza, las dos confirmaciones, la deduplicación ni el cooldown. Se añadió instrumentación para comparar los dos escenarios en juego real.

Comando exacto para tu **monitor 2 de 2560×1440**, conservando la ROI utilizada:

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --debug-ocr --dry-run --monitor 2 --roi "0.32,0.85,0.60,0.96" --interval 0.5 --confirmations 2 --threads 1 --save-combat-images --failed-image-interval 1 --max-failed-images 100
```

`--debug-ocr` activa diagnóstico y fuerza `--dry-run`. Muestra una línea JSON por muestra con texto bruto y filtrado, confianza/aceptación de cada caja, duración del OCR, monitor, resolución observada, rectángulo capturado, tamaño OCR, decisión, detalles de rechazo e intervalo real. La consola mostrará `text_before_elimination_prefix`, `confidence_filter_removed_strict_candidate`, `low_confidence_boxes_present`, `confirmation_interrupted` o `down_is_not_a_kill` cuando correspondan. Los mismos datos quedan en el JSONL.

En un monitor de 2560×1440, esta ROI ocupa **x=819, y=1224, ancho=717, alto=158** respecto al monitor. El `left/top` absoluto de MSS incorpora la posición del monitor 2 en el escritorio. Comprueba `[DEBUG-OCR REGION]`: debe mostrar resolución `[2560,1440]`. El programa usa la resolución que MSS realmente observa; el comando no cambia la resolución del juego ni de Windows.

`--save-combat-images` guarda recortes al leer palabras relacionadas con `ABATIDO`/`ABATIDA`/`DOWNED` o `ELIMINADO`/`ELIMINADA`/`ELIMINATED`, incluidas cajas de baja confianza y eliminaciones aceptadas o deduplicadas. Se toleran `0/O` y `1/I` **solo para seleccionar imágenes de diagnóstico**, nunca para contar kills. Los JPEG van a `data/sessions/<sesión>/combat/`, enlazados al ID de muestra. Comparte con el modo de fallidas el límite de intentos, frecuencia y una sola escritura en vuelo; algunos recortes pueden omitirse por esos límites. Si combinas ambas opciones, todos los recortes van al directorio `combat/`.

`combat_context` registra la última observación de abatimiento, la transición de etiquetas y los segundos desde ese texto hasta una lectura de eliminación. **No verifica que sea el mismo enemigo ni usa esos tiempos para aceptar kills.** Si no se reconoce el abatimiento, el tiempo queda vacío. Esto evita inventar una relación temporal a partir de OCR ausente.

Durante práctica, realiza por separado remates inmediatos y remates con varios segundos de espera, anotando sus tiempos de sesión. Compara las lecturas y capturas antes/durante/después del aviso, especialmente prefijo, cajas extra, confianza y duración visible. Si nunca aparece `ABATIDO` en esta ROI, localiza su región en una captura real. Puedes probar en otra ejecución la ROI de puntos utilizada por el analizador original (es una hipótesis pendiente de calibración):

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --debug-ocr --dry-run --monitor 2 --roi "0.59,0.39,0.79,0.57" --interval 0.5 --confirmations 2 --threads 1 --save-combat-images --failed-image-interval 1 --max-failed-images 100
```

Se analiza **una sola región por ejecución**; no se agregó un segundo OCR simultáneo ni captura de pantalla completa. No ejecutes ambos comandos a la vez. Este segundo ensayo sirve para inspeccionar avisos en otra región, no asegura que ese lugar corresponda al HUD de tu versión. Recortar avisos fuera de la ROI exige cambiar sus coordenadas; la selección de imágenes no puede descubrir texto que nunca se capturó.

Detén con Ctrl+C. El resumen incluye `combat_text_samples` y detalles de rechazo. Las pruebas añadidas comprueban remate inmediato/tardío, eliminación sin abatimiento, abatimientos persistentes sin kill, eliminación de una sola lectura sin confirmar y seis kills rápidas de nombres distintos con un único guardado simulado. El modo debug conserva una sola pasada OCR en CPU por captura; la consola y JPEG agregan trabajo, por lo que conviene usarlo durante ensayos cortos. No se ha medido aún el impacto real de este modo en FPS.

## Contador de kills como fuente principal

Con `--counter-config`, el detector registra kills **solo a partir de incrementos validados del contador personal**. No necesita `ELIMINADO:` ni nombres. Sin esa opción sigue disponible la estrategia anterior. No se incorporaron coordenadas supuestas para la esquina superior derecha: primero debes calibrar.

### Calibración visual en monitor 2, 2560×1440

Desde la raíz, con el HUD visible y su contador de kills:

```powershell
.\.venv\Scripts\python.exe -m backend.counter_calibration calibrate --monitor 2 --delay 8 --initial-roi "0.968,0.045,0.990,0.075" --output data/counter-roi.json
```

Durante esos ocho segundos vuelve al juego. El programa captura **una sola imagen del monitor completo**, luego abre una imagen congelada reducida para seleccionar dos regiones con el mouse. Primero selecciona **solo la calavera** encima del contador, con un pequeño margen; Enter termina la selección y otra vista ampliada permite verificarla con Enter. Después repite el procedimiento para **solo el número de kills personales**, dejando espacio para varios dígitos y excluyendo iconos, jugadores restantes y reloj. Esc cancela. No captura la ventana de calibración ni manda controles al juego. La captura en vivo guarda la resolución real de tu monitor 2: verifica que indique 2560×1440.

El rectángulo amarillo es el ROI numérico orientativo: no se acepta automáticamente. `--initial-skull-roi` permite orientar también la calavera si ya conoces su región; no se necesitan coordenadas supuestas. El JSON versión 2 guarda `skull_roi`, `counter_roi` y el umbral del icono. Conserva a su lado `data/counter-roi-skull.png`, la plantilla necesaria para ejecutar el detector. También se guardan la captura completa `counter-roi-monitor.png`, el número `counter-roi-counter.png` y los recortes ampliados `counter-roi-skull-zoom.png` y `counter-roi-roi-zoom.png`. El JSON solo se guarda después de confirmar ambas regiones. Las calibraciones antiguas de una región requieren recalibrar.

Si prefieres calibrar sobre una captura existente de TODO el monitor a resolución nativa:

```powershell
.\.venv\Scripts\python.exe -m backend.counter_calibration calibrate --monitor 2 --image "ruta/a/monitor-completo.png" --output data/counter-roi.json
```

El JSON guarda monitor, resolución y coordenadas normalizadas. Al ejecutar se verifica la resolución real de MSS; si cambia, exige recalibrar. La selección admite como máximo 320×160 píxeles para evitar regiones grandes por accidente; el OCR la reduce a un máximo de 160×48. Los archivos de calibración y las capturas quedan bajo `data/`, excluidos de Git. La selección visual requiere escritorio interactivo y OpenCV con ventanas, como el instalado en este proyecto. No se usa GPU ni OCR durante la calibración.

### Probar sin guardar replays

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --counter-config data/counter-roi.json --monitor 2 --phase warmup --debug-ocr --dry-run --interval 0.5 --counter-confirmations 2 --counter-confidence 0.85 --threads 1 --roi "0.32,0.85,0.60,0.96"
```

`--roi` sigue siendo el ROI inferior secundario; ambas regiones superiores se cargan del JSON. `--debug-ocr` muestra `[DEBUG-COUNTER]` con contador previo, nuevo, diferencia, confianza, tiempos, ROI, puntuación de la calavera y motivo de aceptación/rechazo. El JSONL registra `skull_config`, cada `skull_sample` y cada `counter_sample`, incluyendo lecturas omitidas por HUD oculto. `--diagnostics` también registra las lecturas y fuerza dry-run, pero no imprime cada contador. Ctrl+C detiene la sesión. Estos comandos no conectan ni activan OBS.

La calavera se compara mediante bordes y template matching de OpenCV, en CPU y sobre su pequeño recorte. Requiere dos coincidencias consecutivas (`--skull-confirmations 2`); una ausencia suspende inmediatamente el OCR numérico. El umbral inicial es 0,8, configurable al calibrar o al ejecutar con `--skull-confidence 0.8`. Es una similitud, **no una probabilidad de visibilidad**. Para validarlo, compara `score`, `threshold`, `matched` y `visible` en dry-run con la calavera presente, ausente, fondos distintos y pantallas de despliegue. La autocomprobación de la plantilla contra sí misma no valida esos escenarios. Si las puntuaciones de presencia y ausencia se solapan, mejora el recorte antes de ajustar el umbral. Cambiar escala/resolución del HUD requiere recalibrar; solo se tolera un pequeño desplazamiento dentro de la región.

La calavera indica **visibilidad del HUD**, nunca inicio de partida. Puede aparecer en prepartida: `warmup`, `loading` y `unknown` no registran kills aunque el icono y el número sean legibles. Cambia la fase explícitamente según los comandos siguientes.

Los fixtures `tests/fixtures/skull-real.png` y `hud-person-real.png` son recortes mínimos de tu captura real. La prueba verifica la plantilla frente a sí misma y rechaza el icono vecino; no mide generalización a otros fondos ni FPS. El OCR inferior de `ELIMINADO` sigue disponible como evidencia secundaria sin duplicar las kills del contador.

### Prepartida, carga y partida: control de fase

Un número aislado no distingue estos estados: **se usa una fase explícita, no una inferencia inventada**. El detector empieza en `unknown` por defecto y no cuenta hasta entrar en `match`. Puedes iniciar en `warmup` como arriba; durante la partida cambia desde otra terminal:

```powershell
.\.venv\Scripts\python.exe -m backend.counter_calibration phase match
```

Al volver a prepartida o entrar a una pantalla de carga:

```powershell
.\.venv\Scripts\python.exe -m backend.counter_calibration phase warmup
.\.venv\Scripts\python.exe -m backend.counter_calibration phase loading
```

Estos comandos solo escriben `data/counter-state.json`, que el detector consulta en cada muestra. No presionan teclas en Warzone ni conectan OBS. Usa `match` **antes de la primera kill**: las primeras dos lecturas establecen referencia y no recuperan kills anteriores al inicio. Si ya estás dentro de una partida, puedes iniciar directamente con `--phase match`; el valor que vea será la referencia inicial, aunque sea mayor que cero.

Una transición de fase invalida referencia, candidatas, evidencia secundaria y highlights pendientes. Repetir `phase match` también fuerza una referencia nueva, útil para otra partida. El cooldown de OBS se conserva entre fases. El archivo de fase se reinicializa al arrancar para no reutilizar un `match` de otra ejecución. Para varias instancias independientes usa rutas distintas con `--phase-file` en el detector y `--state-file` en el comando de fase.

Un descenso confirmado a cero necesita **cuatro lecturas** y genera `counter_reset`, nunca kills negativas. Entonces la sesión **se desarma a `unknown`**: confirma de nuevo `phase match` cuando corresponda. Con los dos ROIs, ocultar la calavera o recibir OCR inválido conserva el total y la fase sin caducar la referencia por tiempo. Al reaparecer exige al menos **tres lecturas numéricas válidas consecutivas del mismo valor** para recuperar la lectura y validar un incremento. La ausencia cancela la agrupación pendiente y el OCR secundario, conservando el cooldown. Las kills recuperadas llevan un intervalo temporal de incertidumbre: si es demasiado amplio, no se genera automáticamente un highlight de tres kills en 15 segundos. Un cambio de partida con un contador igual o mayor no puede distinguirse solo por el icono y los dígitos: usa `phase loading`/`warmup` al salir o cambiar de partida y `phase match` antes de comenzar la siguiente. No se infiere el estado de partida a partir de la calavera.

### Validación y fusión

- Por defecto exige dos lecturas consecutivas iguales y confianza OCR ≥0,85. Una lectura ausente, de baja confianza o diferente reinicia la candidata; una pausa excesiva impide confirmar con una lectura vieja.
- `N → N+1` confirmado registra una kill; `N → N+3` confirmado registra tres eventos con índices distintos. Un contador que permanece igual no repite eventos.
- Por defecto rechaza valores fuera de 0–100, aumentos superiores a cinco (`--counter-max-jump`) y descensos distintos de cero. No convierte números de varias cajas en un contador concatenado.
- La referencia inicial no registra kills. Cada incremento conserva `segundo` como tiempo de confirmación para compatibilidad e incluye `time_bounds.start/end`: última lectura del valor anterior y primera lectura de la nueva candidata estable. Todos los eventos de un salto comparten ese intervalo y `individual_time_observed=false`; no son timestamps individuales precisos. El agrupador exige que todo el intervalo de incertidumbre del grupo quepa en 15 s; la exportación traduce sus límites al tiempo del replay. Un intervalo más amplio no activa automáticamente una multikill.
- Cambios de ronda cancelan agrupación pendiente; el agrupador offline también evita mezclar épocas de contador distintas. Se conservan tres kills en 15 s, postroll de 5 s y cooldown de 30 s.
- El OCR inferior se consulta durante tres segundos tras un incremento, como máximo una vez por segundo (`--secondary-interval`). Usa el filtro y las dos confirmaciones existentes. Una señal confirmada se registra como `secondary_evidence`, asociada temporalmente a un evento primario reciente si está disponible. La asociación no prueba identidad; no cambia `enemigo=desconocido` del contador ni crea kills adicionales.
- El contador es la autoridad: **no hay fallback que genere kills desde el aviso inferior si el contador falla**. Así no se duplica una kill detectada por ambas fuentes. La evidencia inferior sin evento de contador también queda registrada en simulaciones y llamadas directas, sin sumar kills.

El OCR numérico usa `recognize` sobre una sola caja y `allowlist=0123456789`, sin el detector CRAFT de texto de la pantalla. Solo captura el ROI calibrado; aplica escala de grises, umbral Otsu (`--counter-preprocess otsu`, por defecto) y redimensionado. Puedes comparar con `--counter-preprocess gray`, que omite el umbral. Mantiene una sola pasada OCR, el Reader existente en CPU, un hilo por defecto y modelos previamente descargados. Se consideró template matching de OpenCV: disponer solo de un `0` real no permite validar plantillas de los otros nueve dígitos, por lo que se reutilizó el reconocedor existente. Otsu opera sobre el recorte pequeño.

El OCR inferior adicional es serial y puede retrasar algunas capturas; el contador acumulativo permite detectar un salto posterior. Los retrasos quedan en `sample_slow`; no hay cola de frames. Esto limita el trabajo, **no garantiza cero pérdida de FPS**. Compara CPU, frametimes y FPS antes de activar en partidas competitivas. El total confirmado se mantiene durante minutos si sigue habiendo lecturas válidas, aunque no haya kills. La pérdida de referencia se refiere a ausencia del HUD, no a inactividad de kills.

### Ejecutar con OBS después de validar

```powershell
.\.venv\Scripts\python.exe -m backend.live_detector --counter-config data/counter-roi.json --monitor 2 --phase warmup --interval 0.5 --counter-confirmations 2 --counter-confidence 0.85 --threads 1 --roi "0.32,0.85,0.60,0.96"
```

Activa `phase match` desde otra terminal cuando empiece la partida. OBS debe tener Replay Buffer iniciado y credenciales configuradas como antes. Detén con Ctrl+C. No añadas `--debug-ocr` al comando con guardado real: debug y diagnostics siempre fuerzan dry-run. Los registros y la generación posterior de clips horizontales/verticales funcionan como antes.

Resumen posterior de tiempos y motivos del contador:

```powershell
.\.venv\Scripts\python.exe -m backend.diagnostics "data/sessions/TU_SESION.jsonl"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas simulan incrementos, cinco minutos sin kills manteniendo el total, saltos de tres, 9→10, 10→11 y 19→20, rechazo de 1→11, oscilaciones, baja confianza, aparición/desaparición de la calavera, suspensión del OCR con HUD oculto, recuperación con confirmaciones adicionales, reinicios, prepartida, carga, fases, fusión, incertidumbre temporal y selección/cancelación de ambas regiones. La precisión del icono con fondos reales y el impacto en FPS necesitan validación en Warzone; las pruebas simuladas no demuestran esos resultados.

Se inspeccionó la captura real `Captura de pantalla 2026-10-12 174330.png`: el ROI orientativo contiene el `0` bajo la calavera. La imagen mide 2559×1439, un píxel menos que el monitor indicado; calibra sobre el monitor en vivo para guardar su resolución exacta. En esa imagen el OCR en gris leyó `32` con confianza 0,07 (rechazada); con Otsu leyó `0` con confianza 0,90. Es evidencia sobre **una captura estática de cero**, no sobre incrementos en partida ni estabilidad del resto de dígitos. No se activó OBS para probarlo. Sigue pendiente validar en juego real antes de usar el comando con guardado.

El fixture `tests/fixtures/counter-zero-real.png` contiene solo el recorte del dígito, sin la pantalla completa. Prueba optativa del OCR real, en CPU y sin descargas:

```powershell
$env:AUTOCLIPS_OCR_TEST = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:AUTOCLIPS_OCR_TEST
```

## Generar highlights después de jugar

Los registros se guardan en `data/sessions/<sesión>.jsonl`: kills con ID y tiempo de sesión, programación, solicitudes, rutas confirmadas, errores y cierre. Se escriben y vacían al disco en cada evento; una última línea incompleta por cierre abrupto puede recuperarse.

Exportar metadatos para revisión, copiando cada replay asociado a `clips/raw`:

```powershell
.\.venv\Scripts\python.exe -m backend.session_highlights "data/sessions/TU_SESION.jsonl"
```

Generar también clips horizontales y luego verticales mediante los módulos existentes:

```powershell
.\.venv\Scripts\python.exe -m backend.session_highlights "data/sessions/TU_SESION.jsonl" --generate --vertical
```

Se conserva el audio y los originales de OBS. Los nombres de las copias incluyen la sesión; se generan `data/events`, `data/highlights`, `clips/highlights` y `clips/processed`. Repetir la exportación de una sesión regenera sus metadatos y clips. Las kills ya exportadas no generan otro highlight en un replay posterior solapado.

El tiempo dentro del replay se estima como `tiempo_kill - (tiempo_solicitud - duración_real_video)`. La duración se lee con FFmpeg; no se suponen 120 segundos, porque el buffer puede estar calentándose o tener otra duración. El inicio se limita a cero y el final a la duración del video. Latencia de captura, OCR, WebSocket, OBS y keyframes pueden desplazar esta correspondencia: los highlights quedan **pendientes de revisión**, con sincronización aproximada. No es una sincronización frame a frame. Revisa y ajusta los tiempos en `data/highlights/*.json` antes de generar/publicar. Los guardados inciertos se revisan manualmente y no se exportan automáticamente.

La cadena offline anterior sigue disponible:

```powershell
.\.venv\Scripts\python.exe -m backend.analyzer "clips/raw/video.mkv"
.\.venv\Scripts\python.exe -m backend.event_detector "video.mkv"
.\.venv\Scripts\python.exe -m backend.highlight_detector "video.mkv"
.\.venv\Scripts\python.exe -m backend.clip_generator "video.mkv"
.\.venv\Scripts\python.exe -m backend.processor "clips/highlights/video_highlight_1.mp4"
```

## Validación pendiente en Warzone

1. Verificar ROI, texto real, idioma, resolución, HDR y duración del banner mediante capturas y una partida de práctica.
2. Comparar el registro con kills observadas manualmente: falsos positivos, kills omitidas y deduplicación de nombres con OCR inestable.
3. Comprobar tres kills en 15 segundos, cinco segundos finales, cooldown y recuperación después de apagar/desconectar OBS.
4. Medir CPU/RAM, FPS y frametimes antes y durante el detector en el equipo de juego.
5. Revisar duración, posición temporal, imagen horizontal/vertical y audio de los replays reales.

Las pruebas sintéticas validan la lógica y el procesamiento; no demuestran reconocimiento fiable del HUD ni rendimiento competitivo.

Referencias de integración: [obsws-python](https://github.com/aatikturk/obsws-python), [solicitudes de Replay Buffer de OBS](https://github.com/obsproject/obs-websocket/blob/master/src/requesthandler/RequestHandler_Outputs.cpp), [API de EasyOCR](https://www.jaided.ai/easyocr/documentation/).
