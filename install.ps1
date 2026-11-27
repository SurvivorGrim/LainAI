<#
  Lain AI installer for Windows.
    Double-click install.bat   (or:  powershell -ExecutionPolicy Bypass -File install.ps1)
    Options:  -Drive E:\   -Models all|recommended|none|best,mid,laptop,alt   -Yes   -NoDeps   -Format   -RestoreVault C:\path\to\backup
    -Format preselects "format the drive" (you still type ERASE). Without it, a drive that is already
    usable (exFAT/NTFS with a drive letter) is never formatted unless you choose to.
  What it does:
    1. installs what Lain needs on this PC (Microsoft Visual C++ runtime), checks GPU drivers
    2. finds removable drives, lets you pick one (offers to format it exFAT for Windows + Linux)
    3. downloads a portable Python and hands over to installer\lain_install.py, which downloads the
       engines, encryption tool and models onto the drive with progress for every file.
#>
param(
    [string]$Drive = "",
    [string]$Models = "",
    [switch]$Yes,
    [switch]$NoDeps,
    [switch]$Force,
    [switch]$Format,
    [string]$RestoreVault = ""
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Invoke-WebRequest is very slow with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PyTag  = '20261123'; $PyVer = '3.13.18'          # keep in sync with installer\lain_install.py
$PyFile = "cpython-$PyVer+$PyTag-x86_64-pc-windows-msvc-install_only.tar.gz"
$PySha  = '15ed701bae38157e36fbbc9f1cfcb144dd2b49868e962a055235a3d72fa30212'
$PyUrl  = "https://github.com/astral-sh/python-build-standalone/releases/download/$PyTag/" + $PyFile.Replace('+', '%2B')

$script:StepN = 0
function Step($t) { $script:StepN++; Write-Host ""; Write-Host "━━ [setup $($script:StepN)/4] $t" -ForegroundColor White }
function Info($t) { Write-Host "  $t" -ForegroundColor DarkGray }
function Ok($t)   { Write-Host "  ✓ $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  ! $t" -ForegroundColor Yellow }
function Die($t)  { Write-Host "  ✗ $t" -ForegroundColor Red; Read-Host "  Press Enter to close"; exit 1 }
function Ask($q, $def) {
    if ($Yes) { Info "$q $def (automatic)"; return $def }
    $a = Read-Host "  $q"
    if ([string]::IsNullOrWhiteSpace($a)) { return $def } else { return $a.Trim() }
}
function YesNo($q, $def) { return ((Ask $q $def) -match '^[yY]') }
function IsAdmin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
function Human([double]$b) { foreach ($u in 'B','KB','MB','GB','TB') { if ($b -lt 1024 -or $u -eq 'TB') { return ('{0:N1} {1}' -f $b, $u) }; $b /= 1024 } }
function Get-Fs($v) {
    # Filesystem name of a Get-Volume result. Use the FileSystem string: FileSystemType is an enum
    # that has no exFAT value on some Windows builds and reports exFAT volumes as 'Unknown'.
    if (-not $v) { return '' }
    if ($v.FileSystem) { return "$($v.FileSystem)" }
    return "$($v.FileSystemType)"
}
function RunElevated([string]$code) {
    # Runs a PowerShell snippet as administrator (one UAC prompt) and waits for it.
    if (IsAdmin) {
        try { & ([scriptblock]::Create($code)); return $true }
        catch { Write-Host "  $_" -ForegroundColor Red; return $false }   # same as the elevated path: report, don't kill the installer
    }
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("`$ErrorActionPreference='Stop'; try { $code; exit 0 } catch { Write-Host `$_ -ForegroundColor Red; Start-Sleep 8; exit 1 }"))
    try { $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$enc }
    catch { Warn "administrator permission was declined"; return $false }    # "No" on the UAC prompt throws
    return ($p.ExitCode -eq 0)
}

