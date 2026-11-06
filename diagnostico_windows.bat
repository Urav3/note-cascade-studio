@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "OUT=%~dp0diagnostico.txt"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -W ignore -m note_cascade --diagnostico > "%OUT%" 2>&1
) else if exist "%~dp0dist\NoteCascadeStudio\NoteCascadeStudio-consola.exe" (
  "%~dp0dist\NoteCascadeStudio\NoteCascadeStudio-consola.exe" --diagnostico > "%OUT%" 2>&1
) else (
  echo No se encuentra la instalacion. Ejecuta primero install_windows.bat
  pause
  exit /b 1
)
type "%OUT%"
echo.
echo Informe guardado en %OUT%
pause
