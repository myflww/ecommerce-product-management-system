"""Windows installer integration in a temporary sandbox with COM/processes replaced.

No real shortcuts, application executables, or product directories are used.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

import desktop_installer as installer


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL, reason='Windows PowerShell sandbox')


MOCKS = r'''
$ErrorActionPreference = 'Stop'
function Assert-Sandbox([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($script:Sandbox.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw ('The test refused a filesystem path outside its sandbox: ' + $full)
    }
}
$script:MockShell = [pscustomobject]@{}
$script:MockShell | Add-Member ScriptMethod CreateShortcut {
    param([string]$Path)
    Assert-Sandbox $Path
    $link = [pscustomobject]@{
        FullName=$Path; TargetPath=''; Arguments=''; WorkingDirectory=''; IconLocation=''; Description=''
    }
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $saved = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        foreach ($name in @('TargetPath','Arguments','WorkingDirectory','IconLocation','Description')) {
            if ($saved.PSObject.Properties.Name -contains $name) { $link.$name = [string]$saved.$name }
        }
    }
    $link | Add-Member ScriptMethod Save {
        Assert-Sandbox $this.FullName
        $this | Select-Object TargetPath,Arguments,WorkingDirectory,IconLocation,Description |
            ConvertTo-Json | Set-Content -LiteralPath $this.FullName -Encoding UTF8
    }
    return $link
}
function New-Object {
    param([string]$ComObject)
    if ($ComObject -ne 'WScript.Shell') { throw 'The sandbox allows only the mocked WScript.Shell.' }
    return $script:MockShell
}
function Get-CimInstance { param($ClassName, $Filter) return @() }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
    $script:MockStarts += [pscustomobject]@{
        FilePath=$FilePath; ArgumentList=$ArgumentList; WorkingDirectory=$WorkingDirectory; WindowStyle=$WindowStyle
    }
}
'''


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _run_script(sandbox, name, parameters, *, check=True, source_override=None):
    """Run a script with only the two special-folder expressions redirected.

    Read-only Windows system paths (including the script host) remain real.
    The mock rejects any attempt to create a shortcut outside ``sandbox``.
    """
    source = source_override if source_override is not None else (ROOT / 'tools' / name).read_text(encoding='utf-8-sig')
    for folder in ('Desktop', 'StartMenu'):
        expression = f"[Environment]::GetFolderPath('{folder}')"
        if source_override is None and name == 'Install-Desktop.ps1':
            assert source.count(expression) == 1, f'Update sandbox redirection for {folder} before running'
        source = source.replace(expression, _ps_literal(sandbox / folder))
    assert "GetFolderPath('Desktop')" not in source
    assert "GetFolderPath('StartMenu')" not in source
    encoded = base64.b64encode(source.encode('utf-8')).decode('ascii')
    options = ';'.join(
        f'{key}=' + ('$true' if value is True else '$false' if value is False else _ps_literal(value))
        for key, value in parameters.items()
    )
    report = sandbox / 'mock-starts.json'
    harness = (
        '$script:Sandbox=' + _ps_literal(sandbox) + '\n'
        '$script:MockStarts=@()\n' + MOCKS + '\n'
        '$beforeTemp=$env:TEMP; $beforeTmp=$env:TMP\n'
        '$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(' + _ps_literal(encoded) + '))\n'
        '$operation=[ScriptBlock]::Create($source)\n'
        '$options=@{' + options + '}\n'
        'try { & $operation @options; $code=0 } catch { [Console]::Error.WriteLine($_.ToString()); $code=1 }\n'
        "if ($env:TEMP -cne $beforeTemp -or $env:TMP -cne $beforeTmp) { throw 'Installer changed the parent process environment.' }\n"
        '@($script:MockStarts) | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath '
        + _ps_literal(report) + ' -Encoding UTF8\nexit $code\n'
    )
    script = sandbox / 'sandbox-harness.ps1'
    script.write_text(harness, encoding='utf-8-sig')
    result = subprocess.run(
        [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
        cwd=sandbox, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    if check:
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
    return result


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "沙盒 One's $literal"
    root.mkdir()
    for name in ('Desktop', 'StartMenu', 'package', 'external data'):
        (root / name).mkdir()
    (root / 'package' / 'Midoo.exe').write_bytes(b'not-an-executable')
    (root / 'package' / 'Uninstall.exe').write_bytes(b'not-an-executable')
    (root / 'package' / 'Uninstall-Desktop.cmd').write_text('@exit /b 0\n')
    (root / 'package' / 'PAGE_CAPTURE_EXTENSION.md').write_text('public extension guide')
    (root / 'external data' / 'sentinel.txt').write_bytes(b'test product data must be kept')
    return root


def _install(sandbox, *, data=True, runtime=None, launch=False, check=True):
    options = {'PackageDirectory': sandbox / 'package', 'InstallDirectory': sandbox / 'app 中文', 'NoLaunch': not launch}
    if data:
        options['DataDir'] = sandbox / 'external data'
    if runtime is not None:
        options['RuntimeDir'] = runtime
    return _run_script(sandbox, 'Install-Desktop.ps1', options, check=check)


def _marker(sandbox):
    return sandbox / 'app 中文' / installer.MARKER


def test_ps_install_uses_data_drive_runtime_and_same_utf16_launcher(sandbox):
    _install(sandbox, launch=True)
    state = _read_json(_marker(sandbox))
    target = sandbox / 'app 中文'
    runtime = sandbox / '.midoo-runtime'
    identity = state['launcher']
    assert Path(state['runtime_path']) == runtime
    assert Path(state['data_path']) == sandbox / 'external data'
    assert 'Launch-Desktop.vbs' in state['files']
    assert 'PAGE_CAPTURE_EXTENSION.md' in state['files']
    assert not any('.midoo-runtime' in item for item in state['files'])
    assert (target / installer.LAUNCHER).read_bytes() == installer.launcher_script(target / 'Midoo.exe', runtime, target).encode('utf-16')
    assert Path(identity['host']).name.lower() == 'wscript.exe'
    assert identity['arguments'] == '//nologo ' + installer.windows_quote_argument(target / installer.LAUNCHER)
    assert identity['uninstall_arguments'] == identity['arguments'] + ' --uninstall'
    for path in state['shortcuts']:
        link = _read_json(Path(path))
        assert link['TargetPath'].lower() == identity['host'].lower()
        assert link['Arguments'] == (identity['uninstall_arguments'] if Path(path).name == 'Uninstall.lnk' else identity['arguments'])
        assert Path(link['WorkingDirectory']) == target
        if Path(path).name != 'Uninstall.lnk':
            assert link['IconLocation'] == str(target / 'Midoo.exe') + ',0'
    starts = _read_json(sandbox / 'mock-starts.json')
    if isinstance(starts, list):
        assert len(starts) == 1
        starts = starts[0]
    assert starts['FilePath'].lower() == identity['host'].lower()
    actual_arguments = starts['ArgumentList']
    if isinstance(actual_arguments, list):
        actual_arguments = ' '.join(actual_arguments)
    assert actual_arguments == identity['arguments']
    assert (sandbox / 'external data' / 'sentinel.txt').read_bytes() == b'test product data must be kept'


def test_ps_upgrade_retains_explicit_runtime_and_falls_back_to_marker_data(sandbox):
    runtime = sandbox / 'runtime with space'
    _install(sandbox, runtime=runtime)
    target = sandbox / 'app 中文'
    (target / 'desktop-config.json').unlink()
    (runtime / 'retained.bin').write_bytes(b'runtime cache fixture')
    _install(sandbox, data=False)
    state = _read_json(_marker(sandbox))
    assert Path(state['runtime_path']) == runtime
    assert Path(state['data_path']) == sandbox / 'external data'
    assert Path(_read_json(target / 'desktop-config.json')['data_dir']) == sandbox / 'external data'
    assert (runtime / 'retained.bin').read_bytes() == b'runtime cache fixture'
    config = _read_json(target / 'desktop-config.json')
    config['user_setting'] = {'nested': 'keep me'}
    (target / 'desktop-config.json').write_text(json.dumps(config), encoding='utf-8')
    _install(sandbox, data=False)
    assert _read_json(target / 'desktop-config.json')['user_setting'] == {'nested': 'keep me'}


@pytest.mark.parametrize('runtime_key', ['inside_data', 'data_parent', 'install_itself', 'install_parent', 'application_file'])
def test_ps_invalid_runtime_fails_before_install_and_preserves_data(sandbox, runtime_key):
    target = sandbox / 'app 中文'
    runtime = {
        'inside_data': sandbox / 'external data' / 'cache',
        'data_parent': sandbox,
        'install_itself': target,
        'install_parent': sandbox,
        'application_file': target / 'Midoo.exe',
    }[runtime_key]
    result = _install(sandbox, runtime=runtime, check=False)
    assert result.returncode != 0
    assert not (target / 'Midoo.exe').exists()
    assert (sandbox / 'external data' / 'sentinel.txt').read_bytes() == b'test product data must be kept'


def test_ps_reparse_runtime_is_rejected_without_touching_data(sandbox):
    real = sandbox / 'real runtime'
    real.mkdir()
    junction = sandbox / 'runtime junction'
    # Both junction ends are ordinary, verified descendants of this test's root.
    assert real.resolve().is_relative_to(sandbox.resolve())
    assert junction.absolute().is_relative_to(sandbox.resolve())
    command = f"New-Item -ItemType Junction -Path {_ps_literal(junction)} -Target {_ps_literal(real)} | Out-Null"
    encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    result = _install(sandbox, runtime=junction, check=False)
    assert result.returncode != 0
    assert not (sandbox / 'app 中文' / 'Midoo.exe').exists()
    assert not list(real.iterdir())
    assert (sandbox / 'external data' / 'sentinel.txt').read_bytes() == b'test product data must be kept'


def test_ps_uninstall_requires_full_launcher_identity_and_keeps_runtime(sandbox):
    _install(sandbox)
    state = _read_json(_marker(sandbox))
    shortcuts = [Path(path) for path in state['shortcuts']]
    # One unrelated launcher on the same system host, one changed host with the
    # expected arguments, and one untouched application link.
    first = _read_json(shortcuts[0])
    first['Arguments'] += ' --different-task'
    shortcuts[0].write_text(json.dumps(first), encoding='utf-8')
    second = _read_json(shortcuts[1])
    second['TargetPath'] = str(sandbox / 'other-wscript.exe')
    shortcuts[1].write_text(json.dumps(second), encoding='utf-8')
    runtime = Path(state['runtime_path'])
    (runtime / 'retained.bin').write_bytes(b'runtime cache fixture')
    _run_script(sandbox, 'Uninstall-Desktop.ps1', {'InstallDirectory': sandbox / 'app 中文'})
    assert shortcuts[0].is_file()
    assert shortcuts[1].is_file()
    assert not shortcuts[2].exists()
    assert not (sandbox / 'app 中文' / installer.LAUNCHER).exists()
    assert not _marker(sandbox).exists()
    assert (runtime / 'retained.bin').read_bytes() == b'runtime cache fixture'
    assert (sandbox / 'external data' / 'sentinel.txt').read_bytes() == b'test product data must be kept'


def test_ps_uninstall_accepts_legacy_executable_targets(sandbox):
    _install(sandbox)
    state = _read_json(_marker(sandbox))
    target = sandbox / 'app 中文'
    state.pop('launcher')
    _marker(sandbox).write_text(json.dumps(state), encoding='utf-8')
    for path, name in zip(state['shortcuts'], ('Midoo.exe', 'Uninstall.exe', 'Uninstall-Desktop.cmd')):
        link = _read_json(Path(path))
        link['TargetPath'] = str(target / name)
        link['Arguments'] = '--uninstall' if name == 'Uninstall.exe' else ''
        Path(path).write_text(json.dumps(link), encoding='utf-8')
    _run_script(sandbox, 'Uninstall-Desktop.ps1', {'InstallDirectory': target})
    assert all(not Path(path).exists() for path in state['shortcuts'])
    assert (sandbox / 'external data' / 'sentinel.txt').read_bytes() == b'test product data must be kept'


def test_ps_without_native_uninstaller_retains_cmd_shortcut(sandbox):
    (sandbox / 'package' / 'Uninstall.exe').unlink()
    _install(sandbox)
    state = _read_json(_marker(sandbox))
    link = _read_json(Path(state['shortcuts'][-1]))
    assert Path(link['TargetPath']) == sandbox / 'app 中文' / 'Uninstall-Desktop.cmd'
    assert not link['Arguments']


def test_python_uninstall_powershell_requires_full_launcher_identity(sandbox, monkeypatch):
    """Run Python's generated removal script against the same fake COM surface."""
    from types import SimpleNamespace

    target = sandbox / 'app 中文'
    folders = base64.b64encode(json.dumps({'desktop': str(sandbox / 'Desktop'), 'start': str(sandbox / 'StartMenu')}).encode()).decode()
    with monkeypatch.context() as patch:
        patch.setattr(installer, 'installation_running', lambda _: False)
        patch.setattr(installer, 'powershell', lambda _: folders)
        patch.setattr(installer.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr=''))
        installer.install(sandbox / 'package', target, sandbox / 'external data', launch=False)
    state = _read_json(_marker(sandbox))
    identity = state['launcher']
    shortcuts = [Path(path) for path in state['shortcuts']]
    for index, path in enumerate(shortcuts):
        path.parent.mkdir(parents=True, exist_ok=True)
        link = {'TargetPath': identity['host'], 'Arguments': identity['uninstall_arguments'] if index == 2 else identity['arguments']}
        if index == 0:
            link['Arguments'] += ' --different-task'
        elif index == 1:
            link['TargetPath'] = str(sandbox / 'other-host.exe')
        path.write_text(json.dumps(link), encoding='utf-8')
    runtime = Path(state['runtime_path'])
    (runtime / 'retained.bin').write_bytes(b'runtime cache fixture')
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(installer, 'installation_running', lambda _: False)
        patch.setattr(installer, 'powershell', lambda source: captured.append(source) or '')
        installer.uninstall(target)
    assert len(captured) == 1
    _run_script(sandbox, 'python-uninstall', {}, source_override=captured[0])
    assert shortcuts[0].is_file()
    assert shortcuts[1].is_file()
    assert not shortcuts[2].exists()
    assert (runtime / 'retained.bin').read_bytes() == b'runtime cache fixture'
    assert (sandbox / 'external data' / 'sentinel.txt').read_bytes() == b'test product data must be kept'


