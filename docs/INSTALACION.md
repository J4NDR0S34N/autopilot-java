# Instalación e integración (Windows)

> **Camino fácil:** `python autopilot.py setup` (o el instalador de una línea del README) hace todo lo de abajo
> automáticamente. Esta guía es para instalar a mano o resolver problemas.

Requisitos (probado en): Windows 11, Python 3.12, JDK 26.0.2, Apache NetBeans (lanzador 101.4) con
Maven 3.9.16 y Ant 1.10.17 incluidos. Claude Desktop 3.1623.1 / Claude Code 2.1.304.

## 1. Preparar
```powershell
cd C:\ruta\a\autopilot-java
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ProjectsRoot "C:\Users\TU_USUARIO\Documents\NetBeansProjects"
```
Crea `.venv` local, instala `mcp`, **registra el servidor en Claude Code** (ámbito usuario; omítelo con `-NoRegister`), genera `config.json` (si no existe) y `mcp.config.generated.json`, y ejecuta las pruebas
(con `-SkipTests` se omiten). No toca NetBeans ni el PATH.

`config.json` (opciones): `root`, `data_dir`, `java_home`, `maven_cmd`, `ant_cmd`, `gradle_cmd`, `protected_globs`
(patrones `Proyecto/ruta/*`), `read_only_projects`, `allow_network`, `allow_form_edit`, `build_timeout`, `max_run_timeout`.
Variables de entorno que lo sustituyen: `NB_AUTOPILOT_CONFIG`, `NB_AUTOPILOT_ROOT`, `NB_AUTOPILOT_DATA`.

## 2. Conectarlo a Claude Code Desktop
Claude Code Desktop lee servidores MCP de Claude Code (no de `claude_desktop_config.json`, que es de la app de chat).
Hay dos formas; elige una.

**A. Solo para sesiones abiertas en esta carpeta (ya preparado):** el archivo `.mcp.json` de `autopilot-java` define el
servidor. Abre una sesión de Claude Code en `C:\ruta\a\autopilot-java` y **aprueba** el servidor cuando lo pida.

**B. Disponible en todas tus sesiones (cambia tu configuración de usuario; hazlo tú):**
```powershell
Copy-Item "$env:USERPROFILE\.claude.json" "$env:USERPROFILE\.claude.json.bak"
claude mcp add netbeans-autopilot --scope user -e PYTHONPATH="C:\ruta\a\autopilot-java" -- "C:\ruta\a\autopilot-java\.venv\Scripts\python.exe" -m netbeans_autopilot
```
(Si `claude` no está en el PATH: `%USERPROFILE%\.local\bin\claude.exe`.) Abre una sesión **nueva** tras hacerlo.

## 3. Verificar que Claude lo descubre
```powershell
claude mcp list          # debe mostrar netbeans-autopilot ✔ Connected
```
En una sesión, escribe: *«Llama a get_environment y list_projects»*. Debes ver la raíz y tus proyectos.
Si Claude dice que no tiene esas herramientas: el servidor no está aprobado/conectado (ver «Problemas»).

## 4. Iniciar y detener
Lo inicia y detiene Claude automáticamente (proceso hijo por stdio). Para diagnosticar a mano: `run_server.cmd`
(espera protocolo en stdin; `Ctrl+C` lo detiene). Registro: `data\server.log`.

## 5. Actualizar y desinstalar
- Actualizar: `git pull`/copiar archivos y `.\install.ps1 -SkipTests` (reinstala dependencias; no pisa `config.json`).
- Desinstalar: `claude mcp remove netbeans-autopilot --scope user` (si usaste B), y borrar la carpeta `autopilot-java`.
  Tus proyectos no se ven afectados; las copias de seguridad están en `data\backups`.

## 6. Problemas habituales
| Síntoma | Causa / solución |
|---|---|
| `⏸ Pending approval` | Aprueba el servidor del `.mcp.json` al abrir `claude` en esa carpeta. |
| `CONNECTION_CLOSED` | El servidor no encuentra el módulo si Claude lo lanza desde otra carpeta: el `.mcp.json` debe llevar `env.PYTHONPATH` = carpeta de autopilot-java (ya incluido). |
| `Failed to connect` | Ejecuta `run_server.cmd`: un error de configuración sale por stderr y código 2. |
| «Falta la carpeta raíz» | `config.json` sin `root` o `NB_AUTOPILOT_ROOT` vacía. |
| «Maven no encontrado» | Pon `maven_cmd` en `config.json` (NetBeans trae `...\java\maven\bin\mvn.cmd`). |
| «release version 26 not supported» | El proyecto exige más Java del que tiene el JDK: cambia el JDK o el `release` del pom. |
| Maven falla con «offline mode» | Se reintenta en línea solo si `allow_network` es true y hay conexión. |
| Cambios que no ves en NetBeans | Vuelve a enfocar la ventana, o *Archivo > Recargar*; si el archivo estaba modificado sin guardar, guarda antes. |
| «modificado fuera de las herramientas» | Editaste en NetBeans tras un cambio del servidor; vuelve a leer el archivo. |
