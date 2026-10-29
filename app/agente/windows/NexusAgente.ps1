# Nexus Agente __NEXUS_VERSAO__ - monitoramento local dos equipamentos (TecnosIT / Nexus Desk)
# Testa os equipamentos da unidade pela rede interna e envia os resultados ao Nexus.
# Só faz conexões de SAÍDA (HTTPS) para o servidor: nenhuma porta precisa ser liberada no roteador.
$ErrorActionPreference = 'Continue'
$Versao = '__NEXUS_VERSAO__'
$Pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArqConfig = Join-Path $Pasta 'config.json'
$ArqLog = Join-Path $Pasta 'agente.log'
$script:Pendentes = New-Object System.Collections.ArrayList
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

function Escrever-Log([string]$Nivel, [string]$Mensagem, [switch]$SoLocal) {
    $linha = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Nivel.ToUpper(), $Mensagem
    try {
        if ((Test-Path $ArqLog) -and ((Get-Item $ArqLog).Length -gt 2MB)) { Move-Item $ArqLog "$ArqLog.1" -Force }
        Add-Content -Path $ArqLog -Value $linha -Encoding UTF8
    } catch { }
    if (-not $SoLocal) {
        [void]$script:Pendentes.Add(@{ nivel = $Nivel; mensagem = $Mensagem; quando = (Get-Date).ToUniversalTime().ToString('o') })
        while ($script:Pendentes.Count -gt 100) { $script:Pendentes.RemoveAt(0) }
    }
}

function Obter-IpLocal {
    try {
        $u = New-Object Net.Sockets.UdpClient
        $u.Connect('8.8.8.8', 53)
        $ip = $u.Client.LocalEndPoint.Address.ToString()
        $u.Close()
        return $ip
    } catch { return '' }
}

function Obter-SO {
    try { return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption } catch { }
    return [Environment]::OSVersion.VersionString
}

function Traduzir-Erro($ex) {
    $e = $ex
    while ($e.InnerException) { $e = $e.InnerException }
    if ($e -is [Net.Sockets.SocketException]) {
        switch ($e.SocketErrorCode.ToString()) {
            'ConnectionRefused' { return 'Conexão recusada na porta' }
            'HostNotFound' { return 'Endereço não encontrado' }
            'TimedOut' { return 'Sem resposta (tempo esgotado)' }
            'HostUnreachable' { return 'Destino inacessível' }
            'NetworkUnreachable' { return 'Rede inacessível' }
        }
    }
    $m = [string]$e.Message
    if ($m.Length -gt 180) { $m = $m.Substring(0, 180) }
    return $m
}

function Testar-Todos($Equipamentos, [int]$TimeoutMs) {
    $lista = New-Object System.Collections.ArrayList
    $relogio = [Diagnostics.Stopwatch]::StartNew()
    $testes = @()
    foreach ($e in @($Equipamentos)) {
        $t = [pscustomobject]@{ eq = $e; tarefa = $null; obj = $null; erro = $null; porta = $e.porta }
        try {
            if ($e.porta) {
                $t.obj = New-Object Net.Sockets.TcpClient
                $t.tarefa = $t.obj.ConnectAsync([string]$e.host, [int]$e.porta)
            } else {
                $t.obj = New-Object Net.NetworkInformation.Ping
                $t.tarefa = $t.obj.SendPingAsync([string]$e.host, $TimeoutMs)
            }
        } catch { $t.erro = Traduzir-Erro $_.Exception }
        $testes += $t
    }
    # acompanha todos ao mesmo tempo, anotando quando cada um terminou
    $prazo = $TimeoutMs + 500
    $fim = @{}
    while ($relogio.ElapsedMilliseconds -lt $prazo) {
        $pendente = $false
        for ($i = 0; $i -lt $testes.Count; $i++) {
            $t = $testes[$i]
            if ($t.tarefa -and -not $fim.ContainsKey($i)) {
                if ($t.tarefa.IsCompleted) { $fim[$i] = [int]$relogio.ElapsedMilliseconds } else { $pendente = $true }
            }
        }
        if (-not $pendente) { break }
        Start-Sleep -Milliseconds 20
    }
    for ($i = 0; $i -lt $testes.Count; $i++) {
        $t = $testes[$i]
        $ok = $false; $ms = $null; $erro = $t.erro
        if ($t.tarefa) {
            if (-not $t.tarefa.IsCompleted) {
                if ($t.porta) { $erro = 'Sem resposta (tempo esgotado)' } else { $erro = 'Sem resposta ao ping' }
            } elseif ($t.tarefa.IsFaulted) {
                $erro = Traduzir-Erro $t.tarefa.Exception
            } elseif ($t.porta) {
                $ok = $t.obj.Connected
                if ($ok) { $ms = $fim[$i] } else { $erro = 'Sem conexão na porta' }
            } else {
                $r = $t.tarefa.Result
                if ($r.Status -eq [Net.NetworkInformation.IPStatus]::Success) { $ok = $true; $ms = [int]$r.RoundtripTime }
                elseif ($r.Status -eq [Net.NetworkInformation.IPStatus]::TimedOut) { $erro = 'Sem resposta ao ping' }
                else { $erro = 'Ping: ' + $r.Status.ToString() }
            }
        }
        try { $t.obj.Dispose() } catch { }
        [void]$lista.Add(@{ id = [int]$t.eq.id; ok = $ok; ms = $ms; erro = $erro })
    }
    return ,$lista
}

