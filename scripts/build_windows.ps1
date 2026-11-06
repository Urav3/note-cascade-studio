<#
  Genera la versión distribuible en dist\NoteCascadeStudio (carpeta portátil).
  Incluye Audiveris portátil y los datos de OCR si están en tools\.
  Al distribuir esta carpeta se distribuyen componentes AGPL-3.0 (HOMR, Audiveris,
  PyMuPDF): ver LICENCIAS_TERCEROS.md (hay que ofrecer el código fuente).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Uv = Join-Path $Root "tools\uv\uv.exe"
$env:UV_CACHE_DIR = Join-Path $Root "tools\uv-cache"

Write-Host "==> Comprobando PyInstaller" -ForegroundColor Cyan
& $Py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { & $Uv pip install --python $Py "pyinstaller>=6.10" }

Write-Host "==> Comprobando modelos de HOMR" -ForegroundColor Cyan
& $Py -W ignore -m homr.main --init | Out-Null

Write-Host "==> Generando icono" -ForegroundColor Cyan
& $Py scripts\make_icon.py

Write-Host "==> PyInstaller (varios minutos)" -ForegroundColor Cyan
& $Py -m PyInstaller --noconfirm --clean NoteCascadeStudio.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

$Dist = Join-Path $Root "dist\NoteCascadeStudio"
$DistTools = Join-Path $Dist "tools"
New-Item -ItemType Directory -Force $DistTools | Out-Null
foreach ($d in @("audiveris", "tessdata")) {
    $src = Join-Path $Root "tools\$d"
    if (Test-Path $src) {
        Write-Host "==> Copiando tools\${d}" -ForegroundColor Cyan
        robocopy $src (Join-Path $DistTools $d) /E /NFL /NDL /NJH /NJS /NP | Out-Null
    } else {
        Write-Host "    [AVISO] No existe tools\${d}: la versión distribuible no incluirá ese componente." -ForegroundColor Yellow
    }
}
foreach ($f in @("README_ES.md", "LICENSE", "LICENCIAS_TERCEROS.md", "LIMITACIONES.md")) {
    if (Test-Path $f) { Copy-Item $f $Dist -Force }
}

Write-Host "==> Código fuente (obligación de la licencia AGPL-3.0 al distribuir)" -ForegroundColor Cyan
$srcZip = Join-Path $Dist "codigo_fuente.zip"
if (Test-Path $srcZip) { Remove-Item $srcZip -Force }
$items = @("note_cascade", "tests", "scripts", "docs", "pyproject.toml", "requirements.txt", "requirements-lock.txt",
           "NoteCascadeStudio.spec", "install_windows.bat", "run_windows.bat", "test_windows.bat", "build_windows.bat",
           "README_ES.md", "LICENSE", "LICENCIAS_TERCEROS.md", "LIMITACIONES.md", "TAREAS.md") |
         Where-Object { Test-Path $_ }
Compress-Archive -Path $items -DestinationPath $srcZip -CompressionLevel Optimal

Write-Host "==> Prueba del ejecutable (diagnóstico)" -ForegroundColor Cyan
& (Join-Path $Dist "NoteCascadeStudio-consola.exe") --diagnostico
$size = (Get-ChildItem $Dist -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host ("Listo: {0} ({1:N0} MB). Abre NoteCascadeStudio.exe con doble clic." -f $Dist, $size) -ForegroundColor Green
