@echo off
rem Inicia el servidor MCP por stdio. Normalmente lo lanza Claude; usalo a mano solo para diagnosticar.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m netbeans_autopilot
) else (
  python -m netbeans_autopilot
)
