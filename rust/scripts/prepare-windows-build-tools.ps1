$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

# The hosted GPU image is a Data Science VM, not the standard Windows CI image.
# Provision only the ephemeral Actions host; never reboot it.
if ($env:GITHUB_ACTIONS -ne "true" -or $env:OS -ne "Windows_NT") {
    throw "This helper is only for ephemeral Windows GitHub Actions runners"
}
$vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
$components = @(
    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
    "Microsoft.VisualStudio.Component.VC.14.38.17.8.x86.x64",
    "Microsoft.VisualStudio.Component.VC.CMake.Project",
    "Microsoft.VisualStudio.Component.Windows11SDK.26100"
)

function Find-Compiler {
    if (Test-Path -LiteralPath $vswhere) {
        & $vswhere -latest -products '*' -version '[17.0,18.0)' `
            -requires $components -property installationPath
    }
}

$installation = Find-Compiler
if (-not $installation) {
    $installer = Join-Path $env:RUNNER_TEMP "zg-vs2022-build-tools.exe"
    & curl.exe --fail --location --retry 3 --max-time 300 `
        https://aka.ms/vs/17/release/vs_BuildTools.exe --output $installer
    if ($LASTEXITCODE -ne 0) { throw "Downloading Visual Studio Build Tools failed" }
    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
        throw "Visual Studio installer signature is not trusted"
    }
    $existing = if (Test-Path -LiteralPath $vswhere) {
        & $vswhere -latest -products '*' -version '[17.0,18.0)' -property installationPath
    } else { $null }
    $arguments = @('--quiet', '--wait', '--norestart', '--nocache')
    if ($existing) {
        $arguments = @('modify', '--installPath', "`"$existing`"") + $arguments
    }
    foreach ($component in $components) { $arguments += @('--add', $component) }
    $result = Start-Process $installer -ArgumentList $arguments -Wait -PassThru
    if ($result.ExitCode -notin @(0, 3010)) {
        throw "Visual Studio Build Tools installation failed: $($result.ExitCode)"
    }
    $installation = Find-Compiler
    if (-not $installation) { throw "Visual Studio 2022 C++ build components are still missing" }
}
Write-Host "Visual Studio CUDA build tools: $installation"

$clang = Join-Path $env:RUNNER_TEMP "zg-libclang"
& python -m pip install --no-deps --only-binary=:all: --require-hashes `
    --target $clang -r rust/scripts/windows-build-requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Installing the pinned libclang DLL failed" }
$clangDirectory = Join-Path $clang "clang/native"
if (-not (Test-Path -LiteralPath (Join-Path $clangDirectory "libclang.dll"))) {
    throw "The libclang wheel did not contain libclang.dll"
}
"LIBCLANG_PATH=$clangDirectory" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
