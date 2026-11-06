<#
  Instalación de Note Cascade Studio en Windows 10/11 (sin permisos de administrador).

  Todo se instala dentro de la carpeta del proyecto:
    tools\uv            gestor de Python «uv» (Astral, licencia MIT/Apache-2.0)
    tools\python        Python 3.12 independiente (no modifica el Python del sistema)
    .venv               entorno con las dependencias de la aplicación
    tools\audiveris     Audiveris portátil (AGPL-3.0, extraído del instalador oficial .msi)
    tools\tessdata      idiomas de OCR de Tesseract para Audiveris (Apache-2.0)

  Las descargas proceden de los repositorios oficiales de GitHub y se verifican
  con SHA-256 (los resúmenes publicados por GitHub o los fijados abajo).
#>
param(
    [switch]$SinAudiveris,
    [switch]$SinModelos,
    [switch]$AccesoDirecto
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root "tools"
$Downloads = Join-Path $Tools "downloads"
New-Item -ItemType Directory -Force $Tools, $Downloads | Out-Null
$env:UV_CACHE_DIR = Join-Path $Tools "uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools "python"

function Paso($texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Cyan }
function Ok($texto) { Write-Host "    [OK] $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "    [AVISO] $texto" -ForegroundColor Yellow }

function Get-Release($repo) {
    Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -Headers @{ "User-Agent" = "NoteCascadeStudio" }
}

function Get-Verified($url, $dest, $expected) {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    $hash = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($expected) {
        $expected = $expected.ToLower().Replace("sha256:", "")
        if ($hash -ne $expected) {
            Remove-Item $dest -Force
            throw "La suma SHA-256 de $(Split-Path -Leaf $dest) no coincide (esperada $expected, obtenida $hash). Se ha borrado el archivo."
        }
        Ok "Descargado y verificado: $(Split-Path -Leaf $dest)"
    } else {
        Aviso "Descargado sin suma de verificación publicada: $(Split-Path -Leaf $dest) (SHA-256 $hash)"
    }
}

Write-Host "Note Cascade Studio - instalación" -ForegroundColor White
Write-Host "Carpeta: $Root"

# 1) uv ----------------------------------------------------------------------------
Paso "Gestor de Python (uv)"
$UvExe = Join-Path $Tools "uv\uv.exe"
if (-not (Test-Path $UvExe)) {
    $rel = Get-Release "astral-sh/uv"
    $asset = $rel.assets | Where-Object { $_.name -eq "uv-x86_64-pc-windows-msvc.zip" }
    $zip = Join-Path $Downloads $asset.name
    Get-Verified $asset.browser_download_url $zip $asset.digest
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Tools "uv") -Force
}
& $UvExe --version
Ok "uv listo"

# 2) Python 3.12 y entorno virtual -------------------------------------------------
Paso "Python 3.12 y dependencias (puede tardar varios minutos la primera vez)"
& $UvExe python install 3.12 --no-bin
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar Python 3.12." }
$Venv = Join-Path $Root ".venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    & $UvExe venv --python 3.12 $Venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual." }
}
$Py = Join-Path $Venv "Scripts\python.exe"
$Req = Join-Path $Root "requirements-lock.txt"
if (-not (Test-Path $Req)) { $Req = Join-Path $Root "requirements.txt" }
& $UvExe pip install --python $Py -r $Req
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de Python." }
Ok "Dependencias instaladas"

# 3) Modelos de HOMR ---------------------------------------------------------------
if (-not $SinModelos) {
    Paso "Modelos de HOMR (≈ 300 MB, descarga única desde github.com/liebharc/homr)"
    & $Py -W ignore -m homr.main --init
    if ($LASTEXITCODE -ne 0) { Aviso "No se pudieron descargar los modelos de HOMR. Podrás hacerlo desde Diagnóstico." }
    else { Ok "Modelos de HOMR listos" }
}