Write-Host ""
Write-Host "   L A I N   A I   ·   installer for Windows" -ForegroundColor Magenta
Write-Host "   portable · private · offline" -ForegroundColor DarkGray
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { Die "Lain's prebuilt engines are for 64-bit x86 PCs." }
$os = Get-CimInstance Win32_OperatingSystem
if ([int]$os.BuildNumber -lt 17763) { Die "Windows 10 version 1809 or newer is needed (this is build $($os.BuildNumber))." }

# ------------------------------------------------------------------ 1. system software
Step "Software this PC needs"
Info "$($os.Caption) build $($os.BuildNumber)"
if ($NoDeps) { Info "skipped (-NoDeps)" } else {
    # Visual C++ 2015-2022 runtime: the llama.cpp and stable-diffusion.cpp engines are built with it
    $vc = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
    if ($vc -and $vc.Installed -eq 1 -and [int]$vc.Major -ge 14 -and [int]$vc.Minor -ge 40) {
        Ok "Microsoft Visual C++ runtime $($vc.Version)"
    } else {
        Info "Microsoft Visual C++ runtime (needed by the AI engines): $(if ($vc) { "$($vc.Version) - too old" } else { 'not installed' })"
        if (YesNo "Install it now? (Windows will ask for permission) [Y/n]" 'y') {
            $done = $false
            if (Get-Command winget -ErrorAction SilentlyContinue) {
                Info "installing with winget..."
                winget install --id Microsoft.VCRedist.2015+.x64 -e --silent --accept-package-agreements --accept-source-agreements
                $done = ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189)   # 0x8A15002B = already installed
            }
            if (-not $done) {
                Info "downloading vc_redist.x64.exe from Microsoft..."
                $vcexe = Join-Path $env:TEMP 'vc_redist.x64.exe'
                Invoke-WebRequest 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vcexe -UseBasicParsing
                $sig = Get-AuthenticodeSignature $vcexe
                if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { Die "vc_redist.x64.exe signature check failed - not running it." }
                Ok "signed by Microsoft"
                $p = Start-Process $vcexe -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru
                $done = ($p.ExitCode -in 0, 1638, 3010)
                Remove-Item $vcexe -ErrorAction SilentlyContinue
            }
            if ($done) { Ok "Visual C++ runtime installed" } else { Warn "Visual C++ runtime didn't install - the engines may not start" }
        } else { Warn "skipped - the engines may not start without it" }
    }
}
foreach ($c in 'tar.exe') { if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { Die "$c is missing (it ships with Windows 10 1803+)." } }
$gpus = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Basic Display|Remote|Virtual' })
foreach ($g in $gpus) { Info "graphics: $($g.Name)  (driver $($g.DriverVersion))" }
if ($gpus | Where-Object { $_.Name -match 'NVIDIA' }) {
    $smi = Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'
    if ((Test-Path $smi) -and ((& $smi --query-gpu=driver_version --format=csv,noheader 2>$null) -match '\d')) {
        Ok "NVIDIA driver $((& $smi --query-gpu=driver_version --format=csv,noheader | Select-Object -First 1).Trim()) - CUDA engine will work"
    } else { Warn "NVIDIA card found but nvidia-smi isn't working - install the latest driver from nvidia.com/drivers for full speed" }
} elseif (-not $gpus) { Warn "no graphics card detected - Lain will run on the CPU (slower)" }

