# Run on a clean Windows test account. The real installer registers its normal app ID.
param(
    [Parameter(Mandatory)][string]$Baseline,
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$CandidateVersion
)
$ErrorActionPreference = 'Stop'
$scratch = Join-Path $env:RUNNER_TEMP ("SimpleCAD-drill-" + [guid]::NewGuid())
$install = Join-Path $scratch 'Application'
$model = Join-Path $scratch 'Model.scad3'
$prefs = Join-Path $scratch 'settings.json'
$env:SIMPLECAD_USER_DATA = Join-Path $scratch 'Electron settings'
$env:SIMPLECAD_LEGACY_SETTINGS = $prefs
$env:QT_QPA_PLATFORM = 'offscreen'

function Run([string]$Executable, [string[]]$Arguments) {
    $out = Join-Path $scratch 'stdout'
    $err = Join-Path $scratch 'stderr'
    $p = Start-Process $Executable -Wait -PassThru -ArgumentList $Arguments -RedirectStandardOutput $out -RedirectStandardError $err
    if ($p.ExitCode -ne 0) {
        if (Test-Path $err) { Get-Content $err }
        throw "$Executable exited $($p.ExitCode)."
    }
    Get-Content $out
}

function Install([string]$Package) {
    Run $Package @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/DIR="{0}"' -f $install))
}

function Check([string]$Version) {
    $program = Join-Path $install 'SimpleCAD.exe'
    $versionOutput = Run $program @('--version')
    if (($versionOutput -join ' ') -notmatch ('\b' + [regex]::Escape($Version) + '\s*$')) {
        throw "Expected SimpleCAD $Version, got $versionOutput."
    }
    Run $program @('--self-test')
    $association = (Get-Item 'Registry::HKEY_CURRENT_USER\Software\Classes\SimpleCAD.Project\shell\open\command').GetValue('')
    if ($association -ne ('"{0}" "%1"' -f $program)) { throw "Wrong .scad3 open command: $association" }
    if ((Get-Item 'Registry::HKEY_CURRENT_USER\Software\Classes\.scad3').GetValue('') -ne 'SimpleCAD.Project') {
        throw 'The installer did not register .scad3.'
    }
    foreach ($item in $protected) {
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $item.Path).Hash -ne $item.Hash) {
            throw "The installer altered $($item.Path)."
        }
    }
}

New-Item -ItemType Directory -Path $scratch | Out-Null
try {
    Copy-Item (Join-Path $PSScriptRoot '../app/tests/e2e/fixtures/features.scad3') $model
    '{"theme":"dark"}' | Set-Content -Encoding utf8 $prefs
    $protected = Get-FileHash -Algorithm SHA256 -LiteralPath @($model, $prefs)
    Install $Baseline
    Check '0.4.4'
    Install $Candidate
    Check $CandidateVersion
    if (-not (Test-Path (Join-Path $install 'resources/app.asar'))) { throw 'Electron was not installed.' }
    Install $Baseline
    Check '0.4.4'
    Write-Output 'Install, upgrade and rollback passed on Windows.'
} finally {
    $uninstaller = Join-Path $install 'unins000.exe'
    if (Test-Path $uninstaller) { Run $uninstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') }
    Remove-Item $scratch -Recurse
}
