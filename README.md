# NetBeans Autopilot

Servidor **MCP local** (stdio) que permite a Claude Code inspeccionar, editar, compilar, ejecutar y diagnosticar
proyectos Java de Apache NetBeans dentro de **una carpeta raíz autorizada**. No es un chat ni un generador de
fragmentos: Claude trabaja sobre tus archivos reales y recibe la salida real de Maven/Ant/javac.

## Instalación rápida (3 pasos)

Requisitos: Windows, Python 3.10+, Apache NetBeans y [Claude Code](https://claude.com/claude-code).

```powershell
git clone https://github.com/J4NDR0S34N/autopilot-java.git
cd autopilot-java
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ProjectsRoot "C:\Users\TU_USUARIO\Documents\NetBeansProjects"
```

El script crea el entorno, ejecuta las pruebas y registra el servidor en Claude Code. Abre una sesión **nueva** de
Claude Code y pide: *«Llama a get_environment y list_projects»*. Detalles y solución de problemas en
[docs/INSTALACION.md](docs/INSTALACION.md).

> Es un servidor para **Claude Code**, no un plugin de NetBeans: Claude edita los archivos del proyecto y NetBeans los recarga.

## Arquitectura (y por qué)

```
Claude Code Desktop ──stdio/MCP──> netbeans_autopilot (Python + SDK oficial `mcp`)
                                     ├─ security.py   Guard: raíz autorizada, rutas reales, reglas de escritura
                                     ├─ edits.py      crear/editar/borrar/revertir, lotes atómicos, copias, registro
                                     ├─ projects.py   detección Maven/Ant/Gradle/simple, inspección, Swing/.form, BD
                                     ├─ build.py      compile / run / tests con lista cerrada de comandos
                                     ├─ runner.py     procesos sin shell, entorno reducido, timeout, kill del árbol
                                     └─ diagnostics.py errores de compilación, resultados de test, excepciones
```

- **Servidor MCP por stdio, sin GUI propia:** es el mecanismo que admite Claude Code (verificado con `claude mcp`),
  y evita abrir puertos de red.
- **Python + SDK oficial `mcp` (única dependencia):** ya disponible en el equipo, mantenido por Anthropic.
- **Maven/Ant de NetBeans:** se usan los incluidos en `C:\Program Files\Apache NetBeans` (no están en el PATH).
  Maven se ejecuta primero sin red (`-o`) y reintenta en línea solo si falla la resolución de dependencias.
- **Seguridad en el servidor, no en el modelo:** cada herramienta valida sus argumentos con independencia del prompt.

## Herramientas (19)

| Grupo | Herramientas |
|---|---|
| Inspección | `get_environment`, `list_projects`, `inspect_project`, `read_project_file`, `search_project_code`, `get_project_status` |
| Edición | `create_project`, `create_project_file`, `edit_project_file`, `apply_project_changes`, `validate_project_changes`, `revert_project_changes`, `get_change_summary` |
| Construcción | `detect_build_system`, `compile_project`, `run_project`, `run_project_tests`, `get_build_errors`, `inspect_runtime_logs` |

Las de solo lectura llevan `readOnlyHint`, para que Claude pueda pedirte permiso solo en las que escriben o ejecutan.

## Medidas de seguridad implementadas

- Raíz autorizada en `config.json` (`root`) o `NB_AUTOPILOT_ROOT`; nada fijado en el código. `data_dir` no puede estar dentro de ella.
- Rutas resueltas con `realpath` (enlaces simbólicos y uniones NTFS), rechazo de `..`, UNC, unidades ajenas, `:` (flujos
  alternativos), nombres reservados (`CON`, `NUL`…) y de salir del proyecto hacia otro.
- Escritura denegada en `target/`, `build/`, `dist/`, `out/`, `nbproject/private/`, `.git/`, extensiones no textuales
  (`.exe`, `.bat`, `.jar`…), proyectos en `read_only_projects` y patrones `protected_globs`.
- **Código del diseñador de NetBeans** (`GEN-BEGIN/GEN-END`, `GEN-FIRST/GEN-LAST`) y archivos `.form`: no se modifican.
- Nunca se borran `pom.xml`, `build.xml`, `nbproject/*`, `.jar`. Borrar requiere `confirm=true` y deja copia.
- Lotes atómicos con reversión automática; copia de cada archivo antes de tocarlo (`data/backups`), registro en
  `data/changes.jsonl`, `revert_project_changes`. `expected_sha256` detecta ediciones hechas en NetBeans entre tanto.
- Solo se ejecutan: `mvn`, `ant`, `gradle` (si está instalado), `javac`, `java`, con argumentos fijos. Los `mvnw`/`gradlew`
  del proyecto **no** se ejecutan. Nombre de clase y argumentos de `run_project` se validan con listas estrictas.
- Los procesos hijo reciben un entorno reducido (sin tus variables con claves). Sin shell.
- Al leer, se ocultan valores que parecen contraseñas/tokens/JDBC con contraseña; no se leen `.env`, claves, `nbproject/private`.
- XML con `DOCTYPE`/`ENTITY` rechazado (anti XXE).

## Uso diario

1. Abre Claude Code Desktop en una carpeta con el servidor activo (ver [docs/INSTALACION.md](docs/INSTALACION.md)).
2. Pide por ejemplo: *«En el proyecto PrimerCRUD, crea la clase Alumno con validación y haz que compile»*.
3. Claude seguirá: `list_projects → inspect_project → read/search → apply_project_changes → validate → compile →
   run/tests → get_build_errors/inspect_runtime_logs → get_change_summary`.
4. Si NetBeans tiene el proyecto abierto, recarga los archivos (suele hacerlo solo al volver a la ventana).
5. Para deshacer: pide *«revierte el último lote»* (`revert_project_changes`).

Consejos: indica el proyecto por su nombre; para Swing pide la UI **por código** o dime qué hacer en el diseñador
(las regiones generadas son de solo lectura).

## Limitaciones conocidas

- **No hay acceso al editor de NetBeans**: no puede leer la pestaña activa ni los errores subrayados del IDE.
- **No incluye cliente SQL** ni credenciales: no ejecuta consultas. MySQL/MariaDB **no está instalado** en este equipo,
  así que ninguna prueba contra MySQL se ha hecho. Puede crear scripts `.sql` y código JDBC, y compilarlos.
- **Gradle** no está instalado: solo se ha implementado, no probado. `gradlew` no se ejecuta por diseño.
- Maven/Ant: la ejecución usa `java -cp` con el classpath resuelto; las apps Swing se detienen al agotar `timeout_seconds`.
- El análisis de código es por expresiones regulares (no un compilador): puede fallar con construcciones inusuales.
- La detección de ediciones externas es por hash; sin Git no hay historial más allá de las copias del servidor.
- Un proceso lanzado por `run_project` ejecuta código del proyecto con tus permisos de usuario: es la naturaleza de ejecutar un programa.
- Los `.java` sueltos de la raíz (`Ejercicio1_...java`) no son un proyecto y las herramientas no los editan.

Más: [instalación e integración](docs/INSTALACION.md) · [pruebas y ejemplo](docs/PRUEBAS.md)
