param(
    [Parameter(Mandatory = $true)]
    [switch]$Forced,
    [string]$KeyBody = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Protocol = 'v1'
$ProtocolVersion = 1
$HelperVersion = '1.1.1'
$QuietStartDbfs = -40
$QuietMaximumDbfs = -24
$QuietStepDb = 4
$QuietDurationSeconds = 0.5
$QuietFadeSeconds = 0.08

function Write-ProtocolError {
    param([string]$Message)
    $payload = @{
        schemaVersion = 1
        ok = $false
        stage = 'receiver.protocol'
        code = 'protocol-rejected'
        message = $Message
    } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($payload)
}

function Assert-NonElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Receiver Protocol refuses an elevated runtime token'
    }
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

function Resolve-FFplay {
    if (-not (Test-FFplayUsable)) {
        throw 'ffplay.exe is unavailable or not executable'
    }
    return (Get-Command 'ffplay.exe' -CommandType Application).Source
}

function Parse-ReceiverOperation {
    param([string]$Command)
    $parts = @($Command -split ' ' | Where-Object { $_ -ne '' })
    if ($parts.Count -lt 3 -or $parts[0] -ne 'ssh-mixer-receiver' -or $parts[1] -ne 'v1') {
        throw 'Receiver Protocol v1 is required'
    }
    if ($parts[2] -in @('capabilities', 'diagnostics', 'play', 'remove')) {
        if ($parts.Count -ne 3) {
            throw 'operation does not accept arguments'
        }
        return @{ operation = $parts[2] }
    }
    if ($parts[2] -eq 'quiet-test') {
        if ($parts.Count -ne 5 -or $parts[3] -ne '--dbfs') {
            throw 'quiet-test requires --dbfs LEVEL'
        }
        $level = 0
        if (-not [int]::TryParse($parts[4], [ref]$level)) {
            throw 'quiet-test level must be an integer'
        }
        if ($level -notin @(-40, -36, -32, -28, -24)) {
            throw 'quiet-test level is outside the approved range'
        }
        return @{ operation = 'quiet-test'; dbfs = $level }
    }
    throw 'operation is not allowed'
}

function Get-QuietStatePath {
    $stateDirectory = Join-Path $env:LOCALAPPDATA 'ssh-mixer'
    return Join-Path $stateDirectory 'quiet-test-v1.json'
}

function Get-PreviousQuietLevel {
    $path = Get-QuietStatePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return $null
    }
    try {
        $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        return [int]$state.dbfs
    }
    catch {
        return $null
    }
}

function Save-QuietLevel {
    param([int]$Dbfs)
    $path = Get-QuietStatePath
    $directory = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$path.tmp-$PID"
    @{ schemaVersion = 1; dbfs = $Dbfs } |
        ConvertTo-Json -Compress |
        Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $path -Force
}

function Invoke-QuietTest {
    param([int]$Dbfs)
    $previous = Get-PreviousQuietLevel
    if ($null -eq $previous) {
        if ($Dbfs -ne $QuietStartDbfs) {
            throw 'the first quiet test must start at -40 dBFS'
        }
    }
    elseif ($Dbfs -notin @($QuietStartDbfs, $previous, ($previous + $QuietStepDb))) {
        throw 'quiet test increases must use 4 dB steps'
    }
    if ($Dbfs -lt $QuietStartDbfs -or $Dbfs -gt $QuietMaximumDbfs) {
        throw 'quiet test level is outside the approved range'
    }

    $ffplay = Resolve-FFplay
    $amplitude = [Math]::Pow(10.0, $Dbfs / 20.0).ToString('0.00000000', [Globalization.CultureInfo]::InvariantCulture)
    $source = "aevalsrc=$amplitude*sin(2*PI*440*t):s=48000:d=$QuietDurationSeconds"
    $fadeOutStart = $QuietDurationSeconds - $QuietFadeSeconds
    $filter = "afade=t=in:st=0:d=$QuietFadeSeconds,afade=t=out:st=$fadeOutStart:d=$QuietFadeSeconds"
    & $ffplay '-hide_banner' '-loglevel' 'error' '-nodisp' '-autoexit' '-f' 'lavfi' '-i' $source '-af' $filter '-t' "$QuietDurationSeconds"
    if ($LASTEXITCODE -ne 0) {
        throw 'quiet test playback failed'
    }
    Save-QuietLevel -Dbfs $Dbfs
}

function Write-Capabilities {
    @{
        schemaVersion = 1
        protocol = $Protocol
        protocolVersion = $ProtocolVersion
        helperVersion = $HelperVersion
        platform = 'windows'
        operations = @('capabilities', 'diagnostics', 'play', 'quiet-test', 'remove')
        ffplay = (Test-FFplayUsable)
        runtimeElevated = $false
        quietTest = @{
            startDbfs = $QuietStartDbfs
            maximumDbfs = $QuietMaximumDbfs
            stepDb = $QuietStepDb
            durationSeconds = $QuietDurationSeconds
        }
    } | ConvertTo-Json -Compress -Depth 4
}

function Invoke-SelfRemoval {
    if ($KeyBody -notmatch '^[A-Za-z0-9+/]+={0,3}$') {
        throw 'Managed Identity key body is invalid'
    }
    $candidateKeyFiles = @(
        (Join-Path $env:USERPROFILE '.ssh\\authorized_keys'),
        (Join-Path $env:ProgramData 'ssh\\administrators_authorized_keys')
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
        $quietState = Get-QuietStatePath
        Remove-Item -LiteralPath $quietState -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction Stop
        if ((Test-Path -LiteralPath $quietState) -or (Test-Path -LiteralPath $PSCommandPath)) {
            throw 'unshared Receiver state removal could not be verified'
        }
        $helperRemoved = $true
    }
    @{
        schemaVersion = 1
        ok = $true
        keyRevoked = $true
        helperRemoved = $helperRemoved
        runtimeElevated = $false
    } | ConvertTo-Json -Compress
}

function Write-Diagnostics {
    @{
        schemaVersion = 1
        protocol = $Protocol
        protocolVersion = $ProtocolVersion
        helperVersion = $HelperVersion
        platform = 'windows'
        powerShell = $PSVersionTable.PSVersion.ToString()
        openSshSession = ($null -ne $env:SSH_CONNECTION)
        ffplayAvailable = (Test-FFplayUsable)
        runtimeElevated = $false
    } | ConvertTo-Json -Compress
}

try {
    if (-not $Forced -or $KeyBody -notmatch '^[A-Za-z0-9+/]+={0,3}$') {
        throw 'Receiver Protocol requires its fixed Managed Identity context'
    }
    Assert-NonElevated
    $request = Parse-ReceiverOperation -Command ([string]$env:SSH_ORIGINAL_COMMAND)
    switch ($request.operation) {
        'capabilities' { Write-Capabilities; exit 0 }
        'diagnostics' { Write-Diagnostics; exit 0 }
        'play' {
            $ffplay = Resolve-FFplay
            & $ffplay '-hide_banner' '-loglevel' 'warning' '-nodisp' '-autoexit' '-fflags' 'nobuffer' '-flags' 'low_delay' '-sync' 'ext' '-f' 'ogg' '-'
            exit $LASTEXITCODE
        }
        'quiet-test' { Invoke-QuietTest -Dbfs ([int]$request.dbfs); exit 0 }
        'remove' { Invoke-SelfRemoval; exit 0 }
        default { throw 'operation is not implemented' }
    }
}
catch {
    Write-ProtocolError -Message $_.Exception.Message
    exit 64
}
