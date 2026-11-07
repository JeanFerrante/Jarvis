@echo off
rem Instala o que falta, liga o Jarvis e abre no navegador.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar-jarvis.ps1"
pause
