<#
  Atajo para Windows: ejecuta el asistente multiplataforma (autopilot.py).
  Uso:   powershell -ExecutionPolicy Bypass -File .\install.ps1 [-ProjectsRoot "C:\ruta\a\proyectos"] [-SkipTests] [-NoRegister]
  -NoRegister no toca ningun cliente: solo genera mcp.config.generated.json.
#>
param(
    [string]$ProjectsRoot = "",
    [switch]$SkipTests,
    [switch]$NoRegister
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "No se encontro Python 3.10+ en el PATH. Instalalo desde python.org y repite." }
$a = @("autopilot.py", "setup")
if ($ProjectsRoot) { $a += @("--root", $ProjectsRoot) }
if ($SkipTests)    { $a += "--skip-tests" }
if ($NoRegister)   { $a += @("--client", "other") }
& python @a
exit $LASTEXITCODE
