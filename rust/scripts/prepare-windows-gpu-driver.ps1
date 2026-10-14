# Keep the existing workflow entry point, but only CHECK the host driver.
# Driver installation/upgrades belong to the runner image, not a product CI job.
param([string]$Logs = "gpu-logs/driver")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    throw "nvidia-smi is missing. Select a GPU runner image with a working NVIDIA driver; CI will not install one."
}
[IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($Logs)) | Out-Null
$before = (& nvidia-smi 2>&1 | Out-String)
$status = $LASTEXITCODE
$before | Set-Content (Join-Path $Logs "before.txt")
Write-Host $before
if ($status -ne 0) {
    throw "nvidia-smi failed (exit $status). Fix the GPU runner image before retrying; CI will not install or upgrade its driver."
}
if ($before -notmatch "CUDA Version:\s+([0-9]+\.[0-9]+)") {
    throw "nvidia-smi did not report a CUDA driver capability. Verify the GPU runner image; CPU fallback is not a passing GPU test."
}
$supportedCuda = [version]$Matches[1]
# Preserve the suite's existing full CUDA 13.1 capability requirement. This is
# the driver's advertised capability, NOT the installed Toolkit version, and
# does not replace the real GGUF/ONNX CUDA execution checks.
if ($supportedCuda -lt [version]"13.1") {
    throw "The runner driver reports CUDA $supportedCuda; this suite requires CUDA 13.1 or newer driver capability. Select/update the runner image outside this job. CI will not download, upgrade, or reboot its driver."
}
Write-Host "GPU driver preflight passed (CUDA capability $supportedCuda); no driver changes made."
