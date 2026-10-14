# Only for the ephemeral, manually triggered hosted GPU CI VM, not user machines.
param([string]$Logs = "gpu-logs/driver")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0
[IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($Logs)) | Out-Null
$before = (& nvidia-smi 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { throw "The hosted runner has no working NVIDIA driver" }
$before | Set-Content (Join-Path $Logs "before.txt")
Write-Host $before
if ($before -match "CUDA Version:\s+([0-9]+\.[0-9]+)" -and [version]$Matches[1] -ge [version]"13.1") {
    Write-Host "The existing driver supports both tested CUDA families"
    exit 0
}

# Microsoft/Azure's signed Tesla T4 driver, also supporting Server 2022/2025.
# https://www.microsoft.com/en-us/download/details.aspx?id=108602
$installer = Join-Path $env:RUNNER_TEMP "nvidia-591.59.exe"
$url = "https://download.microsoft.com/download/8920fc6c-3546-458b-99d9-647c4e8621a4/591.59-data-center-tesla-desktop-win10-win11-64bit-dch-international.exe"
& curl.exe -fL --retry 3 --connect-timeout 20 --max-time 600 -o $installer $url
if ($LASTEXITCODE -ne 0) { throw "Downloading the hosted GPU driver failed" }
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "NVIDIA Corporation") {
    throw "The GPU driver is not authentically signed by NVIDIA"
}
$expanded = Join-Path $env:RUNNER_TEMP "nvidia-591.59"
& 7z x $installer "-o$expanded" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Extracting the GPU driver failed" }
$setup = Join-Path $expanded "setup.exe"
$logPath = [IO.Path]::GetFullPath($Logs)
$process = Start-Process -FilePath $setup -ArgumentList @("-s", "-n", "Display.Driver", "-log:`"$logPath`"", "-loglevel:6") -Wait -PassThru
$process.ExitCode | Set-Content (Join-Path $Logs "installer-exit-code.txt")
if ($process.ExitCode -eq 1) {
    throw "Driver installation requires a reboot. Use a hosted image with an updated driver; this job will not reboot the runner."
}
if ($process.ExitCode -ne 0) { throw "GPU driver setup failed with exit code $($process.ExitCode)" }
$after = (& nvidia-smi 2>&1 | Out-String)
$after | Set-Content (Join-Path $Logs "after.txt")
Write-Host $after
if ($LASTEXITCODE -ne 0 -or $after -notmatch "CUDA Version:\s+([0-9]+\.[0-9]+)" -or [version]$Matches[1] -lt [version]"13.1") {
    throw "The installed driver is not active with CUDA 13.1 support; a newer hosted image is required"
}
