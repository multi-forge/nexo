# Setup Automatizado do Nexo Host no Windows 10/11
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   Instalador do Nexo Windows Host & Architecture Stack     " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

$RepoRoot = Split-Path -Parent $PSScriptRoot

# 1. Habilitar OpenSSH Server
Write-Host "[1/5] Configurando OpenSSH Server..." -ForegroundColor Yellow
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue
Start-Service sshd -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType 'Automatic'

# 2. Liberar Firewall
Write-Host "[2/5] Configurando regras do Windows Firewall..." -ForegroundColor Yellow
New-NetFirewallRule -Name 'Nexo-Daemon-Port-3284' -DisplayName 'Nexo Daemon HTTP/P2P (Port 3284)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 3284 -Profile Any -EdgeTraversalPolicy Allow -ErrorAction SilentlyContinue
New-NetFirewallRule -Name 'Nexo-Gateway-Port-8765' -DisplayName 'Nexo Live Gateway (Port 8765)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 8765 -Profile Any -EdgeTraversalPolicy Allow -ErrorAction SilentlyContinue
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Any -ErrorAction SilentlyContinue

# 3. Compilar Daemon Rust Nativo
Write-Host "[3/5] Compilando Nexo Daemon em Rust..." -ForegroundColor Yellow
Set-Location "$RepoRoot\daemon"
cargo build --release
Set-Location $RepoRoot

# 4. Instalar Dependencias Python do Orquestrador
Write-Host "[4/5] Instalando dependencias do orquestrador Python..." -ForegroundColor Yellow
pip install -r "$RepoRoot\orchestrator\requirements.txt"

# 5. Registrar MCP de Computer Use no Antigravity CLI (AGY)
Write-Host "[5/5] Registrando Windows Computer Use MCP no AGY..." -ForegroundColor Yellow
agy mcp add windows-computer-use python "$RepoRoot\mcp\windows_computer_use.py"

Write-Host "`nNexo Host provisionado com sucesso!" -ForegroundColor Green
Write-Host "Para iniciar o daemon Rust: .\daemon\target\release\nexo-daemon.exe run" -ForegroundColor Cyan
Write-Host "Para iniciar o orquestrador de voz: python .\orchestrator\voice_queue.py" -ForegroundColor Cyan
