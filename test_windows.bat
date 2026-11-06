@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo No se encuentra la instalacion. Ejecuta primero install_windows.bat
  pause
  exit /b 1
)
set "PY=%~dp0.venv\Scripts\python.exe"
echo == Pruebas del programa (rapidas) ==
"%PY%" -W ignore -m pytest -m "not omr" -q
set "RES=%ERRORLEVEL%"
if /I "%1"=="completo" (
  echo.
  echo == Pruebas con los motores OMR reales (lentas, varios minutos) ==
  "%PY%" -W ignore -m pytest -m omr -q -rA
  if errorlevel 1 set "RES=1"
  echo.
  echo == Precision del reconocimiento con partituras de referencia ==
  "%PY%" -W ignore tests\omr_accuracy.py tests\_salida_precision
)
echo.
if "%RES%"=="0" (echo Todas las pruebas ejecutadas pasaron.) else (echo Algunas pruebas fallaron.)
if /I not "%2"=="sinpausa" pause
exit /b %RES%
