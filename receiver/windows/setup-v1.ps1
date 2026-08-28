#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Probe', 'Apply', 'Commit', 'Rollback', 'Remove')]
    [string]$Mode,
    [string]$ReceiverSource = '',
    [string]$PublicKeyFile = '',
    [string]$ReceiverSha256 = '',
    [string]$SetupSha256 = '',
    [bool]$AdministratorConfirmed = $false,
    [ValidateRange(1, 65535)]
    [int]$SshPort = 22,
    [bool]$InboundSshVerified = $false,
    [string]$TransactionPath = '',
    [string]$KeyBody = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$CompanionVersion = '1.1.1'
$OpenSshCapability = 'OpenSSH.Server~~~~0.0.1.0'
$FirewallRuleName = if ($SshPort -eq 22) {
    'OpenSSH-Server-In-TCP'
} else {
    "SSH-Mixer-OpenSSH-$SshPort"
}
$ReceiverPath = Join-Path $env:USERPROFILE '.ssh\ssh-mixer-receiver-v1.ps1'
$Transaction = Join-Path $env:TEMP ("ssh-mixer-windows-setup-{0}" -f [Guid]::NewGuid().ToString('N'))
$ChangesStarted = $false
$ReceiverExisted = $false
$KeysExisted = $false
$KeysAclChanged = $false
$OriginalKeysSddl = ''
$FirewallCreated = $false
$OpenSshInstalled = $false
$FfmpegInstalled = $false
$FfmpegRollbackComplete = $true
$CreatedDirectories = [Collections.Generic.List[string]]::new()
$AuthorizedKeys = ''

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-AdministratorCapable {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $adminGroup = Get-LocalGroup -SID 'S-1-5-32-544'
        return $null -ne (Get-LocalGroupMember -Group $adminGroup | Where-Object {
            $_.SID.Value -eq $identity.User.Value
        })
    }
    catch {
        return Test-Elevated
    }
}

function Get-OpenSshVersion {
    $sshd = Get-Command 'sshd.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $sshd) {
        $systemSshd = Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'
        if (Test-Path -LiteralPath $systemSshd -PathType Leaf) {
            return (Get-Item -LiteralPath $systemSshd).VersionInfo.FileVersion
        }
        return ''
    }
    return (Get-Item -LiteralPath $sshd.Source).VersionInfo.FileVersion
}

function Test-FFplayUsable {
    $command = Get-Command 'ffplay.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $command) { return $false }
    try {
        & $command.Source '-version' *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-Probe {
    $openSshVersion = Get-OpenSshVersion
    $service = $null
    try { $service = Get-Service -Name 'sshd' -ErrorAction Stop } catch { }
    $firewall = $null
    try { $firewall = Get-NetFirewallRule -Name $FirewallRuleName -ErrorAction Stop } catch { }
    $firewallPort = if ($null -ne $firewall) {
        ($firewall | Get-NetFirewallPortFilter).LocalPort
    } else { '' }
    return @{
        schemaVersion = 1
        platform = 'windows'
        user = $env:USERNAME
        profile = $env:USERPROFILE
        openSshVersion = $openSshVersion
        sshdInstalled = (-not [string]::IsNullOrWhiteSpace($openSshVersion))
        sshdRunning = ($null -ne $service -and $service.Status -eq 'Running')
        firewallRule = ($null -ne $firewall)
        firewallPort = [string]$firewallPort
        ffplay = (Test-FFplayUsable)
        winget = ($null -ne (Get-Command 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue))
        administratorCapable = Test-AdministratorCapable
        elevated = Test-Elevated
    }
}

function Set-RestrictedAcl {
    param(
        [string]$Path,
        [bool]$AdministratorFile
    )
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::None
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $full = [Security.AccessControl.FileSystemRights]::FullControl
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($system, $full, $inheritance, $propagation, $allow))
    if ($AdministratorFile) {
        $administrators = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($administrators, $full, $inheritance, $propagation, $allow))
        $acl.SetOwner($administrators)
    }
    else {
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user, $full, $inheritance, $propagation, $allow))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Test-RestrictedAcl {
    param(
        [string]$Path,
        [bool]$AdministratorFile
    )
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) { return $false }
    $allowed = [Collections.Generic.HashSet[string]]::new()
    [void]$allowed.Add('S-1-5-18')
    if ($AdministratorFile) {
        [void]$allowed.Add('S-1-5-32-544')
        $expectedOwner = 'S-1-5-32-544'
    }
    else {
        $expectedOwner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        [void]$allowed.Add($expectedOwner)
    }
    $owner = ([Security.Principal.NTAccount]::new($acl.Owner)).Translate([Security.Principal.SecurityIdentifier]).Value
    if ($owner -ne $expectedOwner) { return $false }
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($rule.AccessControlType -eq 'Allow' -and -not $allowed.Contains($sid)) {
            return $false
        }
    }
    return $true
}

