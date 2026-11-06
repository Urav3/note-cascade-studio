# Investigación de motores OMR (octubre de 2026)

Se revisaron los repositorios, versiones, requisitos y licencias, y se **probaron** con los archivos del
usuario (`fotos\`) y con partituras de referencia generadas para las pruebas.

| | Audiveris | HOMR | oemer | SheetToMidi |
|---|---|---|---|---|
| Versión revisada | 5.11.0 (jul. 2026) | 0.7.0 (jun. 2026) | 0.1.7/0.1.8 (2023) | sin versiones (4 commits) |
| Licencia | AGPL-3.0 | AGPL-3.0 | MIT | no declarada |
| Tecnología | Java, análisis clásico + clasificador; OCR Tesseract | Python, segmentación + transformer (ONNX) | Python, U-Net + reglas | Envoltorio de oemer + music21 |
| Windows | Instalador `.msi` con Java incluido; se puede **extraer sin instalar** (`msiexec /a`) | `pip` (Python ≥ 3.11), CPU con ONNX Runtime; GPU NVIDIA/AMD opcional | `pip`; descarga de modelos en la primera ejecución | Tkinter + Poppler |
| Salida | MusicXML + proyecto `.omr` (posiciones y `grade` por símbolo) | MusicXML (sin posiciones en 0.7.0; sistemas con `--write-staff-positions`) | MusicXML | MIDI |
| Fuerte en | PDF vectoriales e impresos, escaneos limpios; varios pentagramas, voces, ligaduras, tresillos, dinámicas, letras, textos | Fotos de móvil, capturas pequeñas, perspectiva | Fotos | — |
| Débil en | Fotos con perspectiva o sombras; compases raros (12/8 no leído en «The Pool») | Dinámicas, articulaciones, dobles alteraciones, textos; sin confianza por nota | Lento (minutos/página), sin mantenimiento; su autor recomienda HOMR | — |
| Decisión | **Motor principal** para impresos | **Motor para fotos** y segundo motor | Adaptador opcional (instalación aparte) | No se integra (no añade capacidad propia) |

## Hallazgos con los archivos del usuario

- **«mi foto.png»**: PNG RGBA cuyo canal de color es negro en todos los píxeles; la tinta está solo en el
  canal alfa. Leído sin composición sobre blanco, es una imagen completamente negra y HOMR responde «No
  noteheads found». Con la composición correcta y ampliación ×3,2 (interlineado 6,9 → 22 px) ambos motores
  lo reconocen (17 compases, letra de la canción no convertida en notas).
- **«The Pool.pdf»** (PDF vectorial generado con PDFKit): los símbolos musicales son trazados, no texto; la
  capa de texto contiene título, compositor, números de compás, «accel.», «rit.» y los valores 65/75/133 de
  las indicaciones metronómicas. Audiveris reconoce los 69 compases pero no el compás 12/8 ni varios tempos:
  la aplicación los recupera del texto y propone 12/8.
- **«beethoven.pdf»** (escaneo de 2004, cifrado RC4 sin contraseña de usuario): Audiveris reconoce las páginas
  con posiciones exactas. Las digitaciones apiladas («4 3») y los números de compás en círculo se clasifican
  como texto no musical (en una versión previa se confundían con tempos; se corrigió y hay una prueba).
- **«claro de luna.pdf»** (parte de oboe, páginas compuestas por franjas de imagen): hay que rasterizar la
  página completa; título, créditos, «Adagio sostenuto», «Library No.», número de plancha y editorial se
  clasifican correctamente. Audiveris divide el libro en dos movimientos; la aplicación los une.

## Problema encontrado en Audiveris 5.11 y solución

Con la opción `-save` y un TIFF de varias páginas, Audiveris falla de forma intermitente al guardar hojas
procesadas en paralelo (`FileSystemAlreadyExistsException` / `NullPointerException`). Sin `-save` el libro se
exporta correctamente y el proyecto `.omr` (con posiciones y puntuaciones) se escribe igualmente, así que la
aplicación no usa `-save`.

## oemer en la práctica

`pip install oemer` instala ONNX Runtime y NumPy recientes, con los que su modelo no carga
(«Attribute pads must not contain negative values»). Funciona con `numpy<2`, `onnxruntime==1.16.3` y
OpenCV 4.10 en un entorno aparte (`tools\oemer`), tarda ≈ 5 minutos por página en CPU y no aporta confianza
por nota. Por eso es opcional y no forma parte de la selección automática por defecto.

## Otras alternativas consideradas

- **Extracción directa de notas de PDF vectoriales**: solo es posible si el PDF conserva los glifos de la
  fuente musical como texto (SMuFL). En los PDF probados son trazados, así que se descartó como motor y se
  usa la capa de texto para títulos, tempos y números de compás.
- **Entrenar un modelo propio**: descartado; no hay conjunto de datos ni evaluación que lo justifique. Se
  invirtió en preparación de imagen, validación, comparación entre motores y revisión.
- **Servicios en la nube** (de pago o no): descartados por privacidad y por el requisito de funcionar sin
  conexión.
