$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSCommandPath
$bumbleRoot=& python -c "import importlib.util,pathlib; print(pathlib.Path(importlib.util.find_spec('bumble').origin).parent.parent)"
if($LASTEXITCODE -ne 0){throw 'Bumble must be installed before packaging'}
$arguments=@('-m','PyInstaller','--noconfirm','--noupx','--onedir','--name','esp32-audio-engine','--paths',$bumbleRoot,'--distpath',(Join-Path $project 'backend-dist'),'--workpath',(Join-Path $project 'build\python'),'--specpath',(Join-Path $project 'build'),'--add-binary',((Join-Path $project 'backend\ldacBT_enc.dll')+';.'),'--add-data',((Join-Path $project 'backend\device.json')+';.'),'--collect-submodules','bumble','--collect-submodules','esptool','--collect-all','soxr','--collect-all','pyaudiowpatch','--collect-all','av',(Join-Path $project 'backend\engine.py'))
& python @arguments
if($LASTEXITCODE -ne 0){throw 'Audio engine packaging failed'}