def test_generated_vbs_argument_quoting_roundtrips_with_windows_parser(sandbox):
    cscript = shutil.which('cscript.exe')
    if not cscript:
        pytest.skip('Windows Script Host is unavailable')
    generated = installer.launcher_script(Path('Midoo.exe'), Path('runtime'), Path('app'))
    match = re.search(r'Function QuoteArgument\(value\).*?End Function', generated, flags=re.S)
    assert match, 'The test must exercise the generated VBS quoting implementation'
    values = ['', '中文 with spaces', 'embedded "quote"', 'C:\\trailing path\\', '\\"', "One's $literal"]
    literals = ['"' + value.replace('"', '""') + '"' for value in values]
    output = sandbox / 'quoted-arguments.txt'
    # Execute only the quoting function and a sandbox-local Unicode output file;
    # the generated launcher's process and UI code never run.
    program = (
        'Option Explicit\r\nDim files, stream\r\n' + match.group(0) + '\r\n'
        'Set files = CreateObject("Scripting.FileSystemObject")\r\n'
        'Set stream = files.CreateTextFile("' + str(output).replace('"', '""') + '", True, True)\r\n'
        + '\r\n'.join('stream.WriteLine QuoteArgument(' + value + ')' for value in literals)
        + '\r\nstream.Close\r\n'
    )
    script = sandbox / 'quote-only.vbs'
    script.write_bytes(program.encode('utf-16'))
    result = subprocess.run([cscript, '//nologo', str(script)], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    quoted = output.read_text(encoding='utf-16').splitlines()
    assert len(quoted) == len(values)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    for value, argument in zip(values, quoted):
        count = ctypes.c_int()
        parsed = shell32.CommandLineToArgvW('program.exe ' + argument, ctypes.byref(count))
        assert parsed
        try:
            assert count.value == 2
            assert parsed[1] == value
        finally:
            kernel32.LocalFree(parsed)


def test_script_host_single_pass_percent_encoding_preserves_literal_arguments(sandbox):
    """Exercise generated quote/percent functions through actual WshShell.Run."""
    cscript = shutil.which('cscript.exe')
    if not cscript:
        pytest.skip('Windows Script Host is unavailable')
    fixture_dir = sandbox / 'helper %MIO_TEST_LITERAL_123% 中文'
    fixture_dir.mkdir()
    # The Windows venv launcher is copied, with a sandbox-only configuration
    # pointing at the same installed Python runtime. No build is needed.
    executable = fixture_dir / 'python.exe'
    shutil.copy2(sys.executable, executable)
    base_executable = Path(getattr(sys, '_base_executable', sys.executable))
    (fixture_dir / 'pyvenv.cfg').write_text(
        'home = ' + str(base_executable.parent) + '\n'
        'include-system-site-packages = false\n'
        'executable = ' + str(base_executable) + '\n',
        encoding='utf-8',
    )
    helper = fixture_dir / 'argv-helper.py'
    helper.write_text(
        'import json, pathlib, sys\n'
        'pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]), encoding="utf-8")\n',
        encoding='utf-8',
    )
    output = fixture_dir / 'argv-result.json'
    values = ['', '中文 with spaces', 'embedded "quote"', 'C:\\trailing path\\', '\\"',
              '%TEMP%', '%MIO_TEST_LITERAL_123%', '%%', '%MIDOO_LAUNCH_LITERAL_PERCENT%',
              '100% later', '%MIO_TEST_LITERAL_123%%MIO_TEST_LITERAL_123%']
    generated = installer.launcher_script(executable, sandbox / '.midoo-runtime', sandbox)
    functions = []
    for name in ('QuoteArgument', 'LiteralCommand'):
        match = re.search('Function ' + name + r'\(value\).*?End Function', generated, flags=re.S)
        assert match, 'Exercise the generated VBS implementation of ' + name
        functions.append(match.group(0))
    arguments = [str(value) for value in (executable, helper, output, *values)]
    expressions = ['QuoteArgument("' + value.replace('"', '""') + '")' for value in arguments]
    program = (
        'Option Explicit\r\nDim shell, environment, command\r\n'
        + '\r\n'.join(functions) + '\r\n'
        'Set shell = CreateObject("WScript.Shell")\r\n'
        'Set environment = shell.Environment("Process")\r\n'
        'environment("MIDOO_LAUNCH_LITERAL_PERCENT") = "%"\r\n'
        'command = ' + ' & " " & '.join(expressions) + '\r\n'
        'WScript.Quit shell.Run(LiteralCommand(command), 0, True)\r\n'
    )
    script = sandbox / 'actual-argument-functions.vbs'
    script.write_bytes(program.encode('utf-16'))
    environment = os.environ.copy()
    environment['MIO_TEST_LITERAL_123'] = 'expanded value'
    result = subprocess.run([cscript, '//nologo', '//T:15', str(script)], env=environment, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert _read_json(output) == values
