<#
  Prepara el entorno de NetBeans Autopilot. Solo crea archivos DENTRO de esta carpeta (.venv, config.json, .mcp.json).
  No modifica configuración global de Claude, NetBeans ni el PATH.

  Uso:   powershell -ExecutionPolicy Bypass -File .\install.ps1 [-ProjectsRoot "C:\ruta\a\proyectos"] [-SkipTests]
#>
param(
    [string]$ProjectsRoot = "",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# 1. Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "No se encontró Python 3.10+ en el PATH. Instálalo desde python.org y repite." }
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.10") { throw "Se necesita Python 3.10 o superior (detectado $ver)." }
Write-Host "Python $ver OK"

# 2. Entorno virtual local + dependencias
if (-not (Test-Path ".venv\Scripts\python.exe")) { & python -m venv .venv; if ($LASTEXITCODE) { throw "Falló la creación del venv" } }
& .\.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE) { throw "Falló la instalación de dependencias (¿sin conexión?)" }
& .\.venv\Scripts\python.exe -c "import mcp, importlib.metadata as m; print('mcp', m.version('mcp'))"

# 3. config.json (no se sobrescribe si ya existe)
if (-not (Test-Path "config.json")) {
    if (-not $ProjectsRoot) { $ProjectsRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "NetBeansProjects" }
    if (-not (Test-Path -LiteralPath $ProjectsRoot -PathType Container)) { throw "No existe la carpeta de proyectos: $ProjectsRoot (usa -ProjectsRoot)" }
    $cfg = [ordered]@{ root = (Resolve-Path -LiteralPath $ProjectsRoot).Path; protected_globs = @(); read_only_projects = @() }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot "config.json"), ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Write-Host "Creado config.json con root = $($cfg.root)"
} else { Write-Host "config.json ya existe: no se modifica." }

# 4. Fragmento .mcp.json de ejemplo con rutas absolutas de esta instalación
$py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$mcp = [ordered]@{ mcpServers = [ordered]@{ "netbeans-autopilot" = [ordered]@{
    command = $py; args = @("-m", "netbeans_autopilot"); env = [ordered]@{ PYTHONPATH = $PSScriptRoot } } } }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot "mcp.config.generated.json"), ($mcp | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
Write-Host "Generado mcp.config.generated.json (ver docs/INSTALACION.md para activarlo)."

# 5. Pruebas (usan un sandbox aislado; no tocan tus proyectos)
if (-not $SkipTests) {
    & .\.venv\Scripts\python.exe -m unittest tests.test_acceptance tests.test_mcp_protocol
    if ($LASTEXITCODE) { throw "Las pruebas fallaron; revisa la salida." }
}
Write-Host "Instalación completada."
