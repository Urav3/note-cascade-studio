# Note Cascade Studio

Conversor **local y gratuito** de partituras (PDF, fotos, capturas de pantalla) a **MIDI** editable, con
revisión asistida para quien no sabe leer partituras y un visualizador de notas **en cascada** al estilo de
los tutoriales de piano. Interfaz en español para Windows 10 y 11.

> **Versión beta.** El reconocimiento de partituras (OMR) nunca es perfecto: el programa marca lo que hay que
> revisar y explica por qué, pero conviene escuchar el resultado y compararlo con la partitura. Lo que se sabe
> que falla está en [`LIMITACIONES.md`](LIMITACIONES.md).

- Funciona sin conexión una vez instalado. Las partituras **nunca** se suben a internet.
- Reconoce la música con [Audiveris](https://github.com/Audiveris/audiveris) y
  [HOMR](https://github.com/liebharc/homr), y comprueba cada nota con la propia imagen.
- Exporta MIDI (tipo 1 con una pista por mano, o tipo 0), MusicXML, un informe y el proyecto para seguir
  corrigiendo más tarde.

## Descargar

- **Programa listo para usar:** en [Releases](../../releases) está la carpeta comprimida con
  `NoteCascadeStudio.exe` (≈ 1 GB, incluye Audiveris y los modelos de HOMR). Descomprímela y abre el `.exe`.
  Windows puede avisar de que el programa no está firmado: *Más información → Ejecutar de todas formas*.
- **Desde el código:** descarga este repositorio y ejecuta `install_windows.bat` (descarga y verifica
  Python, las bibliotecas, Audiveris y los modelos) y luego `run_windows.bat`.

## Documentación

| Archivo | Contenido |
|---|---|
| [`README_ES.md`](README_ES.md) | Manual completo: instalación, los cinco pasos, revisión, escucha, exportación, opciones |
| [`LIMITACIONES.md`](LIMITACIONES.md) | Qué reconoce bien, qué no y cómo se corrige |
| [`docs/RESULTADOS_PRUEBAS.md`](docs/RESULTADOS_PRUEBAS.md) | Pruebas automáticas y con partituras reales |
| [`docs/Simbolos_y_orden_de_lectura.pdf`](docs/Simbolos_y_orden_de_lectura.pdf) | Qué símbolos se leen y qué efecto tienen |
| [`LICENCIAS_TERCEROS.md`](LICENCIAS_TERCEROS.md) | Componentes de terceros y sus licencias |

## Licencia

**GNU AGPL-3.0 o posterior** ([`LICENSE`](LICENSE)), porque usa componentes AGPL (HOMR, Audiveris,
PyMuPDF). Puedes usarlo, estudiarlo, modificarlo y redistribuirlo; si lo distribuyes, debes ofrecer también el
código fuente bajo la misma licencia. Detalles y licencias de cada componente en
[`LICENCIAS_TERCEROS.md`](LICENCIAS_TERCEROS.md).

El programa no incluye partituras. Las que conviertas pueden tener derechos de autor: usa solo las que
tengas permiso para usar.
