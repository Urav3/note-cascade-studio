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
foreach ($f in @("README.md", "README_ES.md", "LICENSE", "LICENCIAS_TERCEROS.md", "LIMITACIONES.md")) {
    if (Test-Path $f) { Copy-Item $f $Dist -Force }
}

Write-Host "==> Código fuente (obligación de la licencia AGPL-3.0 al distribuir)" -ForegroundColor Cyan
# Con git, el zip lleva exactamente los archivos del repositorio tal como están en la carpeta (lo que respeta
# .gitignore: sin partituras de prueba, fotos, proyectos, entornos ni archivos compilados). Sin git, una lista
# fija sin __pycache__.
$srcZip = Join-Path $Dist "codigo_fuente.zip"
if (Test-Path $srcZip) { Remove-Item $srcZip -Force }
$files = $null; $commit = ""; $dirty = $false
if (Get-Command git -ErrorAction SilentlyContinue) {
    $ErrorActionPreference = "Continue"          # git escribe en stderr; en PowerShell 5.1 eso no debe parar
    $oldEnc = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
    if ((& git rev-parse --is-inside-work-tree 2>$null) -eq "true") {
        $files = @(& git -c core.quotepath=off ls-files --cached --others --exclude-standard) |
                 Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
        $commit = (& git rev-parse HEAD 2>$null)
        $dirty = [bool](& git status --porcelain 2>$null)
    }
    [Console]::OutputEncoding = $oldEnc
    $ErrorActionPreference = "Stop"
}
if (-not $files) {
    $items = @("note_cascade", "tests", "scripts", "docs", "pyproject.toml", "requirements.txt", "requirements-lock.txt",
               "NoteCascadeStudio.spec", "install_windows.bat", "run_windows.bat", "test_windows.bat", "build_windows.bat",
               "README.md", "README_ES.md", "LICENSE", "LICENCIAS_TERCEROS.md", "LIMITACIONES.md", "TAREAS.md", ".gitignore") |
             Where-Object { Test-Path $_ }
    $files = foreach ($it in $items) {
        Get-ChildItem -LiteralPath $it -Recurse -File | Where-Object { $_.FullName -notmatch '\\__pycache__\\|\.pyc$|\\_salida_precision\\' } |
            ForEach-Object { $_.FullName.Substring($Root.Length + 1) }
    }
}
if ($dirty) { Write-Host "    [AVISO] Hay cambios sin guardar en git: el zip lleva los archivos tal como están ahora." -ForegroundColor Yellow }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::Open($srcZip, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($f in $files) {
        $entry = "note-cascade-studio/" + ($f -replace '\\', '/')
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $Root $f), $entry,
                                                                     [IO.Compression.CompressionLevel]::Optimal)
    }
    $info = "Código fuente de Note Cascade Studio que corresponde a este ejecutable.`r`n" +
            "Repositorio: https://github.com/Urav3/note-cascade-studio`r`n"
    if ($commit) { $info += "Versión del código (commit de git): $commit" + $(if ($dirty) { " + cambios sin guardar" } else { "" }) + "`r`n" }
    $info += "Generado: $(Get-Date -Format 'yyyy-MM-dd HH:mm')`r`n"
    $w = New-Object IO.StreamWriter($zip.CreateEntry("note-cascade-studio/VERSION_DEL_CODIGO.txt").Open(), (New-Object Text.UTF8Encoding $false))
    $w.Write($info); $w.Dispose()
} finally { $zip.Dispose() }
Write-Host ("    {0} archivos en codigo_fuente.zip" -f @($files).Count)

Write-Host "==> Prueba del ejecutable (diagnóstico)" -ForegroundColor Cyan
& (Join-Path $Dist "NoteCascadeStudio-consola.exe") --diagnostico
$size = (Get-ChildItem $Dist -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host ("Listo: {0} ({1:N0} MB). Abre NoteCascadeStudio.exe con doble clic." -f $Dist, $size) -ForegroundColor Green
