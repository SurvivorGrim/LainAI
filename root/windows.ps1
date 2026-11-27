# Lain AI - start on Windows: right-click this file > Run with PowerShell
$py = Join-Path $PSScriptRoot 'LainAI\runtime\win\python.exe'
& $py -I -B -X utf8 (Join-Path $PSScriptRoot 'LainAI\app\lain.py')
if ($LASTEXITCODE) { Read-Host 'Press Enter to close' }
