$wsh = New-Object -ComObject WScript.Shell
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $projectDir) {
    $projectDir = "D:\GitHub\local-media-hub"
}

$launcherPs1 = Join-Path $projectDir "launcher.ps1"
$iconPath = Join-Path $projectDir "media_hub.ico"

# 1. Atalho na pasta do projeto
$shortcutPath1 = Join-Path $projectDir "Local Media Hub.lnk"
$shortcut1 = $wsh.CreateShortcut($shortcutPath1)
$shortcut1.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut1.Arguments = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherPs1`""
$shortcut1.WorkingDirectory = $projectDir
$shortcut1.IconLocation = "$iconPath,0"
$shortcut1.Description = "Local Media Hub - Studio de Geração GPU"
$shortcut1.Save()

# 2. Atalho na Área de Trabalho (Desktop)
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath2 = Join-Path $desktopPath "Local Media Hub.lnk"
$shortcut2 = $wsh.CreateShortcut($shortcutPath2)
$shortcut2.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut2.Arguments = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherPs1`""
$shortcut2.WorkingDirectory = $projectDir
$shortcut2.IconLocation = "$iconPath,0"
$shortcut2.Description = "Local Media Hub - Studio de Geração GPU"
$shortcut2.Save()

Write-Host "✅ Atalhos criados com sucesso!" -ForegroundColor Green
Write-Host "   1. Pasta do projeto: $shortcutPath1"
Write-Host "   2. Área de Trabalho: $shortcutPath2"
