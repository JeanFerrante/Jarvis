@echo off
rem Liga o Jarvis e abre no navegador (sem internet).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar-jarvis.ps1" -SoLigar
if errorlevel 1 pause
