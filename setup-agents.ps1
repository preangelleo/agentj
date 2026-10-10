# Windows entry: Agent J runs inside WSL2, never directly in Windows.
[CmdletBinding()]
param([switch]$DryRun, [switch]$NoAliases, [switch]$WithAgentJ, [switch]$NoAgentJ,
      [switch]$Relay, [switch]$NoRelay, [switch]$NoFullAccess, [switch]$Mirror,
      [string]$Cli = 'opencode,codex')
$ErrorActionPreference = 'Stop'
if (($WithAgentJ -and $NoAgentJ) -or ($Relay -and $NoRelay)) { throw 'Choose only one of each route/Agent J option.' }
if ($Cli -notmatch '^(opencode|codex|claude|gemini)(,(opencode|codex|claude|gemini))*$') { throw 'Invalid CLI list.' }
$setupArgs = @('--cli', $Cli)
if ($NoAliases) { $setupArgs += '--no-aliases' }
if ($WithAgentJ) { $setupArgs += '--with-agentj' }
if ($NoAgentJ) { $setupArgs += '--no-agentj' }
if ($Relay) { $setupArgs += '--relay' }
if ($NoRelay) { $setupArgs += '--no-relay' }
if ($NoFullAccess) { $setupArgs += '--no-full-access' }
if ($Mirror) { $setupArgs += '--mirror' }
if ($DryRun) {
    Write-Output 'PLAN: check Windows build, administrator rights and WSL2; enable WSL, install Ubuntu, reboot if required; run setup-agents.sh in Ubuntu.'
    Write-Output ('PLAN: Ubuntu setup ' + ($setupArgs -join ' ') + '; automatic npm/Node mirror fallback + SHA checks; selected verified WSL tools get PowerShell functions; gx only with explicit gemini; personal Google login discontinued, paid Gemini API key required; Agent J uses official assistant.')
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
    Write-Output ('curl -fsSL https://agentj.app/setup-agents.sh | bash -s -- ' + ($setupArgs -join ' '))
    if ($installed -match 'Ubuntu') {
        & wsl.exe -d Ubuntu -- bash -c 'taskdir=$(mktemp -d); trap ''rm -rf "$taskdir"'' EXIT; curl --connect-timeout 10 --max-time 45 -fsSL https://agentj.app/setup-agents.sh -o "$taskdir/setup.sh" && bash "$taskdir/setup.sh" "$@"' -- @setupArgs
        if ($LASTEXITCODE -ne 0) { throw 'Ubuntu setup incomplete; repair the reported component and rerun.' }
        if (!$NoAliases) {
            $definitions = @{codex='cx';claude='cc';opencode='oc';gemini='gx'}
            $flags = @{codex='--dangerously-bypass-approvals-and-sandbox';claude='--dangerously-skip-permissions';opencode='--auto';gemini='--yolo --sandbox=false'}
            New-Item -ItemType Directory -Force (Split-Path -Parent $PROFILE) | Out-Null
            if (!(Test-Path -LiteralPath $PROFILE)) { New-Item -ItemType File $PROFILE | Out-Null }
            if ((Get-Item -LiteralPath $PROFILE).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing profile symlink.' }
            foreach ($tool in ($Cli -split ',')) {
                & wsl.exe -d Ubuntu -- bash -lc "command -v $tool >/dev/null && $tool --version >/dev/null && $tool --help >/dev/null"
                if ($LASTEXITCODE -ne 0) { continue }
                $flag = if ($NoFullAccess) { '' } else { $flags[$tool] }
                $line = 'function ' + $definitions[$tool] + ' { wsl.exe -d Ubuntu -- bash -lc ''exec ' + $tool + ' ' + $flag + ' "$@"'' -- @args }'
                if (@(Get-Content -LiteralPath $PROFILE) -cnotcontains $line) { Add-Content -LiteralPath $PROFILE -Value ("`n" + $line) }
            }
            Write-Output 'Aliases ready in a new PowerShell terminal. cc shadows a compiler command; rename it ccx if needed.'
        }
    }
} catch {
    Write-Error 'Windows setup failed. Manual steps: https://agentj.app/docs/setup-agents/#windows'
    throw
}
