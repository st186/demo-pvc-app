<#
Builds a self-contained PVC Studio folder: a private copy of Python, every
package, the background-removal model and the launcher. Nothing is installed
system-wide and no admin rights are needed.

Used by install.bat (installs on this PC) and by the GitHub Actions build that
packages the folder into PVC-Studio-Setup.exe.

  powershell -ExecutionPolicy Bypass -File build_env.ps1 -Source <repo> -Target <folder> [-Shortcuts]
#>
param(
    [Parameter(Mandatory)] [string] $Source,
    [Parameter(Mandatory)] [string] $Target,
    [string] $PythonVersion = "3.12.10",
    [switch] $Shortcuts,      # create Desktop and Start menu shortcuts (install.bat)
    [switch] $NoModel         # skip pre-downloading the background-removal model
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is much faster without it
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Download($url, $out) {
    for ($i = 1; $i -le 4; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; return }
        catch { if ($i -eq 4) { throw }; Write-Host "  retry $i ($($_.Exception.Message))"; Start-Sleep -Seconds (2 * $i) }
    }
}
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }

$Source = (Resolve-Path $Source).Path
New-Item -ItemType Directory -Force -Path $Target | Out-Null
$Target = (Resolve-Path $Target).Path
# A running PVC Studio locks its files: stop it (only processes from this folder).
Get-Process pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith("$Target\", [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
$py = Join-Path $Target "python"
$python = Join-Path $py "python.exe"
$tmp = Join-Path $env:TEMP "pvc-studio-build"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

if (-not (Test-Path $python)) {
    Step "Downloading Python $PythonVersion (embeddable, private to PVC Studio)"
    $zip = Join-Path $tmp "python-embed.zip"
    Download "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $zip
    Expand-Archive -Path $zip -DestinationPath $py -Force

    # Let the embedded interpreter use site-packages (pip installs there).
    $tag = ($PythonVersion.Split(".")[0..1] -join "")
    Set-Content -Path (Join-Path $py "python$tag._pth") -Encoding ASCII -Value @(
        "python$tag.zip", ".", "Lib\site-packages", "import site")

    Step "Installing pip"
    $getpip = Join-Path $tmp "get-pip.py"
    Download "https://bootstrap.pypa.io/get-pip.py" $getpip
    & $python $getpip --no-warn-script-location --disable-pip-version-check
    Check "pip install"
}

Step "Installing PVC Studio and its packages (this is the big download)"
# The embeddable Python ignores PYTHONPATH, so pip's isolated build env can't
# see setuptools: install the build tools here and build without isolation.
& $python -m pip install --no-warn-script-location --disable-pip-version-check --upgrade setuptools wheel
Check "build tools install"
# DirectML lets background removal use any Windows GPU; it falls back to the CPU.
& $python -m pip install --no-warn-script-location --disable-pip-version-check --upgrade --no-build-isolation "$Source[web,photo-gpu]"
Check "PVC Studio install"

if (-not $NoModel) {
    Step "Downloading the background-removal model (~176 MB)"
    $env:U2NET_HOME = Join-Path $Target "models"
    & $python -c "from pvccard import passport; passport.warm_up(); print('model ready:', passport.status('fast'))"
    Check "model download"
}

Step "Finishing"
Copy-Item (Join-Path $Source "windows\pvc.ico") (Join-Path $Target "pvc.ico") -Force
Copy-Item (Join-Path $Source "windows\uninstall.bat") (Join-Path $Target "uninstall.bat") -Force
# Marks a packaged install: the launcher then keeps models and logs beside it.
Set-Content -Path (Join-Path $Target "pvc-studio.portable") -Value "PVC Studio $PythonVersion"
& $python -m pip cache purge 2>$null | Out-Null

if ($Shortcuts) {
    Step "Creating Desktop and Start menu shortcuts"
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
    foreach ($dir in $places) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "PVC Studio.lnk"))
        $lnk.TargetPath = Join-Path $py "pythonw.exe"
        $lnk.Arguments = "-m pvccard.desktop"
        $lnk.WorkingDirectory = $Target
        $lnk.IconLocation = Join-Path $Target "pvc.ico"
        $lnk.Description = "PVC Studio - PVC cards and passport photos"
        $lnk.Save()
    }
}

Write-Host "`nPVC Studio is ready in $Target" -ForegroundColor Green
