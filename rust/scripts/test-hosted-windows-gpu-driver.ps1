# Mock-only tests. Never download, install, or interact with a real GPU.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0
$prepare = Join-Path $PSScriptRoot "prepare-hosted-windows-gpu-driver.ps1"
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("zg-hosted-driver-test-" + [guid]::NewGuid())
$environment = @("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "RUNNER_OS", "GITHUB_EVENT_NAME", "RUNNER_TEMP")
$saved = @{}
foreach ($name in $environment) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }

function nvidia-smi {
    $state.Probes++
    $global:LASTEXITCODE = $case.ProbeExit
    if ($state.Probes -eq 1) { $case.Before } else { $case.After }
}
function curl.exe { $state.Downloads++; $global:LASTEXITCODE = $case.DownloadExit }
function Get-AuthenticodeSignature {
    [pscustomobject]@{ Status = $case.Signature; SignerCertificate = [pscustomobject]@{ Subject = "CN=NVIDIA Corporation" } }
}
function Get-FileHash { [pscustomobject]@{ Hash = ("0" * 64) } }
function 7z { $global:LASTEXITCODE = 0 }
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru)
    if ($ArgumentList -notcontains "-n" -or $ArgumentList -notcontains "Display.Driver") {
        throw "Missing no-reboot or component-only restriction."
    }
    $state.Installs++
    $mock = [pscustomobject]@{ ExitCode = $case.InstallExit }
    $mock | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Milliseconds) $true }
    $mock
}

$scenarios = @(
    @{ Name = "compatible"; Before = "Driver Version: 591.59 CUDA Version: 13.1"; Downloads = 0; Installs = 0 },
    @{ Name = "newer"; Before = "Driver Version: 610.00 CUDA Version: 14.0"; Downloads = 0; Installs = 0 },
    @{ Name = "upgrade-old" },
    @{ Name = "no-consent"; Allow = $false; Error = "explicit opt-in"; Downloads = 0; Installs = 0 },
    @{ Name = "self-hosted"; RunnerEnvironment = "self-hosted"; Error = "GitHub-hosted"; Downloads = 0; Installs = 0 },
    @{ Name = "not-manual"; Event = "push"; Error = "manually dispatched"; Downloads = 0; Installs = 0 },
    @{ Name = "not-actions"; Actions = "false"; Error = "explicit opt-in"; Downloads = 0; Installs = 0 },
    @{ Name = "not-windows"; OS = "Linux"; Error = "Windows runner"; Downloads = 0; Installs = 0 },
    @{ Name = "broken-driver"; ProbeExit = 9; Error = "nvidia-smi failed"; Downloads = 0; Installs = 0 },
    @{ Name = "unknown-capability"; Before = "CUDA Version: N/A"; Error = "did not report"; Downloads = 0; Installs = 0 },
    @{ Name = "download-fails"; DownloadExit = 22; Error = "Downloading"; Installs = 0 },
    @{ Name = "bad-signature"; Signature = "HashMismatch"; Error = "valid NVIDIA signature"; Installs = 0 },
    @{ Name = "reboot-required"; InstallExit = 1; Error = "requested a reboot" },
    @{ Name = "msi-reboot-required"; InstallExit = 3010; Error = "requested a reboot" },
    @{ Name = "install-failed"; InstallExit = 5; Error = "setup failed" },
    @{ Name = "inactive-driver"; After = "Driver Version: 471.11 CUDA Version: 11.4"; Error = "not active with CUDA 13.1" },
    @{ Name = "wrong-driver-version"; After = "Driver Version: 591.00 CUDA Version: 13.1"; Error = "expected driver version" }
)
try {
    foreach ($scenario in $scenarios) {
        $case = @{
            Before = "Driver Version: 471.11 CUDA Version: 11.4"
            After = "Driver Version: 591.59 CUDA Version: 13.1"
            Allow = $true; Actions = "true"; RunnerEnvironment = "github-hosted"; OS = "Windows"; Event = "workflow_dispatch"
            ProbeExit = 0; DownloadExit = 0; Signature = "Valid"; InstallExit = 0
            Downloads = 1; Installs = 1; Error = $null
        }
        foreach ($key in $scenario.Keys) { $case[$key] = $scenario[$key] }
        $state = [pscustomobject]@{ Probes = 0; Downloads = 0; Installs = 0 }
        $env:GITHUB_ACTIONS = $case.Actions
        $env:RUNNER_ENVIRONMENT = $case.RunnerEnvironment
        $env:RUNNER_OS = $case.OS
        $env:GITHUB_EVENT_NAME = $case.Event
        $env:RUNNER_TEMP = $temporary
        $failure = $null
        try { & $prepare -AllowInstallation:$case.Allow -Logs (Join-Path $temporary $case.Name) }
        catch { $failure = $_.Exception.Message }
        if ($case.Error) {
            if (-not $failure -or $failure -notmatch $case.Error) { throw "$($case.Name): expected '$($case.Error)', got '$failure'" }
        } elseif ($failure) { throw "$($case.Name): unexpected failure: $failure" }
        if ($state.Downloads -ne $case.Downloads -or $state.Installs -ne $case.Installs) {
            throw "$($case.Name): unexpected download/install counts: $state"
        }
        Write-Host "PASS $($case.Name)"
    }
    Write-Host "All $($scenarios.Count) hosted driver safety checks passed; all external operations were mocked."
    $global:LASTEXITCODE = 0
} finally {
    foreach ($name in $environment) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
