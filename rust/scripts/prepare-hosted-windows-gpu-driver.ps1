# Opt-in exception for a manually dispatched, disposable GitHub-hosted Windows VM.
# The ordinary prepare-windows-gpu-driver.ps1 remains strictly check-only.
param([switch]$AllowInstallation, [string]$Logs = "gpu-logs/driver")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

if (-not $AllowInstallation -or $env:GITHUB_ACTIONS -ne "true" -or
    $env:RUNNER_ENVIRONMENT -ne "github-hosted" -or $env:RUNNER_OS -ne "Windows" -or
    $env:GITHUB_EVENT_NAME -ne "workflow_dispatch") {
    throw "Driver preparation requires explicit opt-in on a manually dispatched GitHub-hosted Windows runner."
}
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    throw "nvidia-smi is missing; refusing to modify an unrecognized GPU environment."
}
[IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($Logs)) | Out-Null
$before = (& nvidia-smi 2>&1 | Out-String)
$status = $LASTEXITCODE
$before | Set-Content (Join-Path $Logs "before.txt")
Write-Host $before
if ($status -ne 0) { throw "nvidia-smi failed (exit $status); refusing to change a broken GPU environment." }
if ($before -notmatch "CUDA Version:\s+([0-9]+\.[0-9]+)") {
    throw "The existing driver did not report a CUDA capability."
}
if ([version]$Matches[1] -ge [version]"13.1") {
    Write-Host "The existing driver supports the suite; no download or installation needed."
    return
}

# Fixed, previously validated Microsoft/Azure Tesla T4 package. Not latest.
# https://www.microsoft.com/en-us/download/details.aspx?id=108602
$version = "591.59"
$url = "https://download.microsoft.com/download/8920fc6c-3546-458b-99d9-647c4e8621a4/591.59-data-center-tesla-desktop-win10-win11-64bit-dch-international.exe"
if (-not $env:RUNNER_TEMP) { throw "RUNNER_TEMP is missing." }
$installer = Join-Path $env:RUNNER_TEMP "nvidia-$version.exe"
& curl.exe -fL --retry 3 --connect-timeout 20 --max-time 600 -o $installer $url
if ($LASTEXITCODE -ne 0) { throw "Downloading the fixed GPU driver failed." }
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "NVIDIA Corporation") {
    throw "The GPU driver does not have a valid NVIDIA signature."
}
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
@{ version = $version; url = $url; sha256 = $hash; signer = $signature.SignerCertificate.Subject } |
    ConvertTo-Json | Set-Content (Join-Path $Logs "installer.json")

$expanded = Join-Path $env:RUNNER_TEMP "nvidia-$version"
& 7z x $installer "-o$expanded" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Extracting the GPU driver failed." }
$setup = Join-Path $expanded "setup.exe"
$logPath = [IO.Path]::GetFullPath($Logs)
# -n prevents an installer reboot. Install the display driver component only.
$process = Start-Process -FilePath $setup -ArgumentList @("-s", "-n", "Display.Driver", "-log:`"$logPath`"", "-loglevel:6") -Wait -PassThru
$process.ExitCode | Set-Content (Join-Path $Logs "installer-exit-code.txt")
if ($process.ExitCode -in @(1, 3010, 1641)) {
    throw "Driver installation requested a reboot; this job will not reboot or continue testing."
}
if ($process.ExitCode -ne 0) { throw "GPU driver setup failed with exit code $($process.ExitCode)." }
$after = (& nvidia-smi 2>&1 | Out-String)
$status = $LASTEXITCODE
$after | Set-Content (Join-Path $Logs "after.txt")
Write-Host $after
if ($status -ne 0 -or $after -notmatch "CUDA Version:\s+([0-9]+\.[0-9]+)" -or [version]$Matches[1] -lt [version]"13.1") {
    throw "The installed driver is not active with CUDA 13.1 support."
}
if ($after -notmatch ("Driver Version:\s+" + [regex]::Escape($version) + "\b")) {
    throw "The expected driver version $version is not active."
}
Write-Host "Fixed GPU driver $version is active; real CUDA CLI tests must still pass."
