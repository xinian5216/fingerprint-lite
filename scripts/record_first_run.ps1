# Run on a Windows desktop, with the exact ZIP from the WIP build artifact.
# This records a real interactive wizard; it never bypasses setup or deletes data.
param(
    [Parameter(Mandatory = $true)][string]$PortableZip,
    [int]$Port = 18080,
    [int]$SetupTimeoutSeconds = 1200
)
$ErrorActionPreference = 'Stop'
foreach ($key in @('CPM_DATA_DIR', 'CPM_BROWSER_DIR', 'CPM_TEMP_DIR', 'CPM_DB_PATH')) {
    if ([Environment]::GetEnvironmentVariable($key)) {
        throw "Use a clean PowerShell session without $key for this fresh-install test."
    }
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied; choose another -Port."
}
$archive = (Resolve-Path $PortableZip).Path
$caseRoot = Join-Path (Get-Location) ('.work/first-run-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $caseRoot | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath "$caseRoot/unpacked"
$entries = @(Get-ChildItem "$caseRoot/unpacked" -Recurse -File -Filter FingerprintLite.exe)
if ($entries.Count -ne 1) { throw 'The ZIP must contain exactly one FingerprintLite.exe.' }
$exe = $entries[0].FullName
$programRoot = $entries[0].DirectoryName
if ((Test-Path "$programRoot/paths.env") -or (Test-Path "$programRoot/Data/profiles.db")) {
    throw 'Not a fresh portable ZIP: paths.env or profiles.db is already present.'
}

function Read-RunLogs {
    $dataRoot = Join-Path $programRoot 'Data'
    if (Test-Path "$programRoot/paths.env") {
        $line = Get-Content "$programRoot/paths.env" | Where-Object { $_ -match '^CPM_DATA_DIR=' } | Select-Object -First 1
        if ($line) {
            $chosen = ($line -replace '^CPM_DATA_DIR=', '').Trim('"')
            $dataRoot = if ([IO.Path]::IsPathRooted($chosen)) { $chosen } else { Join-Path $programRoot $chosen }
        }
    }
    $script:chosenData = $dataRoot
    $files = @("$programRoot/Data/logs/console.log", "$dataRoot/logs/console.log") | Select-Object -Unique
    return (($files | Where-Object { Test-Path $_ } | ForEach-Object { Get-Content $_ -Raw }) -join "`n")
}
function Sample-Run {
    $processes = @(Get-Process -Name FingerprintLite -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe })
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    @{
        time = (Get-Date).ToUniversalTime().ToString('o')
        processes = @($processes | Select-Object Id, MainWindowHandle, MainWindowTitle)
        listeners = @($listeners | Select-Object LocalPort, OwningProcess)
        paths_env = (Test-Path "$programRoot/paths.env")
    } | ConvertTo-Json -Depth 5 -Compress | Add-Content "$caseRoot/processes.jsonl" -Encoding utf8
    return $processes
}

Write-Host "Fresh test copy: $programRoot"
Write-Host 'Complete the actual Wizard, including the verified browser install. Keep the manager open.'
$parent = Start-Process -FilePath $exe -WorkingDirectory $programRoot -ArgumentList @('--port', "$Port") -PassThru
$deadline = (Get-Date).AddSeconds($SetupTimeoutSeconds)
$stableSince = $null
$managerId = $null
$candidate = $false
while ((Get-Date) -lt $deadline) {
    $processes = @(Sample-Run)
    $logs = Read-RunLogs
    $manager = $processes | Where-Object { $_.Id -ne $parent.Id -and $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -eq 'Fingerprint Lite' } | Select-Object -First 1
    $parent.Refresh()
    $healthy = $false
    if ($manager -and $parent.HasExited -and $parent.ExitCode -eq 0 -and
        $logs -match "parent_pid=$($parent.Id) child_pid=$($manager.Id)" -and
        $logs -match "Startup pid=$($manager.Id): wizard skipped") {
        $owned = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $manager.Id }
        if ($owned) {
            try {
                $page = Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
                $healthy = $page.StatusCode -eq 200
            } catch { $healthy = $false }
        }
    }
    if ($healthy) {
        if ($managerId -ne $manager.Id) { $stableSince = Get-Date; $managerId = $manager.Id }
        if (((Get-Date) - $stableSince).TotalSeconds -ge 10) { $candidate = $true; break }
    } else { $stableSince = $null; $managerId = $null }
    Start-Sleep -Seconds 1
}

$observed = 'no'
$cleanup = $false
if ($candidate) {
    Write-Host 'A fresh manager PID, skipped wizard, visible window and owned HTTP port stayed healthy for 10 seconds.'
    $observed = Read-Host 'Did the actual Wizard finish and the manager render and work normally? Type yes only after checking'
    Write-Host 'Close the manager normally now. Recording shutdown for up to 60 seconds.'
    $closeDeadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $closeDeadline) {
        $remaining = @(Sample-Run)
        $listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0 -and $listening.Count -eq 0 -and -not (Test-Path "$chosenData/instance.lock")) {
            $cleanup = $true; break
        }
        Start-Sleep -Seconds 1
    }
}
$logs = Read-RunLogs
$logs | Set-Content "$caseRoot/console-combined.log" -Encoding utf8
$result = @{
    zip_sha256 = (Get-FileHash $archive -Algorithm SHA256).Hash
    parent_pid = $parent.Id
    manager_pid = $managerId
    stable_handoff = $candidate
    user_confirmed_manager = ($observed.Trim().ToLowerInvariant() -eq 'yes')
    clean_shutdown = $cleanup
    status = 'FAILED_OR_INCOMPLETE'
}
if ($candidate -and $result.user_confirmed_manager -and $cleanup) { $result.status = 'OBSERVED_PASS' }
$result | ConvertTo-Json | Set-Content "$caseRoot/result.json" -Encoding utf8
Write-Host "Result: $($result.status). Evidence: $caseRoot"
Write-Host 'All test data and logs are preserved. No process was force-killed. Review logs for private paths before sharing.'
if ($result.status -ne 'OBSERVED_PASS') { exit 1 }
