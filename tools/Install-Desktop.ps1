[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PackageDirectory = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\EcommerceProductManager'),
    [string]$DataDir = '',
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'

function Get-OrdinaryPath([string]$Value, [string]$BaseDirectory) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'A filesystem path cannot be empty.' }
    $candidate = if ([IO.Path]::IsPathRooted($Value)) { $Value } else { Join-Path $BaseDirectory $Value }
    $full = [IO.Path]::GetFullPath($candidate)
    $root = [IO.Path]::GetPathRoot($full)
    $current = $root
    $parts = @($full.Substring($root.Length).Split([char[]]'\/', [StringSplitOptions]::RemoveEmptyEntries))
    foreach ($part in $parts) {
        $current = Join-Path $current $part
        try { $entry = Get-Item -LiteralPath $current -Force -ErrorAction Stop }
        catch {
            if ($_.CategoryInfo.Category -eq [Management.Automation.ErrorCategory]::ObjectNotFound) { break }
            throw
        }
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Linked or redirected filesystem paths are not supported. Choose an ordinary directory.'
        }
    }
    return $full
}

function Test-WithinPath([string]$Candidate, [string]$Parent) {
    return $Candidate.Equals($Parent, [StringComparison]::OrdinalIgnoreCase) -or $Candidate.StartsWith($Parent.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-DirectoryOrMissing([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and !(Test-Path -LiteralPath $Path -PathType Container)) { throw 'The selected directory is an existing file.' }
}

$workingDirectory = (Get-Location).ProviderPath
$packagePath = Get-OrdinaryPath $PackageDirectory $workingDirectory
$installPath = Get-OrdinaryPath $InstallDirectory $workingDirectory
Assert-DirectoryOrMissing $installPath
if ($installPath -eq [IO.Path]::GetPathRoot($installPath)) { throw 'The installation directory cannot be a drive root.' }
if (Test-WithinPath $installPath $packagePath) { throw 'Choose an installation directory outside the extracted release package.' }
$sourceExe = Get-OrdinaryPath (Join-Path $packagePath 'Midoo.exe') $packagePath
$targetExe = Get-OrdinaryPath (Join-Path $installPath 'Midoo.exe') $installPath
if (!(Test-Path -LiteralPath $sourceExe -PathType Leaf)) { throw 'Midoo.exe was not found. Extract the complete Windows release before installing.' }
$marker = Get-OrdinaryPath (Join-Path $installPath '.ecommerce-product-manager-install.json') $installPath
$previousMarker = $null
if (Test-Path -LiteralPath $marker -PathType Leaf) {
    $previousMarker = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    $previousInstallPath = Get-OrdinaryPath ([string]$previousMarker.install_path) $installPath
    if ($previousMarker.product -ne 'EcommerceProductManager' -or !$previousInstallPath.Equals($installPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'The installation marker does not belong to this application directory.' }
} elseif (Test-Path -LiteralPath $targetExe) {
    throw 'This directory contains an unrecognized installation. Choose another directory to avoid overwriting files.'
}
$running = Get-CimInstance Win32_Process -Filter "Name = 'Midoo.exe'" | Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -eq $targetExe) }
if ($running) { throw 'Close this installed desktop application normally before updating it. No processes were stopped.' }
$configPath = Get-OrdinaryPath (Join-Path $installPath 'desktop-config.json') $installPath
if ($DataDir) {
    $dataPath = Get-OrdinaryPath $DataDir $installPath
    if (!(Test-Path -LiteralPath $dataPath -PathType Container)) { throw 'The existing data directory does not exist. Check -DataDir before installing.' }
} elseif ($previousMarker -and (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $oldConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $dataPath = if ($oldConfig.data_dir) { Get-OrdinaryPath ([string]$oldConfig.data_dir) $installPath } else { Join-Path $installPath 'data' }
} elseif ($previousMarker -and $previousMarker.data_path) {
    $dataPath = Get-OrdinaryPath ([string]$previousMarker.data_path) $installPath
} else { $dataPath = Join-Path $installPath 'data' }
$dataPath = Get-OrdinaryPath $dataPath $installPath
Assert-DirectoryOrMissing $dataPath

# Preflight all sources and destinations before copying any application file.
$allowedFiles = @('Midoo.exe','Uninstall.exe','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md','README.en.md','Install-Desktop.cmd','Uninstall-Desktop.cmd','VERSION')
$copies = [Collections.Generic.List[object]]::new()
foreach ($name in $allowedFiles) {
    $source = Get-OrdinaryPath (Join-Path $packagePath $name) $packagePath
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $copies.Add([pscustomobject]@{Source=$source;Relative=$name;Destination=(Get-OrdinaryPath (Join-Path $installPath $name) $installPath)})
    }
}
foreach ($folder in @('docs','third_party_licenses','tools')) {
    $sourceFolder = Get-OrdinaryPath (Join-Path $packagePath $folder) $packagePath
    if (!(Test-Path -LiteralPath $sourceFolder -PathType Container)) { continue }
    foreach ($item in Get-ChildItem -LiteralPath $sourceFolder -Recurse -Force) {
        $source = Get-OrdinaryPath $item.FullName $packagePath
        if ($item.PSIsContainer) { continue }
        $relative = $source.Substring($packagePath.TrimEnd('\').Length + 1)
        $destination = Get-OrdinaryPath (Join-Path $installPath $relative) $installPath
        if (!(Test-WithinPath $destination $installPath)) { throw 'A release file points outside the installation directory.' }
        $copies.Add([pscustomobject]@{Source=$source;Relative=$relative;Destination=$destination})
    }
}
foreach ($copy in $copies) {
    if (Test-WithinPath $copy.Destination $dataPath) { throw 'Application files cannot overwrite the selected product data directory.' }
}
if (Test-WithinPath $configPath $dataPath) { throw 'Application settings cannot overwrite the selected product data directory.' }

if (!$PSCmdlet.ShouldProcess($installPath, 'Install the desktop application and shortcuts; preserve product data')) { return }
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
$installedFiles = [Collections.Generic.List[string]]::new()
foreach ($copy in $copies) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $copy.Destination) -Force | Out-Null
    Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination -Force
    $installedFiles.Add($copy.Relative)
}
New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
@{data_dir=$dataPath} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
$installedFiles.Add('desktop-config.json')
$displayName = [string]::Concat([char[]]@(0x7535,0x5546,0x4EA7,0x54C1,0x7BA1,0x7406,0x7CFB,0x7EDF))
$desktopFolder = [Environment]::GetFolderPath('Desktop')
$startFolder = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Ecommerce Product Manager'
$desktopFolder = Get-OrdinaryPath $desktopFolder $installPath
$startFolder = Get-OrdinaryPath $startFolder $installPath
New-Item -ItemType Directory -Path $startFolder -Force | Out-Null
$links = @((Join-Path $desktopFolder ($displayName+'.lnk')), (Join-Path $startFolder ($displayName+'.lnk')))
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $links) {
    $checkedShortcutPath = Get-OrdinaryPath $shortcutPath $installPath
    $shortcut = $shell.CreateShortcut($checkedShortcutPath)
    $shortcut.TargetPath = $targetExe
    $shortcut.WorkingDirectory = $installPath
    $shortcut.IconLocation = "$targetExe,0"
    $shortcut.Description = 'E-commerce Product Management System - Midoo'
    $shortcut.Save()
}
$uninstallPath = Get-OrdinaryPath (Join-Path $startFolder 'Uninstall.lnk') $installPath
$uninstallLink = $shell.CreateShortcut($uninstallPath)
if (Test-Path -LiteralPath (Join-Path $installPath 'Uninstall.exe') -PathType Leaf) {
    $uninstallLink.TargetPath = Join-Path $installPath 'Uninstall.exe'
    $uninstallLink.Arguments = '--uninstall'
} else {
    $uninstallLink.TargetPath = Join-Path $installPath 'Uninstall-Desktop.cmd'
}
$uninstallLink.WorkingDirectory = $installPath
$uninstallLink.Save()
$links += $uninstallPath
@{product='EcommerceProductManager';schema=1;install_path=$installPath;data_path=$dataPath;files=@($installedFiles);shortcuts=$links} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $marker -Encoding UTF8
Write-Output ('Installed: '+$targetExe)
Write-Output ('Product data preserved at: '+$dataPath)
if (!$NoLaunch) { Start-Process -FilePath $targetExe -WorkingDirectory $installPath -WindowStyle Normal }
