param([string]$Destination = "C:\besu")
$ErrorActionPreference = "Stop"
$Version = "26.5.0"
$ExpectedSha256 = "9ddbe9e94662459898ff7b3ff4439821eeeee3bc2ff961378604202fa7da09e6"
$Url = "https://github.com/besu-eth/besu/releases/download/$Version/besu-$Version.zip"
$Zip = Join-Path $env:TEMP "besu-$Version.zip"
Write-Host "Downloading Besu $Version ..."
Invoke-WebRequest -Uri $Url -OutFile $Zip
$Actual = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
if ($Actual -ne $ExpectedSha256) { throw "SHA256 mismatch. Expected $ExpectedSha256, got $Actual" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Expand-Archive -Path $Zip -DestinationPath $Destination -Force
$Home = Join-Path $Destination "besu-$Version"
Write-Host "Installed: $Home"
Write-Host "For this CMD session run:"
Write-Host "  set BESU_HOME=$Home"
Write-Host "Then verify:"
Write-Host "  %BESU_HOME%\bin\besu.bat --version"
