@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Note Cascade Studio - Instalacion
echo.
echo  Note Cascade Studio - instalacion
echo  ----------------------------------
echo  Se descargaran (una sola vez) Python 3.12, las dependencias, Audiveris,
echo  los modelos de HOMR y los datos de OCR. Hace falta conexion a Internet
echo  solo durante la instalacion. No se necesitan permisos de administrador.
echo.
set "SHORTCUT="
choice /C SN /M "Crear un acceso directo en el escritorio"
if errorlevel 2 (set "SHORTCUT=") else (set "SHORTCUT=-AccesoDirecto")
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1" %SHORTCUT% %*
if errorlevel 1 (
  echo.
  echo  La instalacion no se completo. Revisa los mensajes anteriores.
  pause
  exit /b 1
)
echo.
pause
