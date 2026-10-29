# Instalador do Nexus Agente __NEXUS_VERSAO__ (Windows)
param([string]$Chave = '', [string]$Unidade = '')
$ErrorActionPreference = 'Stop'
$Servidor = '__NEXUS_URL__'
$Versao = '__NEXUS_VERSAO__'
$Tarefa = 'Nexus Agente'
$Destino = Join-Path $env:ProgramData 'NexusAgente'
$Origem = Split-Path -Parent $MyInvocation.MyCommand.Path
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

function Titulo($t) { Write-Host ''; Write-Host "  $t" -ForegroundColor Cyan }
function Falhar($m) { Write-Host ''; Write-Host "  ERRO: $m" -ForegroundColor Red; Write-Host ''; exit 1 }

function Chamar-Ativacao($corpo) {
    $json = $corpo | ConvertTo-Json -Compress
    try {
        return Invoke-RestMethod -Uri ($Servidor.TrimEnd('/') + '/api/agente/ativar') -Method Post `
            -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json)) `
            -TimeoutSec 30 -UseBasicParsing
    } catch {
        $msg = $null
        try { $msg = ($_.ErrorDetails.Message | ConvertFrom-Json).erro } catch { }
        if (-not $msg) { $msg = "Não foi possível falar com o Nexus em $Servidor ($($_.Exception.Message)). Verifique a internet deste computador." }
        Falhar $msg
    }
}

function Obter-IpLocal {
    try { $u = New-Object Net.Sockets.UdpClient; $u.Connect('8.8.8.8', 53); $ip = $u.Client.LocalEndPoint.Address.ToString(); $u.Close(); return $ip } catch { return '' }
}

Write-Host ''
Write-Host '  ==============================================' -ForegroundColor DarkYellow
Write-Host "   Nexus Agente $Versao - monitoramento local" -ForegroundColor White
Write-Host '  ==============================================' -ForegroundColor DarkYellow
Write-Host "   Servidor: $Servidor"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Falhar 'Rode o Instalar.bat (ele pede permissão de administrador).' }

Titulo 'Chave de ativação'
Write-Host '   Está no cadastro do cliente: Nexus Desk > Administração > Clientes.'
while (-not $Chave) { $Chave = (Read-Host '   Digite a chave (ex.: NXA-XXXX-XXXX-XXXX-XXXX)').Trim() }

try { $so = (Get-CimInstance Win32_OperatingSystem).Caption } catch { $so = [Environment]::OSVersion.VersionString }
$Maquina = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { [Environment]::MachineName }
$base = @{ chave = $Chave; hostname = $Maquina; so = $so; versao = $Versao; ip_local = (Obter-IpLocal) }
$resp = Chamar-Ativacao $base

if ($resp.unidades) {
    Titulo "Cliente: $($resp.cliente)"
    Write-Host '   Em qual unidade este computador está?'
    $lista = @($resp.unidades)
    for ($i = 0; $i -lt $lista.Count; $i++) { Write-Host ('     {0,2}) {1}' -f ($i + 1), $lista[$i].nome) }
    $escolha = 0
    if ($Unidade) { $escolha = [int]$Unidade }
    while ($escolha -lt 1 -or $escolha -gt $lista.Count) {
        [int]::TryParse((Read-Host '   Número da unidade'), [ref]$escolha) | Out-Null
    }
    $base.unidade_id = [int]$lista[$escolha - 1].id
    $resp = Chamar-Ativacao $base
}
if (-not $resp.token) { Falhar 'O Nexus não devolveu o token de ativação.' }

Titulo 'Instalando'
if (Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false
}
New-Item -ItemType Directory -Path $Destino -Force | Out-Null
Copy-Item (Join-Path $Origem 'NexusAgente.ps1') (Join-Path $Destino 'NexusAgente.ps1') -Force
Unblock-File (Join-Path $Destino 'NexusAgente.ps1') -ErrorAction SilentlyContinue
$cfg = @{ servidor = $Servidor; token = $resp.token; agente_id = $resp.agente_id; cliente = $resp.cliente; unidade = $resp.unidade }
[IO.File]::WriteAllText((Join-Path $Destino 'config.json'), ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
# só SYSTEM e Administradores acessam a pasta (o token fica nela)
& icacls.exe $Destino /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null

$acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Destino\NexusAgente.ps1`""
$gatilho = New-ScheduledTaskTrigger -AtStartup
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$quem = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger $gatilho -Settings $ajustes -Principal $quem `
    -Description 'Nexus Desk - monitoramento local dos equipamentos (TecnosIT)' | Out-Null
Start-ScheduledTask -TaskName $Tarefa

Write-Host ''
Write-Host '  Pronto! Agente instalado e rodando.' -ForegroundColor Green
Write-Host "   Cliente: $($resp.cliente)"
Write-Host "   Unidade: $($resp.unidade)"
Write-Host "   Ele inicia sozinho com o Windows. Log: $Destino\agente.log"
Write-Host '   No Nexus: Administração > Agentes mostra o status.'
Write-Host ''