# ------------------------------------------------------------------ 2. pick the drive
Step "Choosing the drive"
function Refresh-Storage { try { Update-HostStorageCache -ErrorAction Stop } catch {} }   # Get-Disk/Get-Volume cache stale info otherwise
function Get-Candidates {
    Refresh-Storage
    $out = @()
    $disks = @(Get-Disk | Where-Object { -not $_.IsBoot -and -not $_.IsSystem -and ($_.BusType -in 'USB','SD','MMC' -or $_.IsRemovable) })
    $remVols = @(Get-Volume | Where-Object { $_.DriveType -eq 'Removable' -and $_.DriveLetter })
    foreach ($d in $disks) {
        $parts = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | Where-Object { $_.Type -notin 'Reserved','System','Recovery' })
        $vols = 0
        foreach ($p in $parts) {
            $v = $p | Get-Volume -ErrorAction SilentlyContinue
            if (-not $v -and $p.GptType -eq '{0fc63daf-8483-4772-8e79-3d69d8477de4}') {
                $vols++
                $out += [pscustomobject]@{ Disk = $d.Number; Letter = $null; Fs = 'linux-tagged'; Label = ''; Size = $p.Size; Free = 0
                    Model = $d.FriendlyName; Bus = $d.BusType; Part = $p.PartitionNumber; GptType = $p.GptType }
                continue
            }
            if (-not $v) { continue }
            $vols++
            $out += [pscustomobject]@{ Disk = $d.Number; Letter = $p.DriveLetter; Fs = (Get-Fs $v); Label = $v.FileSystemLabel
                Size = $p.Size; Free = $v.SizeRemaining; Model = $d.FriendlyName; Bus = $d.BusType; Part = $p.PartitionNumber; GptType = $p.GptType }
        }
        if ($vols -eq 0) {
            $out += [pscustomobject]@{ Disk = $d.Number; Letter = $null; Fs = 'none'; Label = ''; Size = $d.Size; Free = 0
                Model = $d.FriendlyName; Bus = $d.BusType; Part = 0; GptType = $null }
        }
    }
    foreach ($v in $remVols) {   # card readers etc. that Get-Disk didn't flag
        if ($out | Where-Object { $_.Letter -eq $v.DriveLetter }) { continue }
        $out += [pscustomobject]@{ Disk = $null; Letter = $v.DriveLetter; Fs = (Get-Fs $v); Label = $v.FileSystemLabel
            Size = $v.Size; Free = $v.SizeRemaining; Model = 'removable drive'; Bus = 'Removable'; Part = 0; GptType = $null }
    }
    return ,$out
}

function Backup-Vault($root) {
    $v = Join-Path $root 'LainAI\vault'
    if (-not (Test-Path $v)) { return $null }
    $dst = Join-Path $HOME ("LainAI-vault-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Info "this drive has a Lain vault - backing it up to $dst first"
    Copy-Item $v $dst -Recurse
    foreach ($f in Get-ChildItem $v -File) {
        if ((Get-FileHash $f.FullName).Hash -ne (Get-FileHash (Join-Path $dst $f.Name)).Hash) { Die "vault backup didn't verify - stopping, nothing was erased" }
    }
    Ok "vault backed up and verified"
    return $dst
}

$MsData = '{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}'     # "Microsoft basic data": Windows + Linux both mount it
$LinuxData = '{0fc63daf-8483-4772-8e79-3d69d8477de4}'  # "Linux filesystem": Windows hides it

function Show-Contents($d) {
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────────────────────┐" -ForegroundColor Red
    Write-Host "  │  WARNING: formatting ERASES EVERYTHING on this drive.        │" -ForegroundColor Red
    Write-Host "  │  It can't be undone. Back up anything you want to keep.      │" -ForegroundColor Red
    Write-Host "  └──────────────────────────────────────────────────────────────┘" -ForegroundColor Red
    Write-Host ("  disk {0}  ·  {1}  ·  {2}" -f $d.Number, (Human $d.Size), $d.FriendlyName) -ForegroundColor White
    foreach ($p in @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue)) {
        $v = $p | Get-Volume -ErrorAction SilentlyContinue
        Info ("    partition {0}  {1}  {2} {3}" -f $p.PartitionNumber, (Human $p.Size), $(if ($p.DriveLetter) { "$($p.DriveLetter):" } else { '' }), $(if ($v) { "$(Get-Fs $v) $($v.FileSystemLabel)" } else { '(not readable on Windows)' }))
        if (-not $p.DriveLetter) { continue }
        $root = "$($p.DriveLetter):\"
        $items = @(Get-ChildItem $root -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin 'System Volume Information', '$RECYCLE.BIN' })
        Write-Host ("      {0} in use, {1} item(s) at the top level:" -f (Human ($v.Size - $v.SizeRemaining)), $items.Count) -ForegroundColor Yellow
        $items | Select-Object -First 8 | ForEach-Object { Write-Host "        $($_.Name)" }
        if ($items.Count -gt 8) { Write-Host "        ... and $($items.Count - 8) more" }
        if (Test-Path (Join-Path $root 'LainAI\vault')) { Write-Host "      contains a Lain vault - it will be backed up to your user folder first" -ForegroundColor Yellow }
        if (Test-Path (Join-Path $root 'LainAI\models')) { Write-Host "      contains Lain models - they'd have to be downloaded again" -ForegroundColor Yellow }
    }
}

