param(
  [switch]$AlsoDeleteStoredFiles
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendArtifacts = Join-Path $repoRoot "artifacts"

function Remove-IfExists([string]$path) {
  if (Test-Path -LiteralPath $path) {
    Remove-Item -LiteralPath $path -Force -Recurse
  }
}

Remove-IfExists (Join-Path $backendArtifacts "soterra-demo.sqlite3")
Remove-IfExists (Join-Path $backendArtifacts "playwright-e2e.sqlite3")
Remove-IfExists (Join-Path $backendArtifacts "playwright-e2e-storage")

if ($AlsoDeleteStoredFiles) {
  $storageDir = Join-Path $backendArtifacts "storage"
  if (Test-Path -LiteralPath $storageDir) {
    Get-ChildItem -LiteralPath $storageDir -Directory -Filter "rpt-*" -ErrorAction SilentlyContinue |
      ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -Recurse }
  }
}

Write-Host "Backend demo data reset complete."
