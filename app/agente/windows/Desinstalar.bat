@echo off
rem Nexus Agente - desinstalar
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-ScheduledTask -TaskName 'Nexus Agente' -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'Nexus Agente' -Confirm:$false -ErrorAction SilentlyContinue; Remove-Item (Join-Path $env:ProgramData 'NexusAgente') -Recurse -Force -ErrorAction SilentlyContinue; Write-Host 'Nexus Agente removido.'"
echo.
pause