function Update-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Get-ForcedEntry {
    param([string]$PublicKey)
    $fields = @($PublicKey -split ' ' | Where-Object { $_ -ne '' })
    if ($fields.Count -lt 2 -or $fields[0] -ne 'ssh-ed25519' -or $fields[1] -notmatch '^[A-Za-z0-9+/]+={0,3}$') {
        throw 'Managed Identity public key is invalid'
    }
    $launcher = "& `"`$env:USERPROFILE\.ssh\ssh-mixer-receiver-v1.ps1`" -Forced -KeyBody '$($fields[1])'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launcher))
    $command = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -EncodedCommand $encoded"
    return "command=`"$command`",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 $($fields[1]) ssh-mixer-managed-windows-v1"
}

function Invoke-Rollback {
    $complete = $true
    try {
        if ($ReceiverExisted) {
            Copy-Item -LiteralPath (Join-Path $Transaction 'receiver.backup') -Destination $ReceiverPath -Force
        }
        else {
            Remove-Item -LiteralPath $ReceiverPath -Force -ErrorAction SilentlyContinue
        }
    }
    catch { $complete = $false }
    try {
        if ($KeysExisted) {
            Copy-Item -LiteralPath (Join-Path $Transaction 'authorized_keys.backup') -Destination $AuthorizedKeys -Force
            if ($KeysAclChanged) {
                $restoredAcl = Get-Acl -LiteralPath $AuthorizedKeys
                $restoredAcl.SetSecurityDescriptorSddlForm($OriginalKeysSddl)
                Set-Acl -LiteralPath $AuthorizedKeys -AclObject $restoredAcl
            }
        }
        else {
            Remove-Item -LiteralPath $AuthorizedKeys -Force -ErrorAction SilentlyContinue
        }
    }
    catch { $complete = $false }
    try {
        if ($FfmpegInstalled) {
            & winget uninstall --id 'Gyan.FFmpeg' --exact --source winget --disable-interactivity
            if ($LASTEXITCODE -ne 0) { $complete = $false }
        }
        if (-not $FfmpegRollbackComplete) { $complete = $false }
    }
    catch { $complete = $false }
    try {
        if ($FirewallCreated) {
            Remove-NetFirewallRule -Name $FirewallRuleName
        }
        if ($OpenSshInstalled) {
            Stop-Service -Name 'sshd' -ErrorAction SilentlyContinue
            Remove-WindowsCapability -Online -Name $OpenSshCapability | Out-Null
        }
    }
    catch { $complete = $false }
    foreach ($directory in $CreatedDirectories) {
        try {
            Remove-Item -LiteralPath $directory -Force -ErrorAction Stop
        }
        catch {
            if (Test-Path -LiteralPath $directory) { $complete = $false }
        }
    }
    if ($complete) {
        [Console]::Error.WriteLine('{"schemaVersion":1,"ok":false,"code":"rolled-back"}')
    }
    else {
        [Console]::Error.WriteLine('{"schemaVersion":1,"ok":false,"code":"Rollback-Incomplete"}')
    }
    return $complete
}

function Assert-TransactionPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'setup transaction path is required' }
    $full = [IO.Path]::GetFullPath($Path)
    $temp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if (-not $full.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($full) -notmatch '^ssh-mixer-windows-setup-[0-9a-f]{32}$') {
        throw 'setup transaction path is invalid'
    }
    return $full
}

if ($Mode -eq 'Probe') {
    Get-Probe | ConvertTo-Json -Compress
    exit 0
}

if ($Mode -eq 'Remove') {
    if ($KeyBody -notmatch '^[A-Za-z0-9+/]+={0,3}$') {
        throw 'Managed Identity key body is invalid'
    }
    $candidateKeyFiles = @(
        (Join-Path $env:USERPROFILE '.ssh\authorized_keys'),
        (Join-Path $env:ProgramData 'ssh\administrators_authorized_keys')
    ) | Select-Object -Unique
    $suffix = " ssh-ed25519 $KeyBody ssh-mixer-managed-windows-v1"
    foreach ($keyFile in $candidateKeyFiles) {
        if (Test-Path -LiteralPath $keyFile -PathType Leaf) {
            $item = Get-Item -LiteralPath $keyFile
            $parentItem = Get-Item -LiteralPath (Split-Path -Parent $keyFile)
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                ($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'authorized_keys path is unsafe'
            }
            $retained = @(Get-Content -LiteralPath $keyFile | Where-Object { -not $_.EndsWith($suffix, [StringComparison]::Ordinal) })
            Set-Content -LiteralPath $keyFile -Value $retained -Encoding ascii
            if ($null -ne (Get-Content -LiteralPath $keyFile | Where-Object { $_.EndsWith($suffix, [StringComparison]::Ordinal) })) {
                throw 'Managed Identity key revocation could not be verified'
            }
        }
    }
    $remaining = @($candidateKeyFiles | Where-Object {
        (Test-Path -LiteralPath $_ -PathType Leaf) -and
        ($null -ne (Get-Content -LiteralPath $_ | Where-Object { $_ -match ' ssh-mixer-managed-windows-v1$' }))
    })
    $helperRemoved = $false
    if ($remaining.Count -eq 0) {
        $quietState = Join-Path $env:LOCALAPPDATA 'ssh-mixer\quiet-test-v1.json'
        Remove-Item -LiteralPath $ReceiverPath, $quietState -Force -ErrorAction SilentlyContinue
        if ((Test-Path -LiteralPath $ReceiverPath) -or (Test-Path -LiteralPath $quietState)) {
            throw 'unshared Receiver state removal could not be verified'
        }
        $helperRemoved = $true
    }
    @{ schemaVersion = 1; ok = $true; keyRevoked = $true; helperRemoved = $helperRemoved } | ConvertTo-Json -Compress
    exit 0
}

if ($Mode -eq 'Commit') {
    $transactionToCommit = Assert-TransactionPath -Path $TransactionPath
    if (-not (Test-Path -LiteralPath (Join-Path $transactionToCommit 'transaction.json') -PathType Leaf)) {
        throw 'setup transaction metadata is missing'
    }
    Remove-Item -LiteralPath $transactionToCommit -Recurse -Force
    '{"schemaVersion":1,"ok":true,"committed":true}'
    exit 0
}

if ($Mode -eq 'Rollback') {
    $Transaction = Assert-TransactionPath -Path $TransactionPath
    $metadataPath = Join-Path $Transaction 'transaction.json'
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        throw 'setup transaction metadata is missing'
    }
    $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    $ReceiverPath = [string]($metadata.receiverPath)
    $AuthorizedKeys = [string]($metadata.authorizedKeys)
    $ReceiverExisted = [bool]($metadata.receiverExisted)
    $KeysExisted = [bool]($metadata.keysExisted)
    $KeysAclChanged = [bool]($metadata.keysAclChanged)
    $OriginalKeysSddl = [string]($metadata.originalKeysSddl)
    $FirewallCreated = [bool]($metadata.firewallCreated)
    $OpenSshInstalled = [bool]($metadata.openSshInstalled)
    $FfmpegInstalled = [bool]($metadata.ffmpegInstalled)
    $FfmpegRollbackComplete = [bool]($metadata.ffmpegRollbackComplete)
    $ChangesStarted = $true
    foreach ($directory in @($metadata.createdDirectories)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$directory)) {
            $CreatedDirectories.Add([string]$directory)
        }
    }
    $rolledBack = Invoke-Rollback
    Remove-Item -LiteralPath $Transaction -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Transaction) { $rolledBack = $false }
    @{ schemaVersion = 1; ok = $rolledBack; complete = $rolledBack } | ConvertTo-Json -Compress
    if ($rolledBack) { exit 0 } else { exit 1 }
}

try {
    $probe = Get-Probe
    if ($probe.administratorCapable -and -not $AdministratorConfirmed) {
        throw 'administrator-capable Receiver setup requires confirmation'
    }
    if (-not (Test-Path -LiteralPath $ReceiverSource -PathType Leaf) -or
        -not (Test-Path -LiteralPath $PublicKeyFile -PathType Leaf)) {
        throw 'staged setup artifacts are missing'
    }
    if ($ReceiverSha256 -notmatch '^[0-9a-f]{64}$' -or $SetupSha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'artifact checksum is invalid'
    }
    if ((Get-FileHash -LiteralPath $ReceiverSource -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ReceiverSha256) {
        throw 'Receiver Protocol checksum verification failed'
    }
    if ((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $SetupSha256) {
        throw 'Companion Setup checksum verification failed'
    }

    New-Item -ItemType Directory -Path $Transaction | Out-Null
    $administratorFile = [bool]($probe.administratorCapable)
    $AuthorizedKeys = if ($administratorFile) {
        Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
    } else {
        Join-Path $env:USERPROFILE '.ssh\authorized_keys'
    }
    $receiverDirectory = Split-Path -Parent $ReceiverPath
    $authorizedDirectory = Split-Path -Parent $AuthorizedKeys
    $ChangesStarted = $true
    foreach ($directory in (@($receiverDirectory, $authorizedDirectory) | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            $CreatedDirectories.Add($directory)
        }
    }
    if (Test-Path -LiteralPath $ReceiverPath -PathType Leaf) {
        $ReceiverExisted = $true
        Copy-Item -LiteralPath $ReceiverPath -Destination (Join-Path $Transaction 'receiver.backup')
    }
    if (Test-Path -LiteralPath $AuthorizedKeys -PathType Leaf) {
        $KeysExisted = $true
        Copy-Item -LiteralPath $AuthorizedKeys -Destination (Join-Path $Transaction 'authorized_keys.backup')
        $OriginalKeysSddl = (Get-Acl -LiteralPath $AuthorizedKeys).Sddl
    }

    if (-not $probe.sshdInstalled) {
        if (-not (Test-Elevated)) { throw 'OpenSSH capability installation requires an approved Administrator terminal' }
        Add-WindowsCapability -Online -Name $OpenSshCapability | Out-Null
        $OpenSshInstalled = $true
        Set-Service -Name 'sshd' -StartupType Automatic
        Start-Service -Name 'sshd'
    }
    $installedVersionText = Get-OpenSshVersion
    if ($installedVersionText -notmatch '^\d+\.\d+(?:\.\d+)?(?:\.\d+)?') {
        throw 'installed Windows OpenSSH version could not be verified'
    }
    try { $installedVersion = [version]$Matches[0] }
    catch { throw 'installed Windows OpenSSH version could not be verified' }
    if ($installedVersion -lt [version]'8.1.0.0') {
        throw 'Windows OpenSSH 8.1 or newer is required for forced restrictions'
    }
    if ((-not $probe.firewallRule -or ([string]($probe.firewallPort)) -ne ([string]$SshPort)) -and -not $InboundSshVerified) {
        if (-not (Test-Elevated)) { throw 'firewall setup requires an approved Administrator terminal' }
        New-NetFirewallRule -Name $FirewallRuleName -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort $SshPort | Out-Null
        $FirewallCreated = $true
    }
    if (-not $probe.ffplay) {
        & winget list --id 'Gyan.FFmpeg' --exact --source winget --disable-interactivity | Out-Null
        $packageAlreadyPresent = ($LASTEXITCODE -eq 0)
        if ($packageAlreadyPresent) { $FfmpegRollbackComplete = $false }
        $FfmpegInstalled = -not $packageAlreadyPresent
        & winget install --id 'Gyan.FFmpeg' --exact --source winget --scope user --accept-source-agreements --accept-package-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) { throw 'winget FFmpeg installation failed' }
        Update-ProcessPath
    }
    if (-not (Test-FFplayUsable)) {
        throw 'ffplay.exe was not executable after package installation'
    }

    Copy-Item -LiteralPath $ReceiverSource -Destination $ReceiverPath -Force
    if ((Get-FileHash -LiteralPath $ReceiverPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ReceiverSha256) {
        throw 'installed Receiver Protocol checksum verification failed'
    }
    $managedPublicKey = (Get-Content -LiteralPath $PublicKeyFile -Raw).Trim()
    $managedKeyBody = @($managedPublicKey -split ' ' | Where-Object { $_ -ne '' })[1]
    $entry = Get-ForcedEntry -PublicKey $managedPublicKey
    $managedSuffix = " ssh-ed25519 $managedKeyBody ssh-mixer-managed-windows-v1"
    $existing = if ($KeysExisted) {
        @(Get-Content -LiteralPath $AuthorizedKeys | Where-Object {
            -not $_.EndsWith($managedSuffix, [StringComparison]::Ordinal)
        })
    } else { @() }
    if (-not $administratorFile -and $KeysExisted -and -not (Test-RestrictedAcl -Path $AuthorizedKeys -AdministratorFile $false)) {
        throw 'standard Receiver authorized_keys ACL requires separate native preparation'
    }
    @(@($existing) + @($entry)) | Set-Content -LiteralPath $AuthorizedKeys -Encoding ascii
    if ($administratorFile) {
        Set-RestrictedAcl -Path $AuthorizedKeys -AdministratorFile $true
        $KeysAclChanged = $true
    }
    elseif (-not $KeysExisted) {
        Set-RestrictedAcl -Path $AuthorizedKeys -AdministratorFile $false
    }
    if (-not (Test-RestrictedAcl -Path $AuthorizedKeys -AdministratorFile $administratorFile)) {
        throw 'Windows OpenSSH key ACL verification failed'
    }
    if ($null -eq (Get-Content -LiteralPath $AuthorizedKeys | Where-Object { $_ -ceq $entry })) {
        throw 'forced Managed Identity entry verification failed'
    }

    @{
        schemaVersion = 1
        receiverPath = $ReceiverPath
        authorizedKeys = $AuthorizedKeys
        receiverExisted = $ReceiverExisted
        keysExisted = $KeysExisted
        keysAclChanged = $KeysAclChanged
        originalKeysSddl = $OriginalKeysSddl
        firewallCreated = $FirewallCreated
        openSshInstalled = $OpenSshInstalled
        ffmpegInstalled = $FfmpegInstalled
        ffmpegRollbackComplete = $FfmpegRollbackComplete
        createdDirectories = @($CreatedDirectories)
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $Transaction 'transaction.json') -Encoding utf8
    @{
        schemaVersion = 1
        ok = $true
        protocol = 'v1'
        companionVersion = $CompanionVersion
        platform = 'windows'
        runtimeElevated = $false
        administratorConfirmed = [bool]$AdministratorConfirmed
        packageInstalled = $FfmpegInstalled
        transaction = $Transaction
    } | ConvertTo-Json -Compress
    exit 0
}
catch {
    if ($ChangesStarted) {
        [void](Invoke-Rollback)
    }
    else {
        [Console]::Error.WriteLine('{"schemaVersion":1,"ok":false,"code":"no-changes-applied"}')
    }
    [Console]::Error.WriteLine((@{
        schemaVersion = 1
        ok = $false
        stage = 'receiver.windows-setup'
        code = 'setup-failed'
        message = $_.Exception.Message
    } | ConvertTo-Json -Compress))
    Remove-Item -LiteralPath $Transaction -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Transaction) {
        [Console]::Error.WriteLine('{"schemaVersion":1,"ok":false,"code":"Rollback-Incomplete"}')
    }
    exit 1
}
