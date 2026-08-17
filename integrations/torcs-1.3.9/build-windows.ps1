<#
.SYNOPSIS
    Build the pinned TORCS 1.3.9 overlay and the Apex desktop app for Windows,
    then package both as one per-user study installer.

.DESCRIPTION
    This is the Windows counterpart of build.sh. It uses the Visual Studio
    solution that ships inside the verified archive (TORCS.sln, with prebuilt
    Win64 dependency libraries under src/windows/lib64 and dll64) rather than
    autotools, and it stages the result so that Apex.exe finds the simulator at
    the packaged location default_torcs_binary() looks for.

    The granite_bridge robot is deliberately excluded: its UDP socket code is
    POSIX-only and the live-coach path is not part of the study workflow.

.PARAMETER Action
    prepare  verify, extract, and patch the source only
    build    also compile the solution and populate the TORCS runtime
    stage    also build Apex and assemble the installer payload
    install  also compile the NSIS installer (default)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File integrations\torcs-1.3.9\build-windows.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('prepare', 'build', 'stage', 'install')]
    [string]$Action = 'install',
    [string]$Archive,
    [string]$BuildRoot,
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release',
    [ValidateSet('x64', 'Win32')]
    [string]$Platform = 'x64',
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$ExpectedSha256 = 'f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c'

if (-not $Archive) { $Archive = Join-Path $RepoRoot 'torcs-1.3.9.tar.bz2' }
if (-not $BuildRoot) { $BuildRoot = Join-Path $env:TEMP 'apex-torcs-win' }

$SourceDir = Join-Path $BuildRoot 'torcs-1.3.9'
$RuntimeDirName = if ($Configuration -eq 'Debug') { 'runtimed' } else { 'runtime' }
$RuntimeDir = Join-Path $SourceDir $RuntimeDirName
$StageDir = Join-Path $BuildRoot 'stage'
$OutputDir = Join-Path $BuildRoot 'dist'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

function Copy-Tree($source, $destination) {
    # robocopy rather than Copy-Item: merging a source tree into an existing
    # destination tree is exactly what it is for, and Copy-Item -Recurse can
    # nest the source directory instead of merging it.
    & robocopy $source $destination /E /NFL /NDL /NJH /NJS /NP | Out-Null
    # robocopy signals work done through its exit code: 0 means nothing needed
    # copying, 1-7 are successful combinations, 8 and above are real failures.
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy '$source' -> '$destination' failed with exit code $LASTEXITCODE"
    }
    $global:LASTEXITCODE = 0
}

function Resolve-Tool($name, [string[]]$candidates, $hint) {
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "$name not found. $hint"
}

function Resolve-MSBuild {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $path = & $vswhere -latest -products '*' `
            -requires Microsoft.Component.MSBuild `
            -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
        if ($path) { return $path }
    }
    return Resolve-Tool 'msbuild' @() 'Install Visual Studio 2022 with the C++ desktop workload.'
}

# --- 1. Verify the pinned archive ------------------------------------------

if (-not (Test-Path -LiteralPath $Archive)) {
    throw "TORCS archive not found: $Archive"
}
Write-Step "Verifying $Archive"
$actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $ExpectedSha256) {
    throw "Refusing unexpected TORCS archive.`n  Expected: $ExpectedSha256`n  Actual:   $actual"
}

# --- 2. Extract -------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$extractMarker = Join-Path $SourceDir '.apex-archive-complete'
if (-not (Test-Path -LiteralPath $extractMarker)) {
    Write-Step "Extracting the verified archive into $BuildRoot"
    # bsdtar ships with Windows 10 1803 and later and reads bz2 directly.
    & tar -xjf $Archive -C $BuildRoot
    if ($LASTEXITCODE -ne 0) { throw "tar failed with exit code $LASTEXITCODE" }
    New-Item -ItemType File -Path $extractMarker -Force | Out-Null
}

# --- 3. Apply the overlay and patches --------------------------------------

Write-Step 'Applying the Apex source overlay'
Copy-Tree (Join-Path $ScriptDir 'overlay') $SourceDir

function Resolve-Patch {
    # Deliberately does NOT trust whatever `patch` sits on PATH. The GitHub
    # Windows image ships Strawberry Perl, and its bundled GNU patch 2.5.9 (2002)
    # aborts on these files with an assertion failure -- "Expression: hunk",
    # patch.c line 354 -- rather than reporting a normal error. Git for Windows
    # carries a modern GNU patch, so locate that one specifically, relative to
    # wherever git actually is.
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Git\usr\bin\patch.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Git\usr\bin\patch.exe')
    )
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        # <install>\cmd\git.exe or <install>\bin\git.exe -> <install>\usr\bin\patch.exe
        $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)
        $candidates += (Join-Path $gitRoot 'usr\bin\patch.exe')
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

