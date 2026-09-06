$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
try {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $Dist = Join-Path $RepoRoot "dist/FantasyStore"
    $Exe = Join-Path $Dist "FantasyStore.exe"

    if (-not (Test-Path $Exe -PathType Leaf)) { throw "FantasyStore.exe is missing" }
    foreach ($required in @("_internal/ui/index.html", "_internal/ui/index-store-manager.html", "_internal/ui/css/app.css", "_internal/ui/js/app.js", "_internal/ui/js/store-manager-bridge-client.js", "_internal/resources/schema/pack-v1.json", "_internal/resources/schema/items-v1.json", "README.md")) {
        $candidate = Join-Path $Dist $required
        if (-not (Test-Path $candidate)) { throw "Required bundled resource is missing: $required" }
    }

    $forbiddenNames = @("tests", ".git", "__pycache__")
    foreach ($name in $forbiddenNames) {
        if (Get-ChildItem -Path $Dist -Recurse -Force | Where-Object { $_.Name -eq $name }) {
            throw "Development artifact leaked into distribution: $name"
        }
    }
    foreach ($pattern in @("*.db", "*.db-wal", "*.db-shm", "app.log", "*.vpack")) {
        if (Get-ChildItem -Path $Dist -Recurse -File -Filter $pattern) {
            throw "Persistent/sample user data leaked into distribution: $pattern"
        }
    }
    Write-Host "Distribution static verification PASS"
    exit 0
} catch {
    Write-Error $_
    exit 1
}
