@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Note Cascade Studio - Generar ejecutable
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo No se encuentra la instalacion. Ejecuta primero install_windows.bat
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows.ps1" %*
if errorlevel 1 (
  echo.
  echo  No se pudo generar el ejecutable. Revisa los mensajes anteriores.
  pause
  exit /b 1
)
pause
