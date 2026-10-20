# GOST stack: web + PostgreSQL, tile render cache on SSD (G:). No Surya/Ollama.
param(
    [switch]$NoBuild,
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

& "$PSScriptRoot\setup_ssd_cache.ps1"

if (-not (Test-Path "$Root\scan")) { New-Item -ItemType Directory -Path "$Root\scan" | Out-Null }
if (-not (Test-Path "$Root\data\tmp")) { New-Item -ItemType Directory -Path "$Root\data\tmp" -Force | Out-Null }

if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Host "Created .env — set PDF_STROIPROFI_LOGIN/PDF_STROIPROFI_PASSWORD for Стройпрофи checks."
}

$composeFiles = @(
    "-f", "docker-compose.yml",
    "-f", "docker-compose.ssd.yml",
    "-f", "docker-compose.fast.yml"
)

Write-Host "=== GOST stack (SSD cache) ===" -ForegroundColor Cyan
$upArgs = $composeFiles + @("up", "-d")
if ($Recreate) { $upArgs += "--force-recreate" }
if (-not $NoBuild) { $upArgs += "--build" }
docker compose @upArgs

$ssdRoot = if ($env:BELENER_SSD_ROOT) { $env:BELENER_SSD_ROOT } else { "G:/BelenerCache" }
Write-Host ""
Write-Host "=== Ready ===" -ForegroundColor Green
Write-Host "Web:   http://localhost:8090"
Write-Host "Cache: $ssdRoot (zone_render, tmp)"
Write-Host "Re-uploading the same PDF is faster (tiles cached on SSD)."
