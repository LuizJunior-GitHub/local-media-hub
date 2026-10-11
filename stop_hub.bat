@echo off
echo ========================================================
echo Encerrando Local Media Hub e Motor ComfyUI...
echo ========================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*ComfyUI*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Processo encerrado: PID ' + $_.ProcessId) }"
echo.
echo Servicos encerrados com sucesso.
timeout /t 2 >nul