function Test-LainVolume($disk, $letter) {
    # Is the drive usable for Lain as it is right now? Looks at the biggest data partition.
    # Returns Ready/Letter/Fs/Label/Part plus Problem (why not) or Note (usable, but worth knowing).
    Refresh-Storage
    $r = [pscustomobject]@{ Ready = $false; Letter = $null; Fs = ''; Label = ''; Disk = $disk; Part = $null; Problem = ''; Note = '' }
    if ($null -ne $disk) {
        if (-not (Get-Disk -Number $disk -ErrorAction SilentlyContinue)) { $r.Problem = "disk $disk isn't there any more - was it unplugged? Go back (b) and rescan"; return $r }
        $parts = @(Get-Partition -DiskNumber $disk -ErrorAction SilentlyContinue | Where-Object { $_.Type -notin 'Reserved','System','Recovery' } | Sort-Object Size -Descending)
    } else { $parts = @(Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue) }
    $p = $parts | Select-Object -First 1
    if ($p) { $v = $p | Get-Volume -ErrorAction SilentlyContinue; $r.Letter = $p.DriveLetter; $r.Part = $p.PartitionNumber }
    elseif ($letter) { $v = Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue; $r.Letter = $letter }   # card readers
    else { $r.Problem = "it has no partition yet - it isn't formatted"; return $r }
    if ($v) { $r.Fs = Get-Fs $v; $r.Label = "$($v.FileSystemLabel)" }
    if ($r.Letter -eq [char]0) { $r.Letter = $null }
    if ($p -and $p.GptType -eq $LinuxData) { $r.Problem = "the partition is tagged 'Linux filesystem', so Windows hides it"; return $r }
    switch -Regex ($r.Fs) {
        '^exFAT$' { }
        '^NTFS$'  { $r.Note = "it's NTFS - works on Windows and most Linux, but exFAT is the safer cross-OS choice" }
        '^FAT'    { $r.Problem = "it's FAT32, which can't hold files over 4 GB (the models are bigger) - format it as exFAT"; return $r }
        '^(|RAW|Unknown)$' { $r.Problem = "it isn't formatted yet"; return $r }
        default   { $r.Problem = "it's '$($r.Fs)', which isn't usable on both Windows and Linux - format it as exFAT"; return $r }
    }
    if (-not $r.Letter) { $r.Problem = "it's formatted ($($r.Fs)) but has no drive letter yet"; return $r }
    $r.Ready = $true
    return $r
}

function Add-LainLetter($st) {
    if ($null -eq $st.Disk -or $null -eq $st.Part) { return }
    Info "giving it a drive letter (Windows will ask for administrator permission)..."
    RunElevated "Add-PartitionAccessPath -DiskNumber $($st.Disk) -PartitionNumber $($st.Part) -AssignDriveLetter" | Out-Null
    Start-Sleep 2
}

