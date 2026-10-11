@echo off
cd /d "%~dp0"
start "" powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launcher.ps1"
exit
