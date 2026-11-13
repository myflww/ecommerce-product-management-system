[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\EcommerceProductManager'))
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
            throw 'Linked or redirected filesystem paths are not supported. No files were removed.'
        }
    }
    return $full
}

function Test-WithinPath([string]$Candidate, [string]$Parent) {
    return $Candidate.Equals($Parent, [StringComparison]::OrdinalIgnoreCase) -or $Candidate.StartsWith($Parent.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)
}

$workingDirectory = (Get-Location).ProviderPath
$installPath = Get-OrdinaryPath $InstallDirectory $workingDirectory
if ($installPath -eq [IO.Path]::GetPathRoot($installPath)) { throw 'The installation directory cannot be a drive root.' }
$markerPath = Get-OrdinaryPath (Join-Path $installPath '.ecommerce-product-manager-install.json') $installPath
if (!(Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw 'No recognized installation marker was found. No files were removed.' }
$marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
$recordedPath = Get-OrdinaryPath ([string]$marker.install_path) $installPath
if ($marker.product -ne 'EcommerceProductManager' -or !$recordedPath.Equals($installPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid installation marker. No files were removed.' }
$dataPath = Get-OrdinaryPath ([string]$marker.data_path) $installPath
$defaultDataPath = Get-OrdinaryPath (Join-Path $installPath 'data') $installPath
$targetExe = Get-OrdinaryPath (Join-Path $installPath 'Midoo.exe') $installPath
$running = Get-CimInstance Win32_Process -Filter "Name = 'Midoo.exe'" | Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -eq $targetExe) }
if ($running) { throw 'Close the desktop application normally before uninstalling. No processes were stopped.' }

# Validate the complete manifest before deleting any shortcut or file.
$resolvedFiles = @()
foreach ($relative in $marker.files) {
    if ($relative -isnot [string] -or [string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative)) { throw 'Invalid installed-file entry. No files were removed.' }
    $resolved = Get-OrdinaryPath (Join-Path $installPath $relative) $installPath
    if ($resolved.Equals($installPath,[StringComparison]::OrdinalIgnoreCase) -or !(Test-WithinPath $resolved $installPath)) { throw 'A file points outside this installation. No files were removed.' }
    if ((Test-WithinPath $resolved $dataPath) -or (Test-WithinPath $resolved $defaultDataPath)) { throw 'Product data cannot be removed by this uninstaller.' }
    $resolvedFiles += $resolved
}
$shortcutPaths = @()
foreach ($shortcutPath in $marker.shortcuts) {
    if ($shortcutPath -isnot [string] -or ![IO.Path]::IsPathRooted($shortcutPath) -or [IO.Path]::GetExtension($shortcutPath) -ne '.lnk') { throw 'Invalid shortcut entry. No files were removed.' }
    $shortcutPaths += Get-OrdinaryPath $shortcutPath $installPath
}
if (!$PSCmdlet.ShouldProcess($installPath, 'Remove installed application files and matching shortcuts; KEEP all product data')) { return }
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $shortcutPaths) {
    if (!(Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { continue }
    $link = $shell.CreateShortcut($shortcutPath)
    if ($link.TargetPath -eq $targetExe -or $link.TargetPath -eq (Join-Path $installPath 'Uninstall-Desktop.cmd') -or $link.TargetPath -eq (Join-Path $installPath 'Uninstall.exe')) { Remove-Item -LiteralPath $shortcutPath }
}
foreach ($file in $resolvedFiles) { if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file } }
Remove-Item -LiteralPath $markerPath
Write-Output ('Application removed. Product data was kept at: '+$dataPath)
