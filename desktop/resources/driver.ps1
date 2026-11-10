param([ValidateSet('install','remove')][string]$Mode='install')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$stateDir=Join-Path $env:ProgramData 'ESP32Audio'
$stateFile=Join-Path $stateDir 'driver.json'
function Find-Cable {
    @(Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName -eq 'VB-Audio Virtual Cable' -or ($_.DeviceName -match '^VB-Audio.*Cable' -and $_.DriverProviderName -match 'VB-Audio') })
}
function Write-State($record){
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    & icacls.exe $stateDir '/inheritance:r' '/grant:r' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Could not protect driver ownership record'}
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
}
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if(-not $admin){
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode {1}' -f $PSCommandPath,$Mode
    $child=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments -Wait -PassThru
    exit $child.ExitCode
}
try {
$setup=Join-Path $PSScriptRoot 'vbcable\VBCABLE_Setup_x64.exe'
$signature=Get-AuthenticodeSignature -LiteralPath $setup
if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Vincent|VB-Audio|Burel'){throw 'VB-CABLE installer signature validation failed'}
$before=@(Find-Cable)
if((Test-Path -LiteralPath $stateFile) -and $before.Count -gt 0){
    $pendingState=Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    if($pendingState.owner -eq 'ESP32Audio' -and $pendingState.pending){
        foreach($device in $before){
            $infPath=Join-Path $env:windir ('INF\'+$device.InfName)
            if(-not(Test-Path -LiteralPath $infPath) -or (Get-FileHash -LiteralPath $infPath -Algorithm SHA256).Hash -ne $pendingState.infHash){throw 'Pending installation identity differs; preserving driver'}
        }
        Write-State @{owner='ESP32Audio';pending=$false;devices=@($before | ForEach-Object { @{id=$_.DeviceID;inf=$_.InfName;provider=$_.DriverProviderName} })}
    }
}
if($Mode -eq 'install'){
    if($before.Count -gt 0){Write-Output 'Existing VB-CABLE preserved. No ownership claimed.';exit 0}
    $infHash=(Get-FileHash -LiteralPath (Join-Path (Split-Path $setup) 'vbMmeCable64_win10.inf') -Algorithm SHA256).Hash
    Write-State @{owner='ESP32Audio';pending=$true;infHash=$infHash;installedAt=(Get-Date).ToString('o');devices=@()}
    $process=Start-Process -FilePath $setup -ArgumentList '-i','-h' -WorkingDirectory (Split-Path $setup) -WindowStyle Hidden -Wait -PassThru
    $after=@()
    for($attempt=0;$attempt -lt 15;$attempt++){
        Start-Sleep -Seconds 2
        $after=@(Find-Cable)
        if($after.Count -gt 0){break}
    }
    if($after.Count -eq 0){throw "VB-CABLE was not detected after setup (exit $($process.ExitCode)). A restart may be required."}
    Write-State @{owner='ESP32Audio';pending=$false;installedAt=(Get-Date).ToString('o');devices=@($after | ForEach-Object { @{id=$_.DeviceID;inf=$_.InfName;provider=$_.DriverProviderName} })}
    Write-Output 'Installed VB-CABLE. Windows may require a restart.'
}else{
    if(-not(Test-Path -LiteralPath $stateFile)){Write-Output 'No app-owned driver. Existing audio devices preserved.';exit 0}
    $state=Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    if($state.owner -ne 'ESP32Audio'){throw 'Invalid driver ownership record'}
    if($state.pending){throw 'Installation is pending. Restart Windows and retry before removing this application.'}
    if($state.devices.Count -eq 0){throw 'No owned device identity was recorded; preserving drivers'}
    foreach($record in $state.devices){
        $current=@($before | Where-Object { $_.DeviceID -eq $record.id -and $_.InfName -eq $record.inf -and $_.DriverProviderName -eq $record.provider })
        if($current.Count -eq 0){throw 'Driver identity changed. Refusing to remove a replaced or shared driver.'}
    }
    # VB-CABLE is a shared endpoint: ask explicitly before removing our installation.
    Add-Type -AssemblyName System.Windows.Forms
    $answer=[System.Windows.Forms.MessageBox]::Show('移除此程式安裝的 VB-CABLE？若其他程式也正在使用它，請選擇「否」保留。','ESP32 Audio',[System.Windows.Forms.MessageBoxButtons]::YesNo)
    if($answer -ne [System.Windows.Forms.DialogResult]::Yes){Write-Output 'Driver kept by user.';exit 0}
    $process=Start-Process -FilePath $setup -ArgumentList '-u','-h' -WorkingDirectory (Split-Path $setup) -WindowStyle Hidden -Wait -PassThru
    Start-Sleep -Seconds 2
    if((Find-Cable).Count -gt 0){throw "Removal pending or failed (exit $($process.ExitCode)); restart may be required. Ownership retained."}
    Remove-Item -LiteralPath $stateFile
    Write-Output 'Removed app-owned VB-CABLE; restart Windows if requested.'
}
}catch{
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    $_.Exception.ToString() | Set-Content -LiteralPath (Join-Path $stateDir 'last-error.txt') -Encoding UTF8
    Write-Error $_
    exit 1
}
