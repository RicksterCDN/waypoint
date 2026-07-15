# Setup script for Caldova Waypoint
# Run: .\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "🚀 Setting up Caldova Waypoint..." -ForegroundColor Cyan

function Test-Command {
  param($Command, $InstallHint)
  if (!(Get-Command $Command -ErrorAction SilentlyContinue)) {
    Write-Host "❌ $Command is not installed. Please install it first." -ForegroundColor Red
    Write-Host "   $InstallHint" -ForegroundColor Yellow
    exit 1
  }
  Write-Host "✅ $Command found" -ForegroundColor Green
}

Write-Host ""
Write-Host "Checking prerequisites..."
Test-Command "dotnet" "Install from: https://dotnet.microsoft.com/download"
Test-Command "python" "Install from: https://www.python.org/"
Test-Command "node" "Install from: https://nodejs.org/"
Test-Command "uv" "Install with: irm https://astral.sh/uv/install.ps1 | iex"

# Check for Aspire CLI
if (!(Get-Command aspire -ErrorAction SilentlyContinue)) {
  Write-Host ""
  Write-Host "📦 Installing Aspire CLI..." -ForegroundColor Cyan
  Invoke-RestMethod https://aspire.dev/install.ps1 | Invoke-Expression
  $env:PATH = "$env:USERPROFILE\.aspire\bin;$env:PATH"
}
Write-Host "✅ Aspire CLI found" -ForegroundColor Green

# Install pre-commit hooks
Write-Host ""
Write-Host "🔧 Setting up pre-commit hooks..." -ForegroundColor Cyan
uv tool install pre-commit 2>$null
pre-commit install

Write-Host ""
Write-Host "✅ Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "To start the application, run:"
Write-Host "  aspire run" -ForegroundColor Yellow
Write-Host ""
Write-Host "Access points:"
Write-Host "  • Web App:          http://localhost:5173"
Write-Host "  • API Docs:         http://localhost:8000/docs"
Write-Host "  • Aspire Dashboard: http://localhost:15888"
