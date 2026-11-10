$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$source=Join-Path $root 'vendor\libldac'
$output=Join-Path $root 'desktop\backend\ldacBT_enc.dll'
& gcc -shared -O2 -I (Join-Path $source 'inc') -I (Join-Path $source 'src') (Join-Path $source 'src\ldaclib.c') (Join-Path $source 'src\ldacBT.c') -o $output -lm
if($LASTEXITCODE -ne 0){throw 'LDAC encoder build failed'}
Get-FileHash -Algorithm SHA256 -LiteralPath $output