function Sincronizar($Config, $Resultados) {
    $corpo = @{
        versao = $Versao; hostname = $Maquina; so = $script:SO; ip_local = (Obter-IpLocal)
        resultados = @($Resultados | ForEach-Object { $_ }); logs = @($script:Pendentes.ToArray())
    }
    $json = $corpo | ConvertTo-Json -Depth 6 -Compress
    $resp = Invoke-RestMethod -Uri ($Config.servidor.TrimEnd('/') + '/api/agente/sincronizar') -Method Post `
        -Headers @{ Authorization = 'Bearer ' + $Config.token } -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 30 -UseBasicParsing
    $script:Pendentes.Clear()
    return $resp
}

# ------------------------------------------------------------------ início
if (-not (Test-Path $ArqConfig)) {
    Escrever-Log 'erro' "Arquivo de configuração não encontrado ($ArqConfig). Rode o instalador." -SoLocal
    exit 1
}
$config = Get-Content $ArqConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$script:SO = Obter-SO
$intervalo = 60; $timeout = 4000; $tarefas = $null; $qtd = -1; $falhasSeguidas = 0
$umaVez = $args -contains '-UmaVez'   # um ciclo só (diagnóstico)
$Maquina = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { [Environment]::MachineName }
Escrever-Log 'info' ("Agente iniciado (versão {0}) em {1}, servidor {2}" -f $Versao, $Maquina, $config.servidor)

while ($true) {
    $inicio = Get-Date
    $resultados = @(); $testou = $false
    if ($tarefas -and $tarefas.equipamentos) { $resultados = Testar-Todos $tarefas.equipamentos $timeout; $testou = $true }
    try {
        $resp = Sincronizar $config $resultados
        if ($falhasSeguidas -gt 0) { Escrever-Log 'info' "Conexão com o Nexus restabelecida após $falhasSeguidas tentativa(s)." }
        $falhasSeguidas = 0
        $primeira = ($null -eq $tarefas)
        $tarefas = $resp
        if ($resp.intervalo_seg) { $intervalo = [int]$resp.intervalo_seg }
        if ($resp.timeout_ms) { $timeout = [int]$resp.timeout_ms }
        $n = @($resp.equipamentos).Count
        if ($n -ne $qtd) { Escrever-Log 'info' "Monitorando $n equipamento(s) da unidade $($resp.unidade)."; $qtd = $n }
        if ($primeira -and $n -gt 0) { continue }   # primeira lista: testa já
    } catch {
        $falhasSeguidas++
        $status = $null
        try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        if ($status -eq 401) {
            Escrever-Log 'erro' 'Agente não autorizado pelo Nexus (desativado ou reinstalado em outro lugar). Tentando de novo em 5 min.' -SoLocal
            $intervalo = 300
        } elseif ($falhasSeguidas -le 3 -or ($falhasSeguidas % 30) -eq 0) {
            Escrever-Log 'aviso' ("Sem conexão com o Nexus (tentativa {0}): {1}" -f $falhasSeguidas, (Traduzir-Erro $_.Exception))
        }
    }
    if ($umaVez -and ($testou -or $null -eq $tarefas -or @($tarefas.equipamentos).Count -eq 0)) { break }
    $dorme = $intervalo - [int]((Get-Date) - $inicio).TotalSeconds
    if ($dorme -gt 0) { Start-Sleep -Seconds $dorme }
}
