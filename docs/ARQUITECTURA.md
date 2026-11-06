# Arquitectura

```
note_cascade/
├─ app/              rutas, configuración JSON, registro, errores con mensaje para el usuario, diagnóstico
├─ importers/        detección del tipo por contenido, carga robusta de imágenes (alfa, EXIF, 16 bits, CMYK,
│                    TIFF multipágina), documento de origen (PDF, imagen, portapapeles, MusicXML, MIDI)
├─ pdf_processing/   análisis de cada página PDF (vectorial / imagen / mixta / en blanco), capa de texto con
│                    posiciones y tamaño de fuente, rasterizado a la resolución elegida
├─ image_processing/ calidad de imagen (limpia, captura, escaneo, foto), detección de pentagramas, sistemas y
│                    barras, transformaciones (perspectiva, fondo, ruido, contraste, giro, escala) con matriz
│                    de coordenadas original ↔ procesada
├─ layout/           OCR (RapidOCR), clasificación contextual del texto, integración del texto en la partitura
├─ omr/              interfaz común de motores, adaptadores de Audiveris, HOMR y oemer, ejecución de procesos
│                    con cancelación, lectura del proyecto .omr (posiciones y puntuaciones), posiciones
│                    aproximadas, registro y selección de motores
├─ music_model/      modelo interno (notas con Fraction, compases, claves, armaduras, dinámicas, tempos…),
│                    alturas y ortografía, manos, interpretación temporal (repeticiones, ligaduras, tempo,
│                    intensidad, pedal), unión de páginas, ediciones con deshacer
├─ musicxml/         lector propio (conserva voces, alteraciones impresas, posiciones) y escritor MusicXML 4.0
├─ music_validation/ comprobaciones (ritmo, alturas, alteraciones, estructura, manos), comparación entre
│                    motores, correcciones aplicables, estados verde/amarillo/rojo
├─ midi/             escritor MIDI tipo 0/1, verificación por relectura, importación de MIDI
├─ player/           sintetizador interno (síntesis aditiva, pistas por mano) y motor de reproducción
│                    (sounddevice o sintetizador MIDI de Windows; reloj silencioso si no hay audio)
├─ piano_roll/       visualizador en cascada (mismo dibujante para pantalla y vídeo), teclado, editor
├─ export/           exportación verificada, informe HTML, vídeo MP4
├─ project/          proyecto .ncsproj (ZIP con imágenes y resultados), autoguardado y recuperación
├─ pipeline/         orquestación de la conversión con progreso real, caché de resultados de motores
└─ ui/               interfaz PySide6: ventana principal, cinco pasos, diálogos, tareas en segundo plano
```

## Flujo de datos

1. `importers.document.open_document` → `SourceDocument` con `SourcePage` (render bajo demanda).
2. `pipeline.ConversionPipeline.convert`:
   - `image_processing.pipeline.preprocess` por página → `ProcessedPage` (imagen original y procesada,
     matriz, calidad, geometría).
   - `layout.page_analysis.classify_text` → `TextElement` por página.
   - `omr.registry.plan_engines` → motor principal (y secundario si hace falta).
   - `OMREngine.recognize` → MusicXML (+ `.omr` en Audiveris) → `musicxml.reader` → `Score`.
   - posiciones (`omr.audiveris_omr` exactas o `omr.positions` aproximadas).
   - reparaciones con evidencia (`music_validation.repairs`), claves comprobadas en la imagen
     (`omr.clef_check`), notas comprobadas en la imagen (`omr.image_check`) e indicaciones de octava resueltas
     con la imagen (`omr.octave_lines`: alcance medido, pentagrama correcto, nunca dos veces).
   - manos y texto integrado.
   - `music_validation.validator.validate` (+ comparación) → `Issue` y estados.
3. `Project` reúne todo; la interfaz edita `project.score` mediante `AppController.apply_edit`
   (deshacer/rehacer) y revalida.
4. `music_model.timeline.build_performance` produce la `Performance` que comparten el reproductor, el
   visualizador y el exportador MIDI (lo que se oye es lo que se exporta).

### Alturas

`Note.midi` es siempre la altura **sonora** (como `<pitch>` en MusicXML). El lector de MusicXML no transpone
por `<octave-shift>`; las octavas se aplican una sola vez en `omr.octave_lines`, que guarda en la nota el
motivo (`Note.pitch_log`) junto a la altura original del motor (`Note.engine_midi`). Ni la interpretación, ni
el MIDI, ni la cascada vuelven a transponer (salvo la opción, desactivada por defecto, de separar manos al
escuchar). `omr.pitch_trace` reconstruye el recorrido de cada nota para el diagnóstico.

## Coordenadas

Las posiciones de notas, compases y textos se guardan normalizadas (0..1) respecto de la página original.
La matriz 3×3 de cada página convierte entre la imagen procesada (la que ve el motor) y la original (la que
ve el usuario), de modo que las superposiciones coinciden aunque la página se haya enderezado, recortado o
ampliado.
