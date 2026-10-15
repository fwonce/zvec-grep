$ErrorActionPreference = 'Stop'
# Microsoft standalone Debugging Tools only; no compiler or native SDK build.
$cdb = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Debuggers\x64\cdb.exe'
if (-not (Test-Path -LiteralPath $cdb)) {
    $installer = Join-Path $env:RUNNER_TEMP 'zg-windows-debuggers-22621.exe'
    & curl.exe -fL --retry 2 --connect-timeout 15 --max-time 120 -o $installer 'https://download.microsoft.com/download/3/b/d/3bd97f81-3f5b-4922-b86d-dc5145cd6bfe/windowssdk/winsdksetup.exe'
    if ($LASTEXITCODE -ne 0) { throw 'Debugger installer download failed' }
    if ((Get-FileHash $installer -Algorithm SHA256).Hash -ne '3F73F59566B0CF3EDDDDAF61AD72BB0C6E4588A5D9E004ABF68115B752EBBBD8') { throw 'Debugger installer SHA256 mismatch' }
    $signature = Get-AuthenticodeSignature $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'Invalid debugger installer signature' }
    $setup = Start-Process -FilePath $installer -ArgumentList '/features', 'OptionId.WindowsDesktopDebuggers', '/quiet', '/norestart' -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "Debugger installation failed or requires restart: $($setup.ExitCode)" }
}
if (-not (Test-Path -LiteralPath $cdb)) { throw 'CDB is missing' }
"ZG_CDB=$cdb" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
& $cdb -version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
