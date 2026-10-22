# Pruebas de aceptación y ejemplo

Ejecución real el 2026-10-22, JDK 26.0.2, Maven 3.9.16 de NetBeans, Python 3.12 (venv).
Todas usan un **sandbox aislado** (`.sandbox/`, ignorado por Git); tus proyectos nunca son el objetivo.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_acceptance tests.test_mcp_protocol -v     # 28 pruebas
```

| Prueba | Qué se comprobó | Resultado |
|---|---|---|
| A acceso | raíz, herramientas, enumerar, inspeccionar, leer, ocultar contraseñas, rechazar `..`/UNC/unidad/`:`/otro proyecto, unión NTFS que escapa | OK (7) |
| B creación | crear `Saludo.java`, compilar con Maven, ejecutar → `Suma: 5`; proyecto sin build con stdin: `4,-9 → -5`, `0,0 → 0`; excepción real analizada; clase `main` ambigua e inyección rechazadas | OK (4) |
| C corrección | error `cannot find symbol totl` (línea 6, col 28) detectado, corregido, validado, recompilado y ejecutado → `5` | OK |
| D Swing | compila (con código del diseñador), abre `JFrame`, 2 clics en el botón → `CLICKS=2` | OK, con ventana real (hay escritorio) |
| E MCP | arranque, `initialize`, 19 herramientas con anotaciones, llamada real, `isError` en: fuera de raíz, proyecto inexistente, argumentos inválidos, herramienta desconocida, solo lectura; arranque sin configuración → código 2 sin ruido en stdout; flujo completo por protocolo | OK (4) |
| E Claude real | `claude -p --mcp-config`: servidor `connected`, 19 herramientas visibles, Claude las invocó y arregló un proyecto (abajo) | OK **con Claude Code CLI**; ver nota |
| F protección | archivo protegido por config, proyecto solo lectura, `target/`, `.git/`, `.exe`, `.bat`, borrar `pom.xml`, sobrescribir, regiones del diseñador, `.form`, lote atómico revertido, edición externa detectada, revertir, proyectos reales intactos | OK (11) |
| Integridad | 212 archivos de `NetBeansProjects` comparados (ruta, tamaño, fecha, hash) antes/después | Sin cambios |

**Nota sobre Claude Code Desktop:** la invocación real desde Claude se hizo con el ejecutable de Claude Code
(`claude.exe -p`) y una configuración MCP temporal. La app Desktop usa el mismo motor, y `claude mcp get` reconoce el
`.mcp.json`, pero la sesión de Desktop en la que se desarrolló esto no pudo cargar el servidor nuevo: **falta que lo
apruebes y compruebes en una sesión nueva** (docs/INSTALACION.md, paso 2-3).

## Ejemplo completo: «RotoMaven no compila, arréglalo»
Claude, sin intervención, ejecutó 10 turnos con esta secuencia (registro real):

`inspect_project → compile_project (falla) → read_project_file → edit_project_file → validate_project_changes →
compile_project (BUILD SUCCESS) → run_project → get_change_summary`

- Error real devuelto: `src/main/java/demo/Main.java`, línea 6, columna 28, `cannot find symbol`, `symbol: variable totl`.
- Corrección: `System.out.println(totl);` → `System.out.println(total);`
- Ejecución: código de salida 0, salida exacta `5`.
- Resumen: 1 archivo modificado, copia de seguridad `380878ab91-0.bak`, reversible con `revert_project_changes`.

## Qué NO se ha probado
- Claude Code **Desktop** (interfaz) cargando el servidor (pendiente de tu aprobación).
- Cualquier operación con MySQL/MariaDB (no instalado), Gradle (no instalado), Ant con compilación real (solo detección e inspección sobre `objectDb`).
- Compilar/ejecutar tus proyectos reales: deliberadamente no se hizo, porque Maven/Ant escribirían `target/` o `build/` en ellos.
