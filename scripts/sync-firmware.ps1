$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$build=Join-Path $root 'firmware\.pio\build\esp32dev'
$target=Join-Path $root 'desktop\resources\firmware'
$boot=Join-Path $env:USERPROFILE '.platformio\packages\framework-arduinoespressif32\tools\partitions\boot_app0.bin'
foreach($file in @('bootloader.bin','partitions.bin','firmware.bin')){
 if(!(Test-Path -LiteralPath (Join-Path $build $file))){throw "Missing firmware build output: $file"}
}
if(!(Test-Path -LiteralPath $boot)){throw 'PlatformIO boot_app0.bin not found'}
New-Item -ItemType Directory -Path $target -Force | Out-Null
foreach($file in @('bootloader.bin','partitions.bin','firmware.bin')){Copy-Item -LiteralPath (Join-Path $build $file) -Destination (Join-Path $target $file)}
Copy-Item -LiteralPath $boot -Destination (Join-Path $target 'boot_app0.bin')
$manifest=[ordered]@{}
foreach($file in @('bootloader.bin','partitions.bin','boot_app0.bin','firmware.bin')){$manifest[$file]=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $target $file)).Hash.ToLowerInvariant()}
[System.IO.File]::WriteAllText((Join-Path $target 'manifest.json'),($manifest | ConvertTo-Json),(New-Object System.Text.UTF8Encoding($false)))
