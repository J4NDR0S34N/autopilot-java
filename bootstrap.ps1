<#
  Instalador de una linea para Windows. Abre PowerShell (normal, no administrador) y pega:

    irm https://raw.githubusercontent.com/J4NDR0S34N/autopilot-java/main/bootstrap.ps1 | iex

  Comprueba Git y Python (los instala con winget si faltan), descarga NetBeans Autopilot en
  %USERPROFILE%\autopilot-java (o lo actualiza) y lanza el asistente de configuracion.
#>
$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/J4NDR0S34N/autopilot-java.git"
$dest = Join-Path $HOME "autopilot-java"

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Ensure-Tool($cmd, $wingetId, $label) {
    if (Test-Cmd $cmd) { return }
    Write-Host "Falta $label."
    if (-not (Test-Cmd winget)) { throw "Instala $label a mano y repite (no se encontro winget)." }
    $a = Read-Host "Instalar $label ahora con winget? (S/n)"
    if ($a -and $a -notmatch '^(s|si|y|yes)$') { throw "$label es necesario." }
    winget install -e --id $wingetId --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    if (-not (Test-Cmd $cmd)) { throw "$label se instalo pero no se detecta: cierra y abre PowerShell y repite el comando." }
}

Ensure-Tool "git" "Git.Git" "Git"
if (-not ((Test-Cmd python) -and ((& python -c "import sys; print(int(sys.version_info >= (3,10)))") -eq "1"))) {
    Ensure-Tool "python" "Python.Python.3.12" "Python 3.10+"
}

if (Test-Path (Join-Path $dest ".git")) {
    Write-Host "Actualizando $dest ..."
    git -C $dest pull --ff-only
} else {
    Write-Host "Descargando en $dest ..."
    git clone $repoUrl $dest
}
Set-Location $dest
python autopilot.py setup