# 4) Audiveris ---------------------------------------------------------------------
if (-not $SinAudiveris) {
    Paso "Audiveris (motor OMR para partituras impresas, AGPL-3.0)"
    $AudExe = Join-Path $Tools "audiveris\Audiveris\Audiveris.exe"
    if (-not (Test-Path $AudExe)) {
        $rel = Get-Release "Audiveris/audiveris"
        $asset = $rel.assets | Where-Object { $_.name -like "*windowsConsole-x86_64.msi" } | Select-Object -First 1
        if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -like "*windows*x86_64.msi" } | Select-Object -First 1 }
        if (-not $asset) { throw "No se encontró el instalador de Audiveris para Windows en la última versión." }
        Write-Host "    Versión $($rel.tag_name): $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)"
        $msi = Join-Path $Downloads $asset.name
        Get-Verified $asset.browser_download_url $msi $asset.digest
        $target = Join-Path $Tools "audiveris"
        New-Item -ItemType Directory -Force $target | Out-Null
        # Extracción administrativa: no instala nada en el sistema ni pide permisos. Se extrae primero en
        # una carpeta temporal de ruta corta (msiexec falla con rutas de más de 260 caracteres) y luego se
        # mueve con robocopy, que admite rutas largas.
        $tmpTarget = Join-Path $env:TEMP ("ncs_aud_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force $tmpTarget | Out-Null
        $msiLog = Join-Path $Downloads "audiveris_msi.log"
        $p = Start-Process msiexec.exe -ArgumentList @("/a", "`"$msi`"", "/qn", "TARGETDIR=`"$tmpTarget`"",
                                                       "/L*v", "`"$msiLog`"") -Wait -PassThru
        if ($p.ExitCode -ne 0) {
            Remove-Item $tmpTarget -Recurse -Force -ErrorAction SilentlyContinue
            throw "No se pudo extraer Audiveris (código $($p.ExitCode)). Registro: $msiLog"
        }
        robocopy $tmpTarget $target /E /MOVE /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "No se pudo copiar Audiveris a $target (robocopy $LASTEXITCODE)." }
        Remove-Item $tmpTarget -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem $target -Filter *.msi | Remove-Item -Force
        Remove-Item $msi -Force -ErrorAction SilentlyContinue   # el instalador ya no hace falta
    }
    if (Test-Path $AudExe) { Ok "Audiveris en $AudExe" } else { Aviso "Audiveris no quedó instalado." }

    Paso "Idiomas de OCR para Audiveris (tessdata, commit fijado)"
    $TessCommit = "ced78752cc61322fb554c280d13360b35b8684e4"
    $Tess = Join-Path $Tools "tessdata"
    New-Item -ItemType Directory -Force $Tess | Out-Null
    $langs = @{
        "eng" = "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047";
        "ita" = "4f7476c611312beb8f8e182888da08ea642d9824ae4402cc6235f61ab1406406";
        "deu" = "896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4";
        "spa" = "0b0fcbb4665189e01ab8019e591f014dd7260460de072543edd4b2cb4ede7c96";
        "fra" = "eac01c1d72540d6090facb7b2f42dd0a2ee8fc57c5be1b20548ae668e2761913"
    }
    foreach ($l in $langs.Keys) {
        $dest = Join-Path $Tess "$l.traineddata"
        if (-not (Test-Path $dest)) {
            Get-Verified "https://github.com/tesseract-ocr/tessdata/raw/$TessCommit/$l.traineddata" $dest $langs[$l]
        }
    }
    Ok "OCR listo"
}

# 5) Diagnóstico -------------------------------------------------------------------
Paso "Diagnóstico"
& $Py -W ignore -m note_cascade --diagnostico

# 6) Acceso directo ----------------------------------------------------------------
if ($AccesoDirecto) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "Note Cascade Studio.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"
    $sc.Arguments = "-m note_cascade"
    $sc.WorkingDirectory = $Root
    $icon = Join-Path $Root "note_cascade\resources\icon.ico"
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Save()
    Ok "Acceso directo creado en el escritorio"
}

Write-Host ""
Write-Host "Instalación terminada. Abre la aplicación con run_windows.bat" -ForegroundColor Green
