[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PackageDirectory = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\EcommerceProductManager'),
    [string]$DataDir = '',
    [string]$RuntimeDir = '',
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

function ConvertTo-WindowsArgument([string]$Value) {
    $result = '"'; $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++ }
        elseif ($character -eq '"') { $result += ('\' * ($slashes * 2 + 1)) + '"'; $slashes = 0 }
        else { $result += ('\' * $slashes) + $character; $slashes = 0 }
    }
    return $result + ('\' * ($slashes * 2)) + '"'
}

function ConvertTo-VbsLiteral([string]$Value) { return '"' + $Value.Replace('"','""') + '"' }

function Get-LauncherScript([string]$Executable, [string]$RuntimePath, [string]$WorkingPath) {
    $header = @(
        'Option Explicit', 'On Error Resume Next',
        'Dim shell, files, processEnvironment, executable, runtimePath, workingPath, command, firstArgument, index',
        'Set shell = CreateObject("WScript.Shell")', 'Set files = CreateObject("Scripting.FileSystemObject")',
        ('workingPath = ' + (ConvertTo-VbsLiteral $WorkingPath)),
        ('runtimePath = ' + (ConvertTo-VbsLiteral $RuntimePath)),
        ('executable = ' + (ConvertTo-VbsLiteral $Executable)),
        'firstArgument = 0', 'If WScript.Arguments.Count > 0 Then',
        '    If LCase(WScript.Arguments(0)) = "--uninstall" Then',
        ('        executable = ' + (ConvertTo-VbsLiteral (Join-Path $WorkingPath 'Uninstall.exe'))),
        '        firstArgument = 1', '    End If', 'End If'
    ) -join "`r`n"
    $body = @'
If Not files.FileExists(executable) Then Fail "The installed executable was not found: " & executable
EnsureFolder runtimePath
CheckError "Cannot prepare the application runtime directory."
Set processEnvironment = shell.Environment("Process")
processEnvironment("TEMP") = runtimePath
processEnvironment("TMP") = runtimePath
processEnvironment("MIDOO_LAUNCH_LITERAL_PERCENT") = "%"
shell.CurrentDirectory = workingPath
CheckError "Cannot configure the application runtime directory."
command = QuoteArgument(executable)
If firstArgument = 1 Then command = command & " --uninstall"
For index = firstArgument To WScript.Arguments.Count - 1
    command = command & " " & QuoteArgument(WScript.Arguments(index))
Next
shell.Run LiteralCommand(command), 1, False
CheckError "The installed application could not be started."
WScript.Quit 0

Sub EnsureFolder(path)
    Dim parent
    parent = files.GetParentFolderName(path)
    If parent <> "" And parent <> path Then EnsureFolder parent
    If Err.Number <> 0 Then Exit Sub
    If files.FolderExists(path) Then
        If (files.GetFolder(path).Attributes And 1024) <> 0 Then Fail "The runtime directory cannot contain links or junctions."
    Else
        files.CreateFolder path
    End If
End Sub

Sub CheckError(message)
    If Err.Number <> 0 Then Fail message & vbCrLf & Err.Description
End Sub

Sub Fail(message)
    MsgBox message, 16, "Midoo"
    WScript.Quit 1
End Sub

Function LiteralCommand(value)
    LiteralCommand = Replace(value, "%", "%MIDOO_LAUNCH_LITERAL_PERCENT%")
End Function

Function QuoteArgument(value)
    Dim result, slashes, position, character
    result = Chr(34)
    slashes = 0
    For position = 1 To Len(value)
        character = Mid(value, position, 1)
        If character = "\" Then
            slashes = slashes + 1
        ElseIf character = Chr(34) Then
            result = result & String(slashes * 2 + 1, "\") & Chr(34)
            slashes = 0
        Else
            result = result & String(slashes, "\") & character
            slashes = 0
        End If
    Next
    QuoteArgument = result & String(slashes * 2, "\") & Chr(34)
End Function
'@
    return $header + "`r`n" + ($body -replace "`r?`n", "`r`n") + "`r`n"
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
$oldConfig = @{}
if ($previousMarker -and (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $oldConfigObject = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    foreach ($property in $oldConfigObject.PSObject.Properties) { $oldConfig[$property.Name] = $property.Value }
}
if ($DataDir) {
    $dataPath = Get-OrdinaryPath $DataDir $installPath
    if (!(Test-Path -LiteralPath $dataPath -PathType Container)) { throw 'The existing data directory does not exist. Check -DataDir before installing.' }
} elseif ($previousMarker -and (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $dataPath = if ($oldConfig.data_dir) { Get-OrdinaryPath ([string]$oldConfig.data_dir) $installPath } elseif ($previousMarker.data_path) { Get-OrdinaryPath ([string]$previousMarker.data_path) $installPath } else { Join-Path $installPath 'data' }
} elseif ($previousMarker -and $previousMarker.data_path) {
    $dataPath = Get-OrdinaryPath ([string]$previousMarker.data_path) $installPath
} else { $dataPath = Join-Path $installPath 'data' }
$dataPath = Get-OrdinaryPath $dataPath $installPath
Assert-DirectoryOrMissing $dataPath
$selectedRuntime = if ($RuntimeDir) { $RuntimeDir } elseif ($previousMarker -and $previousMarker.runtime_path) { [string]$previousMarker.runtime_path } else { Join-Path (Split-Path -Parent $dataPath) '.midoo-runtime' }
$runtimePath = Get-OrdinaryPath $selectedRuntime $installPath
Assert-DirectoryOrMissing $runtimePath
if ($runtimePath -eq [IO.Path]::GetPathRoot($runtimePath) -or (Test-WithinPath $installPath $runtimePath) -or (Test-WithinPath $runtimePath $dataPath) -or (Test-WithinPath $dataPath $runtimePath)) {
    throw 'Choose a separate runtime directory outside the product data directory.'
}
$launcherPath = Get-OrdinaryPath (Join-Path $installPath 'Launch-Desktop.vbs') $installPath
$scriptHost = Get-OrdinaryPath (Join-Path $env:SystemRoot 'System32\wscript.exe') $installPath
$launcherArguments = '//nologo ' + (ConvertTo-WindowsArgument $launcherPath)
$uninstallArguments = $launcherArguments + ' --uninstall'
$launcherIdentity = @{path=$launcherPath;host=$scriptHost;arguments=$launcherArguments;uninstall_arguments=$uninstallArguments}

# Preflight all sources and destinations before copying any application file.
$allowedFiles = @('Midoo.exe','Uninstall.exe','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md','README.en.md','Install-Desktop.cmd','Uninstall-Desktop.cmd','VERSION','PRICE_TRACKING.md','PRODUCT_CLASSIFICATION.md','ENTERPRISE_KNOWLEDGE.md','STORE_IMPORT.md','PAGE_CAPTURE_EXTENSION.md')
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
    if (Test-WithinPath $copy.Destination $runtimePath) { throw 'Application files cannot overwrite the runtime directory.' }
}
foreach ($managedPath in @($configPath,$marker,$launcherPath)) {
    if ((Test-WithinPath $managedPath $dataPath) -or (Test-WithinPath $managedPath $runtimePath)) { throw 'Application settings cannot overwrite the product data or runtime directory.' }
}
if ((Test-Path -LiteralPath $launcherPath) -and (!$previousMarker -or $previousMarker.files -notcontains 'Launch-Desktop.vbs')) { throw 'An unrecognized launcher already exists. No files were replaced.' }

if (!$PSCmdlet.ShouldProcess($installPath, 'Install the desktop application and shortcuts; preserve product data')) { return }
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
$installedFiles = [Collections.Generic.List[string]]::new()
foreach ($copy in $copies) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $copy.Destination) -Force | Out-Null
    Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination -Force
    $installedFiles.Add($copy.Relative)
}
New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
$oldConfig['data_dir'] = $dataPath
$oldConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $configPath -Encoding UTF8
$installedFiles.Add('desktop-config.json')
[IO.File]::WriteAllText($launcherPath, (Get-LauncherScript $targetExe $runtimePath $installPath), [Text.Encoding]::Unicode)
$installedFiles.Add('Launch-Desktop.vbs')
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
    $shortcut.TargetPath = $scriptHost
    $shortcut.Arguments = $launcherArguments
    $shortcut.WorkingDirectory = $installPath
    $shortcut.IconLocation = "$targetExe,0"
    $shortcut.Description = 'E-commerce Product Management System - Midoo'
    $shortcut.Save()
}
$uninstallPath = Get-OrdinaryPath (Join-Path $startFolder 'Uninstall.lnk') $installPath
$uninstallLink = $shell.CreateShortcut($uninstallPath)
if (Test-Path -LiteralPath (Join-Path $installPath 'Uninstall.exe') -PathType Leaf) {
    $uninstallLink.TargetPath = $scriptHost
    $uninstallLink.Arguments = $uninstallArguments
} else {
    $uninstallLink.TargetPath = Join-Path $installPath 'Uninstall-Desktop.cmd'
    $uninstallLink.Arguments = ''
}
$uninstallLink.WorkingDirectory = $installPath
$uninstallLink.IconLocation = "$targetExe,0"
$uninstallLink.Save()
$links += $uninstallPath
@{product='EcommerceProductManager';schema=2;install_path=$installPath;data_path=$dataPath;runtime_path=$runtimePath;launcher=$launcherIdentity;files=@($installedFiles);shortcuts=$links} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $marker -Encoding UTF8
Write-Output ('Installed: '+$targetExe)
Write-Output ('Product data preserved at: '+$dataPath)
if (!$NoLaunch) { Start-Process -FilePath $scriptHost -ArgumentList $launcherArguments -WorkingDirectory $installPath -WindowStyle Hidden }
