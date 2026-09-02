# Setup Automatizado do Jarvis-Dev Host no Windows 10/11
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "   Instalador do Jarvis-Dev Windows Host & Real Computer Use " -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan

# 1. Habilitar OpenSSH Server
Write-Host "[1/5] Configurando OpenSSH Server..." -ForegroundColor Yellow
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue
Start-Service sshd -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType 'Automatic'

# 2. Liberar Firewall
Write-Host "[2/5] Configurando regras do Windows Firewall..." -ForegroundColor Yellow
New-NetFirewallRule -Name 'Jarvis-Dev-Port-8765' -DisplayName 'Jarvis-Dev Gateway (Port 8765)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 8765 -Profile Any -EdgeTraversalPolicy Allow -ErrorAction SilentlyContinue
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Any -ErrorAction SilentlyContinue

# 3. Instalar Dependencias Python
Write-Host "[3/5] Instalando dependencias Python..." -ForegroundColor Yellow
pip install -r requirements.txt
playwright install chromium

# 4. Registrar MCP de Computer Use no Antigravity CLI (AGY)
Write-Host "[4/5] Registrando Windows Computer Use MCP no AGY..." -ForegroundColor Yellow
agy mcp add windows-computer-use python "$PSScriptRoot\mcp_windows_computer_use.py"

Write-Host "`n✅ Jarvis-Dev Host instalado com sucesso!" -ForegroundColor Green
Write-Host "Para iniciar o host: python host_orchestrator.py" -ForegroundColor Cyan