$patchExe = Resolve-Patch
if ($patchExe) {
    Write-Step "Using patch: $patchExe"
} else {
    # git performed the checkout, so `git apply` is always available. It is
    # stricter than patch -- no fuzz -- which is what we want: the patches carry
    # eol=lf in .gitattributes precisely so their context matches exactly.
    Write-Step 'No Git patch.exe found; falling back to git apply'
}

function Invoke-Patch($name) {
    $patch = Join-Path $ScriptDir "patches\$name"
    if (-not (Test-Path -LiteralPath $patch)) { throw "Patch not found: $patch" }
    Write-Step "Applying $name"
    if ($patchExe) {
        & $patchExe --batch --forward --directory=$SourceDir --strip=1 --input=$patch
    } else {
        Push-Location $SourceDir
        try { & git apply --whitespace=nowarn -p1 $patch } finally { Pop-Location }
    }
    if ($LASTEXITCODE -ne 0) { throw "$name failed to apply (exit $LASTEXITCODE)" }
}

function Test-Marker($relativePath, $pattern) {
    $full = Join-Path $SourceDir $relativePath
    if (-not (Test-Path -LiteralPath $full)) { return $false }
    return [bool](Select-String -LiteralPath $full -Pattern $pattern -SimpleMatch -Quiet)
}

function Get-MarkerCount($relativePath, $pattern) {
    $full = Join-Path $SourceDir $relativePath
    if (-not (Test-Path -LiteralPath $full)) { return 0 }
    return @(Select-String -LiteralPath $full -Pattern $pattern -SimpleMatch).Count
}

# Each guard mirrors build.sh, including the expected hook counts, so the two
# build paths agree on what "already prepared" means and re-running is safe.
$humanSource = 'src\drivers\human\human.cpp'
if (-not (Test-Marker $humanSource 'ApexHumanTelemetryStart') -or
    (Get-MarkerCount $humanSource 'ApexHumanTelemetryRecord') -ne 2 -or
    -not (Test-Marker $humanSource 'ApexHumanTelemetryStop') -or
    -not (Test-Marker 'src\drivers\human\Makefile' 'apex_human_telemetry.cpp')) {
    Invoke-Patch 'human-telemetry.patch'
}
$berniwSource = 'src\drivers\berniw\berniw.cpp'
if (-not (Test-Marker $berniwSource 'ApexRobotTelemetryStart') -or
    (Get-MarkerCount $berniwSource 'ApexRobotTelemetryRecord') -ne 1 -or
    -not (Test-Marker $berniwSource 'ApexRobotTelemetryStop') -or
    -not (Test-Marker 'src\drivers\berniw\Makefile' 'apex_robot_telemetry.cpp')) {
    Invoke-Patch 'berniw-telemetry.patch'
}
if (-not (Test-Marker 'src\libs\raceengineclient\raceinit.cpp' 'ReRunRaceOnGUI')) {
    Invoke-Patch 'graphical-race.patch'
}
if (-not (Test-Marker 'src\libs\tgfclient\screen.cpp' 'GfScrWidth = xw;')) {
    Invoke-Patch 'screen-size-init.patch'
}
# Windows-only: -R graphical preset entry and the per-session profile directory.
if (-not (Test-Marker 'src\windows\main.cpp' 'setApexLocalDir')) {
    Invoke-Patch 'windows-graphical-race.patch'
}
# Windows-only: the recorder sources are registered in the GNU makefiles by the
# patches above, and in the Visual Studio projects by this one.
if (-not (Test-Marker 'src\drivers\human\human.vcxproj' 'apex_human_telemetry.cpp')) {
    Invoke-Patch 'windows-vcxproj-telemetry.patch'
}

if ($Action -eq 'prepare') {
    Write-Host "Prepared source: $SourceDir"
    exit 0
}

# --- 4. Export headers, stage data, then compile ----------------------------