function Wait-LainReady($disk, $letter) {
    # The person formats (with our elevated script, Windows' pop-up "Format disk" window, Explorer or
    # Disk Management - any of them is fine) and tells us when it's done; then we check the drive.
    Write-Host ""
    Info "If Windows pops up its own 'Format' window, you can use it: File system exFAT, then Start."
    Info "Or format it yourself: File Explorer > right-click the drive > Format... > exFAT > Start."
    $auto = 0
    while ($true) {
        if ($Yes) { Start-Sleep 3; $auto++ } else {
            $a = Read-Host "  Press Enter when formatting has finished  (o = open Disk Management · b = back to the drive list)"
            if ($a -match '^[bB]') { return $null }
            if ($a -match '^[oO]') { Start-Process diskmgmt.msc; continue }
        }
        Info "checking the drive..."
        $st = Test-LainVolume $disk $letter
        if (-not $st.Ready -and -not $st.Letter -and $st.Fs -match '^(exFAT|NTFS)$') { Add-LainLetter $st; $st = Test-LainVolume $disk $letter }
        if ($st.Ready) {
            if ($st.Note) { Warn $st.Note }
            Ok "drive is ready: $($st.Letter):  $($st.Fs)  '$($st.Label)'$(if ($st.Fs -eq 'exFAT') { ' - readable on Windows, Linux and macOS' })"
            return "$($st.Letter):\"
        }
        Warn "not ready yet: $($st.Problem)"
        if ($Yes -and $auto -ge 40) { Warn "gave up waiting after 2 minutes"; return $null }
    }
}

