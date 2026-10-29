@echo off
rem Mostra se o agente esta rodando e as ultimas linhas do log
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = Get-ScheduledTask -TaskName 'Nexus Agente' -ErrorAction SilentlyContinue; if ($t) { Write-Host ('Tarefa: ' + $t.State) } else { Write-Host 'Agente NAO instalado.' }; $l = Join-Path $env:ProgramData 'NexusAgente\agente.log'; if (Test-Path $l) { Write-Host ''; Get-Content $l -Tail 25 -Encoding UTF8 }"
echo.
pause
