$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSCommandPath
$arguments=@('-m','PyInstaller','--noconfirm','--clean','--onedir','--name','esp32-audio-engine','--distpath',(Join-Path $project 'backend-dist'),'--workpath',(Join-Path $env:LOCALAPPDATA 'Temp\esp32-audio-pybuild'),'--specpath',(Join-Path $project 'build'),'--add-binary',((Join-Path $project 'backend\ldacBT_enc.dll')+';.'),'--add-data',((Join-Path $project 'backend\device.json')+';.'),'--collect-submodules','bumble','--collect-submodules','esptool','--collect-all','soxr','--collect-all','pyaudiowpatch',(Join-Path $project 'backend\engine.py'))
& python @arguments
if($LASTEXITCODE -ne 0){throw 'Audio engine packaging failed'}