function Format-LainDisk($c) {
    if ($null -eq $c.Disk) {
        # not a disk Windows lets us partition (card reader etc.) - the person formats it with Explorer
        Write-Host ""
        Write-Host "  Formatting ERASES EVERYTHING on $($c.Letter):  - back up anything you want to keep." -ForegroundColor Red
        if ($c.Letter) { $b = Backup-Vault "$($c.Letter):\"; if ($b) { $script:VaultBackup = $b } }
        Info "Format it in File Explorer: right-click $($c.Letter): > Format... > File system exFAT > Start."
        return (Wait-LainReady $null $c.Letter)
    }
    $d = Get-Disk -Number $c.Disk
    if ($d.IsBoot -or $d.IsSystem) { Die "refusing: that's the disk Windows runs from" }
    Show-Contents $d
    Write-Host ""
    $a = Read-Host "  Type ERASE (capitals) to format disk $($d.Number) as exFAT, anything else cancels"
    if ($a -cne 'ERASE') { Info "cancelled - nothing was changed"; return $null }
    foreach ($p in @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | Where-Object DriveLetter)) {
        $b = Backup-Vault "$($p.DriveLetter):\"; if ($b) { $script:VaultBackup = $b }
    }
    $n = $d.Number
    # The partition is created WITHOUT a drive letter and formatted before it gets one, so Windows
    # usually doesn't pop up its "You need to format the disk" window in the middle of it.
    $code = @"
`$d = Get-Disk -Number $n
if (`$d.IsOffline) { Set-Disk -Number $n -IsOffline `$false }
if (`$d.IsReadOnly) { Set-Disk -Number $n -IsReadOnly `$false }
if (`$d.PartitionStyle -ne 'RAW') { Clear-Disk -Number $n -RemoveData -RemoveOEM -Confirm:`$false }
`$d = Get-Disk -Number $n
if (`$d.PartitionStyle -eq 'RAW') { Initialize-Disk -Number $n -PartitionStyle GPT }
elseif (`$d.PartitionStyle -ne 'GPT') { Set-Disk -Number $n -PartitionStyle GPT }
`$p = New-Partition -DiskNumber $n -UseMaximumSize -GptType '$MsData'
Format-Volume -Partition `$p -FileSystem exFAT -NewFileSystemLabel LAINAI -Confirm:`$false | Out-Null
Start-Sleep 2
`$p = Get-Partition -DiskNumber $n -PartitionNumber `$p.PartitionNumber
if (-not `$p.DriveLetter) { Add-PartitionAccessPath -DiskNumber $n -PartitionNumber `$p.PartitionNumber -AssignDriveLetter }
"@
    Info "erasing and formatting (Windows will ask for administrator permission)..."
    if (RunElevated $code) {
        Start-Sleep 2
        $st = Test-LainVolume $n $null
        if ($st.Ready -and $st.Fs -eq 'exFAT') {
            Ok "formatted and verified: $($st.Letter):  exFAT  '$($st.Label)' - readable on Windows, Linux and macOS"
            Info "(if a Windows 'Format' window popped up, just close it)"
            return "$($st.Letter):\"
        }
        Info "the format ran, but the drive isn't showing up as ready yet ($($st.Problem))"
    } else { Warn "the installer's format didn't finish - you can format it with Windows instead" }
    return (Wait-LainReady $n $null)
}

function Fix-LainTag($c) {
    # Partition tagged "Linux filesystem": retag it so Windows mounts it. Files are not touched.
    $code = "Set-Partition -DiskNumber $($c.Disk) -PartitionNumber $($c.Part) -GptType '$MsData'; Start-Sleep 2; " +
            "if (-not (Get-Partition -DiskNumber $($c.Disk) -PartitionNumber $($c.Part)).DriveLetter) { Add-PartitionAccessPath -DiskNumber $($c.Disk) -PartitionNumber $($c.Part) -AssignDriveLetter }"
    Info "changing the partition type (Windows will ask for administrator permission)..."
    if (-not (RunElevated $code)) { Warn "couldn't change it"; return $null }
    Start-Sleep 2
    $p = Get-Partition -DiskNumber $c.Disk -PartitionNumber $c.Part
    $v = $p | Get-Volume -ErrorAction SilentlyContinue
    if ($p.DriveLetter -and $v -and (Get-Fs $v) -in 'exFAT', 'NTFS') {
        Ok "partition is now 'Microsoft basic data': $($p.DriveLetter): $(Get-Fs $v) - files untouched"
        return "$($p.DriveLetter):\"
    }
    Warn "it isn't exFAT/NTFS underneath (probably a Linux filesystem) - putting the tag back; it has to be formatted"
    RunElevated "Set-Partition -DiskNumber $($c.Disk) -PartitionNumber $($c.Part) -GptType '$LinuxData'" | Out-Null
    return $null
}

$script:VaultBackup = $null
$Fs = ''
if ($Drive) {
    if (-not (Test-Path $Drive)) { Die "not found: $Drive" }
    $Drive = (Resolve-Path $Drive).Path
    if ($Format) {
        # -Drive + -Format: format that drive (still asks for ERASE) instead of silently skipping it
        $dp = Get-Partition -DriveLetter $Drive[0] -ErrorAction SilentlyContinue
        $c = [pscustomobject]@{ Disk = $(if ($dp) { $dp.DiskNumber } else { $null }); Letter = $Drive[0] }
        $t = Format-LainDisk $c
        if (-not $t) { Die "the drive wasn't formatted - nothing was installed" }
        $Drive = $t
    }
    try { $Fs = Get-Fs (Get-Volume -DriveLetter $Drive[0] -ErrorAction Stop) } catch {}
} else {
    while ($true) {
        $cands = Get-Candidates
        if ($cands.Count -eq 0) {
            Warn "no removable drives found. Plug one in and press Enter (or type a drive like E:\, q to quit)."
        } else {
            Info "removable drives:"
            for ($i = 0; $i -lt $cands.Count; $i++) {
                $c = $cands[$i]
                $l = if ($c.Letter) { "$($c.Letter):" } else { '--' }
                Write-Host ("   {0}) {1,-3} {2,9}  {3,-6} {4,-12} {5} ({6}){7}" -f ($i + 1), $l, (Human $c.Size), $c.Fs, $c.Label, $c.Model, $c.Bus,
                    $(if ($c.Letter) { "  $(Human $c.Free) free" } else { '  no drive letter' })) -ForegroundColor White
            }
            Info "   r) rescan   ·   a path like E:\) use that   ·   q) quit"
        }
        $a = Ask "Install Lain on which drive? [$(if ($cands.Count) { 1 } else { 'r' })]" $(if ($cands.Count) { '1' } else { 'r' })
        if ($a -eq 'q') { exit 0 }
        if ($a -eq 'r') { continue }
        if ($a -match '^[a-zA-Z]:\\?$|^\\\\') { if (Test-Path $a) { $Drive = $a; break } else { Warn "not found: $a"; continue } }
        if (-not ($a -match '^\d+$') -or [int]$a -lt 1 -or [int]$a -gt $cands.Count) { Warn "pick a number from the list"; continue }
        $c = $cands[[int]$a - 1]
        $Fs = "$($c.Fs)"
        $target = if ($c.Letter) { "$($c.Letter):\" } else { $null }
        $hasLain = $target -and (Test-Path (Join-Path $target 'LainAI'))

        # Already good to go? Then don't format - just confirm (formatting stays available).
        if ($Fs -eq 'exFAT' -and $target -and -not $Format) {
            Ok "exFAT with a drive letter - already good to go on Windows, Linux and macOS, no formatting needed"
            if ($hasLain) { Info "Lain is already on it - this updates it; your vault and downloaded models stay" }
            else { Info "files already on it are kept" }
            $choice = Ask "Install onto $target ? [Y]   (f = format it anyway · b = back to the drive list)" 'y'
            if ($choice -match '^[yY]') { $Drive = $target; break }
            if ($choice -notmatch '^[fF]') { continue }
            $target = Format-LainDisk $c
            if (-not $target) { Warn "no usable drive yet - pick again"; continue }
            $Fs = Get-Fs (Get-Volume -DriveLetter $target[0] -ErrorAction SilentlyContinue)
            $Drive = $target; break
        }

        $keep = $true; $keepLabel = 'Keep the files and install onto it as it is'
        switch -Regex ($Fs) {
            '^exFAT$'   { if (-not $target) { Warn "exFAT, but it has no drive letter"; $keepLabel = 'Give it a drive letter and keep the files' }
                          else { Ok "exFAT - already works on Windows, Linux and macOS" } }
            '^NTFS$'    { Warn "NTFS - works on Windows and most Linux, but exFAT is the safer cross-OS choice"
                          if (-not $target) { $keepLabel = 'Give it a drive letter and keep the files' } }
            '^FAT'      { Warn "FAT32 - can't store files over 4 GB and the models are bigger: it has to be formatted"; $keep = $false }
            '^linux-tagged$' { Warn "partition is tagged 'Linux filesystem', so Windows hides it (it may be exFAT underneath)"
                          $keepLabel = 'Fix the partition tag and keep the files (works if it is exFAT underneath)' }
            default     { Warn "'$Fs' - not usable on Windows + Linux as it is: it has to be formatted"; $keep = $false }
        }
        Write-Host ""
        Write-Host "   1) Format as exFAT for Windows + Linux  " -NoNewline -ForegroundColor White; Write-Host "(ERASES everything on the drive)" -ForegroundColor Red
        $def = '1'
        if ($keep) {
            Write-Host "   2) $keepLabel" -ForegroundColor White
            if (-not $Format) { $def = '2' }     # usable as it is: never default to erasing it
        }
        Write-Host "   b) back to the drive list" -ForegroundColor White
        $choice = Ask "Choose [$def]" $def
        if ($choice -eq '1') { $target = Format-LainDisk $c }
        elseif ($choice -eq '2' -and $keep) {
            if ($Fs -eq 'linux-tagged') { $target = Fix-LainTag $c }
            elseif (-not $target) {
                Add-LainLetter ([pscustomobject]@{ Disk = $c.Disk; Part = $c.Part })
                $l = (Get-Partition -DiskNumber $c.Disk -PartitionNumber $c.Part -ErrorAction SilentlyContinue).DriveLetter
                $target = if ($l -and $l -ne [char]0) { "${l}:\" } else { Warn "it still has no drive letter - try unplugging and replugging it"; $null }
            }
        } else { continue }
        if ($target) { $Fs = Get-Fs (Get-Volume -DriveLetter $target[0] -ErrorAction SilentlyContinue) }
        if (-not $target) { Warn "no usable drive letter - pick again"; continue }
        $Drive = $target; break
    }
}
if (-not $Drive.EndsWith('\')) { $Drive += '\' }
$free = (Get-PSDrive -Name $Drive[0] -ErrorAction SilentlyContinue).Free
Ok "installing to: $Drive  ($(Human $free) free, $(if ($Fs) { $Fs } else { 'unknown filesystem' }))"

# ------------------------------------------------------------------ 3. portable Python for the installer
Step "Installer runtime (portable Python $PyVer, nothing installed system-wide)"
$cache = Join-Path $env:LOCALAPPDATA 'LainAI-installer'
New-Item -ItemType Directory -Force $cache | Out-Null
$arch = Join-Path $cache $PyFile
if ((Test-Path $arch) -and (Get-FileHash $arch -Algorithm SHA256).Hash -eq $PySha) {
    Ok "using cached copy ($cache)"
} else {
    Info "downloading $PyFile"
    $part = "$arch.part"
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -fL --retry 5 --progress-bar -o $part $PyUrl
        if ($LASTEXITCODE) { Die "download failed (curl exit $LASTEXITCODE)" }
    } else { Invoke-WebRequest $PyUrl -OutFile $part -UseBasicParsing }
    if ((Get-FileHash $part -Algorithm SHA256).Hash -ne $PySha) { Remove-Item $part; Die "checksum mismatch on the Python download" }
    Move-Item $part $arch -Force
    Ok "downloaded and checksum verified"
}
$tmp = Join-Path $env:TEMP ("lainai-setup-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $tmp | Out-Null
& tar.exe -xzf $arch -C $tmp
if ($LASTEXITCODE) { Die "couldn't unpack Python" }
$py = Join-Path $tmp 'python\python.exe'
& $py -c "pass"; if ($LASTEXITCODE) { Die "portable Python didn't start (Visual C++ runtime missing?)" }
Ok "ready"

# ------------------------------------------------------------------ 4. hand over
Step "Installing Lain onto the drive"
$pyDrive = if ($Drive -match '^[a-zA-Z]:\\$') { $Drive } else { $Drive.TrimEnd('\') }   # "C:\x y\" would break quoting
$pyArgs = @('-I', '-B', '-X', 'utf8', (Join-Path $Here 'installer\lain_install.py'), '--drive', $pyDrive, '--python-archive', $arch)
if ($Fs)     { $pyArgs += @('--fs', $Fs) }            # Windows PowerShell drops empty arguments
if ($Models) { $pyArgs += @('--models', $Models) }
if ($Yes)    { $pyArgs += '--yes' }
if ($Force)  { $pyArgs += '--force' }
& $py @pyArgs
$rc = $LASTEXITCODE
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

if (-not $RestoreVault) { $RestoreVault = $script:VaultBackup }
if ($RestoreVault) {
    Write-Host ""
    $dstV = Join-Path $Drive 'LainAI\vault'
    if (Test-Path (Join-Path $dstV 'vault.age')) { Info "the drive already has a vault - not restoring $RestoreVault" }
    elseif (Test-Path (Join-Path $RestoreVault 'vault.age')) {
        if (YesNo "Put your backed-up vault ($RestoreVault) onto the drive? [Y/n]" 'y') {
            New-Item -ItemType Directory -Force $dstV | Out-Null
            Copy-Item (Join-Path $RestoreVault '*') $dstV -Recurse -Force
            Ok "vault restored (same passphrase as before) - the backup stays in $RestoreVault until you delete it"
        }
    } else { Warn "$RestoreVault has no vault.age - nothing restored" }
}
if ($rc -eq 0) { Info "Use 'Safely Remove Hardware' before unplugging the drive." }
Read-Host "  Press Enter to close"
exit $rc
