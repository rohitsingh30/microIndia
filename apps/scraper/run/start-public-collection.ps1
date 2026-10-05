[CmdletBinding()]
param(
    [string]$CandidateSource = "",
    [switch]$Background
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($CandidateSource)) {
    $CandidateSource = [Environment]::GetEnvironmentVariable("MICROINDIA_PUBLIC_CANDIDATE_SOURCE")
}

if ([string]::IsNullOrWhiteSpace($CandidateSource)) {
    Write-Warning "No approved public candidate source configured; collector will remain paused."
    $CandidateSource = ""
} else {
    if (-not [System.IO.Path]::IsPathRooted($CandidateSource)) {
        $CandidateSource = Join-Path $Root $CandidateSource
    }
    if (-not (Test-Path -LiteralPath $CandidateSource -PathType Leaf)) {
        Write-Warning "Candidate source not found; collector will remain paused: $CandidateSource"
        $CandidateSource = ""
    } else {
        $CandidateSource = (Resolve-Path -LiteralPath $CandidateSource).Path
    }
}

# The dispatcher consumes only validated public NDJSON records. This launcher
# never substitutes Reels, Home, hashtag, or feed discovery when the source is absent.
$env:MICROINDIA_PUBLIC_CANDIDATE_SOURCE = $CandidateSource
$PathSeparator = [System.IO.Path]::PathSeparator
$env:PYTHONPATH = "$Root/src" + $(if ($env:PYTHONPATH) { "$PathSeparator$($env:PYTHONPATH)" } else { "" })

$Python = Join-Path $Root ".venv/bin/python"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = Join-Path $Root ".venv/Scripts/python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project virtualenv Python was not found under $Root/.venv"
}

$Arguments = @(
    "-m", "microindia_scraper.local_supervisor",
    "--config", "run/local-workers.json",
    "--state", "run/local-supervisor-state.json",
    "--lock", "run/local-supervisor.lock"
)

Write-Host "Starting collection supervisor (source: '$CandidateSource', target: 1000000)."
if ($Background) {
    $LogFile = if ($env:MICROINDIA_SUPERVISOR_LOG) { $env:MICROINDIA_SUPERVISOR_LOG } else { "run/public-collection-supervisor.log" }
    $ErrorLogFile = "$LogFile.error"
    $Process = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $Root -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLogFile -PassThru
    Write-Host "Started collection supervisor (pid $($Process.Id))."
    Write-Host "Status: PYTHONPATH=src .venv/bin/python -m microindia_scraper.local_supervisor --state run/local-supervisor-state.json --status"
} else {
    & $Python @Arguments
    exit $LASTEXITCODE
}
