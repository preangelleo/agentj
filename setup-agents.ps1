# Windows entry: Agent J runs inside WSL2, never directly in Windows.
[CmdletBinding()]
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
if ($DryRun) {
    Write-Output 'PLAN: check Windows build, administrator rights and WSL2; enable WSL, install Ubuntu, reboot if required; run setup-agents.sh in Ubuntu.'
    return
}
try {
    if ([Environment]::OSVersion.Version.Build -lt 19041) {
        throw 'Windows 10 build 19041+ or Windows 11 is required.'
    }
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open PowerShell as Administrator, then run this script again.'
    }
    $installed = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0 -or -not ($installed -match 'Ubuntu')) {
        & wsl.exe --install -d Ubuntu
        if ($LASTEXITCODE -ne 0) { throw 'WSL installation failed. Follow https://agentj.app/docs/setup-agents/#windows' }
        Write-Output 'Restart Windows if asked, open Ubuntu, create your Linux username/password, then run the command below.'
    } else {
        & wsl.exe --set-version Ubuntu 2
        if ($LASTEXITCODE -ne 0) { throw 'WSL2 conversion failed; check BIOS virtualization and the Windows WSL guide.' }
    }
    & wsl.exe --set-default-version 2
    if ($LASTEXITCODE -ne 0) { throw 'Could not set WSL2 default.' }
    Write-Output 'In Ubuntu (not PowerShell):'
    Write-Output 'sudo apt-get update'
    Write-Output 'sudo apt-get install -y curl ca-certificates'
    Write-Output 'curl -fsSL https://agentj.app/setup-agents.sh | bash'
} catch {
    Write-Error 'Windows setup failed. Manual steps: https://agentj.app/docs/setup-agents/#windows'
    throw
}