# This has to happen BEFORE msbuild, not after. setup_win32_generic.bat is not
# only the data-install step its name suggests: it publishes every public header
# into export/include (82 copies, including the SOLID/3D tree) and creates
# export/lib, which is exactly what the .vcxproj files put on their include and
# library paths. It reads nothing from a build directory, so it is purely a
# prepare step -- running it afterwards leaves the compile with no headers and
# fails with a wall of C1083.
Write-Step 'Exporting headers and staging the TORCS runtime data'
Push-Location $SourceDir
try {
    & cmd /c "setup_win32_generic.bat $RuntimeDirName"
    if ($LASTEXITCODE -ne 0) { throw "setup_win32_generic.bat failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

$exportedHeader = Join-Path $SourceDir 'export\include\car.h'
if (-not (Test-Path -LiteralPath $exportedHeader)) {
    throw "Header export did not produce $exportedHeader; the compile would fail with C1083."
}

$msbuild = Resolve-MSBuild
Write-Step "Building TORCS.sln ($Configuration|$Platform) with $msbuild"
# Keep a full-verbosity log beside the build: the console stays readable at
# minimal, but a first failure on an unfamiliar machine needs the detail.
$msbuildLog = Join-Path $BuildRoot 'msbuild.log'
& $msbuild (Join-Path $SourceDir 'TORCS.sln') `
    "/p:Configuration=$Configuration" "/p:Platform=$Platform" `
    '/m' '/nologo' '/verbosity:minimal' `
    '/fileLogger' "/fileLoggerParameters:LogFile=$msbuildLog;Verbosity=normal;Encoding=UTF-8"
if ($LASTEXITCODE -ne 0) {
    throw "msbuild failed with exit code $LASTEXITCODE. Full log: $msbuildLog"
}

# setup_win32_generic.bat copies a fixed list of stock race managers, so the
# two Apex presets are installed here instead of patching that 1000-line file.
Write-Step 'Installing the Apex race-manager presets'
$racemanDir = Join-Path $RuntimeDir 'config\raceman'
New-Item -ItemType Directory -Force -Path $racemanDir | Out-Null
foreach ($preset in @('apexstudy.xml', 'apexrobotstudy.xml')) {
    $source = Join-Path $SourceDir "src\raceman\$preset"
    if (-not (Test-Path -LiteralPath $source)) { throw "Overlay preset missing: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $racemanDir $preset) -Force
}

$exe = Join-Path $RuntimeDir 'wtorcs.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Expected simulator not built: $exe" }
foreach ($module in @('human', 'berniw')) {
    $dll = Join-Path $RuntimeDir "drivers\$module\$module.dll"
    if (-not (Test-Path -LiteralPath $dll)) { throw "Expected driver module not built: $dll" }
}
Write-Host "TORCS runtime ready: $RuntimeDir"

if ($Action -eq 'build') { exit 0 }

# --- 5. Build Apex and stage the payload -----------------------------------

Write-Step 'Building the Apex desktop application'
Push-Location $RepoRoot
try {
    & $Python -m pip install --disable-pip-version-check -e '.[dev,package]'
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
    & $Python -m PyInstaller --noconfirm --distpath (Join-Path $BuildRoot 'apex-dist') `
        --workpath (Join-Path $BuildRoot 'apex-work') 'apex.spec'
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

$apexDist = Join-Path $BuildRoot 'apex-dist\Apex'
if (-not (Test-Path -LiteralPath (Join-Path $apexDist 'Apex.exe'))) {
    throw "Expected Apex.exe under $apexDist"
}

Write-Step "Staging the installer payload in $StageDir"
if (Test-Path -LiteralPath $StageDir) { Remove-Item -LiteralPath $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
Copy-Tree $apexDist $StageDir
# default_torcs_binary() looks for <Apex.exe dir>\torcs-runtime\wtorcs.exe.
Copy-Tree $RuntimeDir (Join-Path $StageDir 'torcs-runtime')

$stagedExe = Join-Path $StageDir 'torcs-runtime\wtorcs.exe'
if (-not (Test-Path -LiteralPath $stagedExe)) { throw "Staging did not produce $stagedExe" }
Write-Host "Payload staged: $StageDir"

if ($Action -eq 'stage') { exit 0 }

# --- 6. Compile the installer ----------------------------------------------

$makensis = Resolve-Tool 'makensis' @(
    (Join-Path $env:ProgramFiles 'NSIS\makensis.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe')
) 'Install NSIS from https://nsis.sourceforge.io or "winget install NSIS.NSIS".'

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$installer = Join-Path $OutputDir 'ApexStudySetup.exe'
Write-Step "Compiling $installer"
& $makensis "/DPAYLOAD_DIR=$StageDir" "/DOUTPUT_FILE=$installer" `
    (Join-Path $ScriptDir 'installer\apexstudy.nsi')
if ($LASTEXITCODE -ne 0) { throw "makensis failed with exit code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $installer)) { throw "makensis produced no installer" }

$size = [math]::Round((Get-Item -LiteralPath $installer).Length / 1MB, 1)
Write-Host ""
Write-Host "Installer ready: $installer ($size MB)" -ForegroundColor Green
