# Licencias y atribuciones

## Note Cascade Studio

Código propio de este proyecto: **GNU Affero General Public License v3.0 o posterior** (archivo `LICENSE`).

**Por qué AGPL:** la aplicación importa y ejecuta **HOMR** (AGPL-3.0) y **PyMuPDF** (AGPL-3.0, o licencia
comercial de Artifex), y se integra con **Audiveris** (AGPL-3.0). Publicar el conjunto bajo AGPL-3.0 hace
que todas las piezas sean compatibles sin recurrir a trucos de integración.

**Obligaciones si distribuyes la aplicación** (por ejemplo, la carpeta `dist\NoteCascadeStudio` a otras
personas) o la ofreces como servicio en red: debes ofrecer el **código fuente completo** correspondiente
(este proyecto entero, incluidos los scripts) bajo AGPL-3.0, conservar estos avisos de licencia y no
añadir restricciones. `build_windows.bat` copia `LICENSE`, este archivo y el manual junto al ejecutable;
entrega también la carpeta del proyecto (o un enlace a ella). Para uso personal en tu propio equipo no hay
obligaciones adicionales. *Gratis no significa sin condiciones.*

## Componentes que se instalan o distribuyen

| Componente | Versión probada | Licencia | Cómo se usa | Origen |
|---|---|---|---|---|
| Audiveris | 5.11.0 | AGPL-3.0 | Programa externo, sin modificar, ejecutado por línea de órdenes. Se extrae del `.msi` oficial en `tools\audiveris`. Incluye su propio Java (OpenJDK, GPL-2.0 con excepción *Classpath*) y Tesseract (Apache-2.0) | github.com/Audiveris/audiveris |
| Datos de idioma de Tesseract (eng, ita, deu, spa, fra) | tessdata (commit ced7875) | Apache-2.0 | Datos para el OCR de Audiveris | github.com/tesseract-ocr/tessdata |
| HOMR | 0.7.0 | AGPL-3.0 | Biblioteca de Python ejecutada en un subproceso | github.com/liebharc/homr |
| Modelos de HOMR (ONNX) | `onnx_checkpoints` | AGPL-3.0 según el `LICENSE_FAQ.md` de HOMR, **con una salvedad**: se entrenaron con conjuntos de datos con licencias propias (DeepScoresV2 CC BY 4.0, OpenScore CC0, PDMX CC BY 4.0, GrandStaff…) y todos los publicados incluyen Camera-PrIMuS, cuya copia en Hugging Face es **CC BY-NC 4.0 (no comercial)**. HOMR lo considera una cuestión legal abierta y no ofrece licencia comercial (revisado en noviembre de 2026). Ver «Uso comercial» más abajo | Se descargan una vez desde las versiones oficiales de HOMR | github.com/liebharc/homr/releases |
| PyMuPDF | 2.0 | AGPL-3.0 o comercial (Artifex) | Lectura, análisis y rasterizado de PDF | github.com/pymupdf/PyMuPDF |
| PySide6 / Qt 6 (Essentials) | 6.12 | LGPL-3.0 | Interfaz gráfica; enlace dinámico (las DLL de Qt se pueden sustituir en la carpeta distribuida) | qt.io |
| NumPy | 2.x | BSD-3-Clause (y otras permisivas) | Cálculo | numpy.org |
| OpenCV (headless) | 4.x | Apache-2.0 | Procesamiento de imagen | opencv.org |
| Pillow | 12.x | MIT-CMU | Lectura de imágenes | python-pillow.org |
| mido | 1.3 | MIT | Archivos MIDI | github.com/mido/mido |
| python-rtmidi / RtMidi | 1.5 | MIT | Salida al sintetizador MIDI de Windows (opcional) | github.com/SpotlightKid/python-rtmidi |
| sounddevice / PortAudio | 0.5 | MIT | Salida de audio del sintetizador interno | python-sounddevice.readthedocs.io |
| RapidOCR y modelos PP-OCR | 3.x | Apache-2.0 | OCR de los textos de la partitura (solo para clasificarlos) | github.com/RapidAI/RapidOCR |
| ONNX Runtime | 1.x | MIT | Ejecución de los modelos de HOMR y del OCR | onnxruntime.ai |
| imageio-ffmpeg y FFmpeg | 0.6 | BSD-2-Clause (envoltorio); el binario de FFmpeg que incluye es GPL | Solo para la exportación opcional de vídeo, como programa externo | github.com/imageio/imageio-ffmpeg, ffmpeg.org |
| shapely, pyclipper, requests, omegaconf, PyYAML… | — | BSD/MIT/Apache-2.0 | Dependencias de HOMR/RapidOCR | PyPI |
| uv | 0.13 | MIT o Apache-2.0 | Solo durante la instalación | github.com/astral-sh/uv |
| Python | 3.12 | PSF | Intérprete | python.org |

## Uso comercial

La AGPL-3.0 permite cobrar por el programa, siempre que quien lo reciba obtenga también el código fuente y
pueda volver a distribuirlo. Los **modelos de HOMR** son la excepción dudosa: incluyen datos de entrenamiento
con una licencia no comercial (ver la tabla). Para una versión de pago, lo prudente es no incluir HOMR (dejar
solo Audiveris) o que el usuario lo descargue aparte desde HOMR, y consultarlo con un abogado. Esto no es
asesoramiento legal.

## Partituras

El programa no incluye partituras. Las partituras que se convierten con él pueden tener derechos de autor:
cada usuario es responsable de tener permiso para usarlas. Las partituras usadas en las pruebas reales
(`test_samples/`, `fotos/`) no se publican con el código por ese motivo; las pruebas que las necesitan se
saltan si no están.

## Solo para desarrollo y pruebas (no se distribuyen)

| Componente | Licencia | Uso |
|---|---|---|
| Verovio | LGPL-3.0 | Dibujar partituras de referencia para medir la precisión |
| music21 | BSD-3-Clause | Comprobación independiente del MusicXML exportado |
| pytest, pytest-qt | MIT | Pruebas automatizadas |
| PyInstaller | GPL-2.0 con excepción para el cargador | Generar el ejecutable (la excepción permite distribuir el resultado bajo otra licencia) |

## Otros

- **Sintetizador interno:** código propio (síntesis aditiva); no usa muestras ni SoundFonts de terceros.
- **Sintetizador MIDI de Windows** («Microsoft GS Wavetable Synth»): forma parte de Windows; la aplicación
  solo le envía mensajes MIDI y no lo redistribuye.
- **oemer** (MIT): motor opcional que no se instala ni se distribuye; si lo instalas tú, se detecta.
- **SheetToMidi**: se estudió pero no se integra (usa oemer internamente y no declara licencia).
- Iconos: glifos de las fuentes «Segoe Fluent Icons» / «Segoe MDL2 Assets» incluidas en Windows (no se
  distribuyen); el icono de la aplicación se genera con código propio (`scripts\make_icon.py`).
