# Old Bird Copilot release build (developer machine only).
#   powershell -ExecutionPolicy Bypass -File build.ps1
# Produces release\OldBirdCopilot-Setup-<version>.exe (installer) and release\OldBirdCopilot-<version>-portable.zip.
# Needs: the .venv (Python 3.13 + requirements), Inno Setup 6. See docs\DEVELOPING.md.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
$version = & $python -c "import landing_callouts; print(landing_callouts.__version__)"
Write-Host "== Old Bird Copilot $version"

Write-Host "== Tests"
& $python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

Write-Host "== Exe (PyInstaller)"
$pyBase = & $python -c "import sys; print(sys.base_prefix)"
$env:TCL_LIBRARY = Join-Path $pyBase "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $pyBase "tcl\tk8.6"
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name OldBirdCopilot `
    --icon assets\icon.ico --collect-all SimConnect --hidden-import win32com.client `
    --collect-data kokoro_onnx --collect-submodules phonemizer --hidden-import espeakng_loader `
    --add-data "landing_callouts/data;landing_callouts/data" --paths . landing_callouts\__main__.py --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "== Check: the exe reads its built-in airport list"
$check = Join-Path $env:TEMP "obc-airport-check.csv"
& $python -c "from landing_callouts.recorder import COLUMNS; import csv, sys; r = [''] * len(COLUMNS); r[0] = '00:00:00.0'; r[1] = '1'; r[-1] = 'destination: PASN'; w = csv.writer(open(sys.argv[1], 'w', newline='')); w.writerow(COLUMNS); w.writerow(r)" $check
$checkCfg = Join-Path $env:TEMP "obc-check-config.toml"   # its own settings file: never touch the user's
& $python -c "from landing_callouts import config; import sys; config.save(config.Config(), sys.argv[1])" $checkCfg
$p = Start-Process dist\OldBirdCopilot.exe -ArgumentList "--config", "`"$checkCfg`"", "--replay", "`"$check`"" -PassThru -Wait
if ($p.ExitCode -ne 0) { throw "Airport check failed (exit $($p.ExitCode))" }

Write-Host "== Installer (Inno Setup)"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
New-Item -ItemType Directory -Force release | Out-Null
& $iscc /Q "/DAppVersion=$version" installer\OldBirdCopilot.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host "== Portable zip"
$stage = Join-Path $env:TEMP "obc-portable\Old Bird Copilot"
if (Test-Path (Split-Path $stage)) { Remove-Item (Split-Path $stage) -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item dist\OldBirdCopilot.exe $stage
Copy-Item voice (Join-Path $stage "voice") -Recurse
Copy-Item "docs\Read Me First.txt" $stage
Copy-Item LICENSE (Join-Path $stage "LICENSE.txt")
# A config.toml next to the exe makes it a portable copy: settings and recordings stay in its own folder.
& $python -c "from landing_callouts import config; import sys; config.save(config.Config(), sys.argv[1])" (Join-Path $stage "config.toml")
Write-Host "== Check: the packaged exe can speak with Michael (silently)"
$p = Start-Process (Join-Path $stage "OldBirdCopilot.exe") -ArgumentList "--voice-check" -PassThru -Wait
if ($p.ExitCode -ne 0) { throw "Voice check failed (exit $($p.ExitCode))" }
$zip = "release\OldBirdCopilot-$version-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $stage -DestinationPath $zip

Get-ChildItem release | Where-Object { $_.Name -like "*$version*" } |
    ForEach-Object { "{0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB) }
