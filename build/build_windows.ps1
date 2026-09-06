$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "FantasyStore Windows build must be executed on Windows; PyInstaller is not a cross-compiler."
}

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11 or later is required for the Windows build."
}
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
Assert-NativeSuccess "Python version guard"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required to run the Phase 6/7 JavaScript regression tests before building."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required to run the Phase 6/7 JavaScript regression tests before building."
}

Write-Host "[1/5] Clean prior build artifacts"
foreach ($path in @("build/work", "dist")) {
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}
New-Item -ItemType Directory -Force -Path "build/work" | Out-Null

Write-Host "[2/5] Install exact dependencies into the active build environment"
python -m pip install --disable-pip-version-check -r requirements.txt -r requirements-dev.txt -r requirements-build.txt
Assert-NativeSuccess "Dependency installation"

Write-Host "[3/5] Run Python + JavaScript regression tests"
python -m pytest
Assert-NativeSuccess "Python regression tests"
npm run test:ui
Assert-NativeSuccess "JavaScript regression tests"

Write-Host "[4/5] Build PyInstaller onedir candidate"
python -m PyInstaller --noconfirm --clean --workpath build/work build/fantasy_store.spec
Assert-NativeSuccess "PyInstaller build"
Copy-Item -Force "docs/RELEASE_README.md" "dist/FantasyStore/README.md"

Write-Host "[5/5] Verify distribution shape"
& (Join-Path $PSScriptRoot "verify_dist.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Distribution verification failed with exit code $LASTEXITCODE."
}
Write-Host "Build candidate ready at dist/FantasyStore. Gate B Windows RC validation is still required."
