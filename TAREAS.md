# Estado del proyecto

Última actualización: 05/11/2026 · versión 1.0.0 beta 1 (primera versión publicada en
https://github.com/Urav3/note-cascade-studio; los fallos y las ideas nuevas se apuntan allí, en *Issues*)

## Hecho

- [x] Investigación de Audiveris, HOMR, oemer y SheetToMidi (versiones, licencias, pruebas reales) → `docs/INVESTIGACION_MOTORES.md`
- [x] Importación: PDF (vectorial, escaneado, foto/captura incrustada, mixto, multipágina, girado, cifrado sin
      contraseña de usuario), PNG/JPG/WEBP/BMP/TIFF (incl. transparencia, EXIF, 16 bits, CMYK, TIFF multipágina),
      portapapeles (Ctrl+V), arrastrar y soltar, varios archivos, MusicXML/MXL y MIDI directos; detección por contenido
- [x] Preparación de imagen según el tipo (perspectiva, fondo/sombras, ruido, contraste, inclinación,
      orientación 90°/180°, escala por interlineado, re-rasterizado de PDF), sin tocar imágenes limpias
- [x] Detección propia de pentagramas, sistemas y barras; páginas sin música
- [x] Texto: capa del PDF + OCR, clasificación contextual en 24 categorías, integración (tempo, rit./accel.,
      8va medida y aplicada o propuesta, títulos, nombre de instrumento) sin generar notas
- [x] Motores: Audiveris (portátil, posiciones y confianza del `.omr`), HOMR (subproceso), oemer (opcional);
      selección automática, segundo motor solo si hace falta, respaldo por página, caché de resultados
- [x] Modelo musical, lector y escritor MusicXML propios (contrastados con music21)
- [x] Validación: ritmo, anacrusa, duraciones, tresillos, ligaduras, voces, rango, octavas, alteraciones,
      armaduras, claves, duplicados, manos, compases vacíos, sistemas duplicados, repeticiones, tempo,
      comparación entre motores; correcciones propuestas aceptables/rechazables; estados verde/amarillo/rojo/gris
- [x] Revisión visual: página con superposiciones, avisos, inspector de nota, editor de rollo de piano,
      deshacer/rehacer, escuchar compases, cambiar armadura/clave/tempo/compás
- [x] MIDI tipo 1 (pistas por mano) y tipo 0, verificados por relectura; MusicXML; informe HTML; proyecto
- [x] Reproductor local (sintetizador interno o MIDI de Windows), visualizador en cascada con bucles,
      velocidad, silenciar manos, rango de teclado; vídeo MP4 opcional; ventana «Ver partitura» que sigue
      la reproducción resaltando las notas que suenan
- [x] Proyectos `.ncsproj`, autoguardado y recuperación, limpieza de temporales
- [x] Interfaz en español en cinco pasos, opciones avanzadas, diagnóstico, licencias
- [x] Pruebas automatizadas (programa + motores reales + precisión) → `docs/RESULTADOS_PRUEBAS.md`
- [x] Scripts `install_windows.bat`, `run_windows.bat`, `test_windows.bat`, `build_windows.bat`; ejecutable PyInstaller

## Pendiente / ideas de mejora

- [x] Instalación desde cero en una carpeta limpia (mismo equipo): correcta tras corregir dos fallos.
- [x] oemer probado de extremo a extremo en su entorno aislado (requiere versiones fijadas, ver README §6).
- [ ] Probar la instalación y el ejecutable en otro equipo y en Windows 10.
- [ ] Posiciones exactas por nota con HOMR cuando una versión publicada incluya los comentarios `imgpos`
      (el lector ya los interpreta si aparecen).
- [ ] Selección por rectángulo en la imagen de la página y edición directa sobre ella.
- [ ] Reproductor de SoundFont (.sf2) opcional.
- [ ] Detección de la figura de pulso de las indicaciones metronómicas dibujadas (♩/♩.) en PDF vectoriales.
- [ ] Mejor heurística de manos en pasajes de manos cruzadas o en un solo pentagrama.
- [ ] Firma digital del ejecutable (Windows SmartScreen puede avisar al abrirlo la primera vez).
