"""Current-user Windows installer; never stops services or deletes product data."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

TITLE = '电商产品管理系统'
MARKER = '.ecommerce-product-manager-install.json'
LAUNCHER = 'Launch-Desktop.vbs'
ALLOW_FILES = {'Midoo.exe','Uninstall.exe','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md','README.en.md','VERSION','Install-Desktop.cmd','Uninstall-Desktop.cmd','PRICE_TRACKING.md','PRODUCT_CLASSIFICATION.md','ENTERPRISE_KNOWLEDGE.md','STORE_IMPORT.md','PAGE_CAPTURE_EXTENSION.md'}
ALLOW_DIRS = {'docs','third_party_licenses','tools'}


def default_install_dir():
    return Path(os.environ['LOCALAPPDATA'])/'Programs'/'EcommerceProductManager'


def payload_dir():
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    return base/'payload'


def ps_literal(value):
    return "'"+str(value).replace("'","''")+"'"


def powershell(script):
    result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',script],stdin=subprocess.DEVNULL,capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError('Windows could not create the shortcuts. The application and data remain in the installation directory.')
    return result.stdout


def installation_running(executable):
    script="$p="+ps_literal(executable)+"; @(Get-CimInstance Win32_Process -Filter \"Name = 'Midoo.exe'\" | Where-Object { $_.ExecutablePath -eq $p }).Count"
    output=powershell(script).strip()
    return bool(output and int(output)>0)


def ordinary_path(value):
    """Reject existing links before resolving, including parent junctions."""
    raw=Path(os.path.abspath(os.path.expanduser(str(value))))
    for part in (raw,*raw.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise RuntimeError('Choose an ordinary directory, not a link or junction.')
        try:attributes=getattr(part.lstat(),'st_file_attributes',0)
        except FileNotFoundError:continue
        if attributes & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
            raise RuntimeError('Choose an ordinary directory, not a link or junction.')
    return raw.resolve()


def windows_quote_argument(value):
    """Always quote one Windows argument, including quotes and trailing slashes."""
    result='"';slashes=0
    for character in str(value):
        if character=='\\':slashes+=1
        elif character=='"':result+='\\'*(slashes*2+1)+'"';slashes=0
        else:result+='\\'*slashes+character;slashes=0
    return result+'\\'*(slashes*2)+'"'


def wscript_host():
    root=os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    if not root:raise RuntimeError('The Windows script host could not be located.')
    return ordinary_path(Path(root)/'System32'/'wscript.exe')


def launcher_identity(target):
    path=target/LAUNCHER
    arguments='//nologo '+windows_quote_argument(path)
    return {'path':str(path),'host':str(wscript_host()),'arguments':arguments,'uninstall_arguments':arguments+' --uninstall'}


def default_runtime_dir(data):
    return data.parent/'.midoo-runtime'


def launcher_script(exe,runtime,target):
    """The installer writes this per-installation script as UTF-16 with a BOM."""
    def literal(value):return '"'+str(value).replace('"','""')+'"'
    header='\n'.join(('Option Explicit','On Error Resume Next',
        'Dim shell, files, processEnvironment, executable, runtimePath, workingPath, command, firstArgument, index',
        'Set shell = CreateObject("WScript.Shell")','Set files = CreateObject("Scripting.FileSystemObject")',
        'workingPath = '+literal(target),'runtimePath = '+literal(runtime),'executable = '+literal(exe),
        'firstArgument = 0','If WScript.Arguments.Count > 0 Then',
        '    If LCase(WScript.Arguments(0)) = "--uninstall" Then',
        '        executable = '+literal(target/'Uninstall.exe'),
        '        firstArgument = 1','    End If','End If'))
    body=r'''
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
'''
    return (header+body).replace('\n','\r\n')


def install(package, target, data_dir=None, launch=True, runtime_dir=None):
    package=ordinary_path(package);target=ordinary_path(target)
    if not (package/'Midoo.exe').is_file():
        raise RuntimeError('The complete application payload is missing.')
    if target==Path(target.anchor) or target.is_symlink() or (hasattr(target,'is_junction') and target.is_junction()):
        raise RuntimeError('Choose an ordinary installation directory, not a drive root or link.')
    marker=ordinary_path(target/MARKER)
    if (target/'Midoo.exe').exists() and not marker.is_file():
        raise RuntimeError('An unrecognized application already exists in this directory. Choose another location.')
    state={}
    if marker.is_file():
        state=json.loads(marker.read_text(encoding='utf-8-sig'))
        if state.get('product')!='EcommerceProductManager' or Path(state.get('install_path','')).resolve()!=target:
            raise RuntimeError('Invalid installation marker. Choose another directory.')
    if installation_running(target/'Midoo.exe'):
        raise RuntimeError('Please close this installed desktop application normally before updating it. No processes were stopped.')
    config_path=ordinary_path(target/'desktop-config.json')
    config=json.loads(config_path.read_text(encoding='utf-8-sig')) if marker.is_file() and config_path.is_file() else {}
    selected=data_dir or config.get('data_dir') or state.get('data_path')
    data=Path(selected).expanduser() if selected else target/'data'
    if not data.is_absolute():data=target/data
    data=ordinary_path(data)
    if data_dir and not data.is_dir():
        raise RuntimeError('The existing product data directory was not found. Check the selected location.')
    if data.exists() and not data.is_dir():
        raise RuntimeError('The product data directory is an existing file.')
    runtime=Path(runtime_dir or state.get('runtime_path') or default_runtime_dir(data)).expanduser()
    if not runtime.is_absolute():runtime=target/runtime
    runtime=ordinary_path(runtime)
    if runtime==Path(runtime.anchor) or runtime==target or target.is_relative_to(runtime) or runtime==data or runtime.is_relative_to(data) or data.is_relative_to(runtime):
        raise RuntimeError('Choose a separate runtime directory outside the product data directory.')
    if runtime.exists() and not runtime.is_dir():
        raise RuntimeError('The runtime directory is an existing file.')
    identity=launcher_identity(target)
    files=[];copies=[]
    for source in package.rglob('*'):
        if not source.is_file():continue
        relative=source.relative_to(package)
        allowed=relative.name in ALLOW_FILES if len(relative.parts)==1 else relative.parts[0] in ALLOW_DIRS
        if not allowed:continue
        ordinary_path(source)
        destination=ordinary_path(target/relative)
        if destination==data or destination.is_relative_to(data):
            raise RuntimeError('Application files cannot overwrite the product data directory.')
        if destination==runtime or destination.is_relative_to(runtime):
            raise RuntimeError('Application files cannot overwrite the runtime directory.')
        copies.append((source,destination,relative))
    for managed in (ordinary_path(marker),config_path,ordinary_path(target/LAUNCHER)):
        if managed==data or managed.is_relative_to(data) or managed==runtime or managed.is_relative_to(runtime):
            raise RuntimeError('Application settings cannot overwrite the product data or runtime directory.')
    if (target/LAUNCHER).exists() and LAUNCHER not in state.get('files',[]):
        raise RuntimeError('An unrecognized launcher already exists. No files were replaced.')
    target.mkdir(parents=True,exist_ok=True)
    runtime.mkdir(parents=True,exist_ok=True)
    for source,destination,relative in copies:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if source!=destination:shutil.copy2(source,destination)
        files.append(str(relative))
    data.mkdir(parents=True,exist_ok=True)
    config['data_dir']=str(data)
    config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    files.append('desktop-config.json')
    exe=target/'Midoo.exe'
    (target/LAUNCHER).write_bytes(launcher_script(exe,runtime,target).encode('utf-16'))
    files.append(LAUNCHER)
    record={'product':'EcommerceProductManager','schema':2,'install_path':str(target),'data_path':str(data),'runtime_path':str(runtime),'launcher':identity,'files':files,'shortcuts':[]}
    marker.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    script=("$s=New-Object -ComObject WScript.Shell; "
            "$d=[Environment]::GetFolderPath('Desktop'); "
            "$m=Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\\Ecommerce Product Manager'; "
            "New-Item -ItemType Directory -Path $m -Force | Out-Null; "
            "$n="+ps_literal(TITLE)+"; $e="+ps_literal(exe)+"; $w="+ps_literal(target)+"; $h="+ps_literal(identity['host'])+"; $a="+ps_literal(identity['arguments'])+"; $ua="+ps_literal(identity['uninstall_arguments'])+"; "
            "$links=@((Join-Path $d ($n+'.lnk')),(Join-Path $m ($n+'.lnk'))); "
            "foreach($p in $links){$l=$s.CreateShortcut($p);$l.TargetPath=$h;$l.Arguments=$a;$l.WorkingDirectory=$w;$l.IconLocation=$e+',0';$l.Description='E-commerce Product Management System';$l.Save()}; "
            "$u=Join-Path $m 'Uninstall.lnk';$l=$s.CreateShortcut($u);if(Test-Path (Join-Path $w 'Uninstall.exe')){$l.TargetPath=$h;$l.Arguments=$ua}else{$l.TargetPath=Join-Path $w 'Uninstall-Desktop.cmd';$l.Arguments=''};$l.WorkingDirectory=$w;$l.IconLocation=$e+',0';$l.Save(); "
            "@($links)+$u | ConvertTo-Json -Compress")
    # UTF-16 encoded command preserves Chinese paths on Windows PowerShell 5.
    import base64
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',encoded],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError('Application files were installed, but Windows could not create the shortcuts. Product data was kept.')
    # Use known filesystem locations obtained through a separate JSON request,
    # avoiding console-codepage differences in the Chinese shortcut name.
    folders=powershell("$j=@{desktop=[Environment]::GetFolderPath('Desktop');start=[Environment]::GetFolderPath('StartMenu')}|ConvertTo-Json -Compress;[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($j))")
    folder_data=json.loads(base64.b64decode(folders.strip()).decode('utf-8'))
    menu=Path(folder_data['start'])/'Programs'/'Ecommerce Product Manager'
    links=[str(Path(folder_data['desktop'])/(TITLE+'.lnk')),str(menu/(TITLE+'.lnk')),str(menu/'Uninstall.lnk')]
    record['shortcuts']=links
    marker.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    if launch:
        subprocess.Popen([identity['host'],'//nologo',identity['path']],cwd=target,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'installed':str(exe),'data_dir':str(data),'runtime_dir':str(runtime),'shortcuts':links}


def uninstall(target):
    target=ordinary_path(target);marker=ordinary_path(target/MARKER)
    if not marker.is_file():raise RuntimeError('No recognized installation was found. No files were removed.')
    state=json.loads(marker.read_text(encoding='utf-8-sig'))
    if state.get('product')!='EcommerceProductManager' or Path(state.get('install_path','')).resolve()!=target:
        raise RuntimeError('Invalid installation marker. No files were removed.')
    if installation_running(target/'Midoo.exe'):
        raise RuntimeError('Close the desktop application normally before uninstalling. No processes were stopped.')
    data=ordinary_path(state['data_path'])
    default_data=ordinary_path(target/'data')
    runtime=ordinary_path(state['runtime_path']) if state.get('runtime_path') else None
    resolved=[]
    for relative in state['files']:
        if not isinstance(relative,str) or not relative.strip() or Path(relative).is_absolute():
            raise RuntimeError('Invalid installed-file entry. No files were removed.')
        item=ordinary_path(target/relative)
        if item==target or not item.is_relative_to(target) or any(item==protected or item.is_relative_to(protected) for protected in (data,default_data,runtime) if protected is not None):
            raise RuntimeError('A protected or out-of-directory file was listed. No files were removed.')
        resolved.append(item)
    # Shortcut paths are matched against this application's target before removal.
    script="$s=New-Object -ComObject WScript.Shell;$targets=@("+','.join(ps_literal(target/name) for name in ('Midoo.exe','Uninstall.exe','Uninstall-Desktop.cmd'))+");"
    identity=state.get('launcher')
    if identity:
        if not isinstance(identity,dict) or not all(isinstance(identity.get(key),str) for key in ('path','host','arguments','uninstall_arguments')):
            raise RuntimeError('Invalid launcher identity. No files were removed.')
        expected_arguments='//nologo '+windows_quote_argument(identity['path'])
        if ordinary_path(identity['path'])!=target/LAUNCHER or ordinary_path(identity['host'])!=wscript_host() or identity['arguments']!=expected_arguments or identity['uninstall_arguments']!=expected_arguments+' --uninstall':
            raise RuntimeError('Invalid launcher identity. No files were removed.')
    if identity:
        script+="$hostPath="+ps_literal(identity['host'])+";$launcherArguments=@("+ps_literal(identity['arguments'])+","+ps_literal(identity['uninstall_arguments'])+");"
    for path in state.get('shortcuts',[]):
        if not isinstance(path,str) or not Path(path).is_absolute():
            raise RuntimeError('Invalid shortcut entry. No files were removed.')
        shortcut=ordinary_path(path)
        if shortcut.suffix.lower()!='.lnk':
            raise RuntimeError('Invalid shortcut entry. No files were removed.')
        match="$targets -contains $l.TargetPath"
        if identity:match+=" -or ($l.TargetPath -eq $hostPath -and ($launcherArguments -ccontains [string]$l.Arguments))"
        script+="$p="+ps_literal(shortcut)+";if(Test-Path -LiteralPath $p){$l=$s.CreateShortcut($p);if("+match+"){Remove-Item -LiteralPath $p}};"
    powershell(script)
    for path in resolved:
        try:path.unlink(missing_ok=True)
        except PermissionError:
            if path.name.lower()!='uninstall.exe':raise
    marker.unlink()
    return {'uninstalled':str(target),'data_preserved':str(data)}


def gui(remove=False,target_dir=None,data_dir=None,runtime_dir=None):
    import tkinter as tk
    from tkinter import filedialog,messagebox
    root=tk.Tk();root.title(TITLE+' · 安装 / Setup');root.geometry('610x400');root.resizable(False,False)
    root.configure(bg='#f5f6fa')
    frame=tk.Frame(root,bg='white',padx=28,pady=24);frame.pack(fill='both',expand=True,padx=16,pady=16)
    tk.Label(frame,text=TITLE,font=('Microsoft YaHei UI',20,'bold'),bg='white',fg='#24334c').pack(anchor='w')
    tk.Label(frame,text='E-commerce Product Management System',bg='white',fg='#7f8da5').pack(anchor='w',pady=(4,12))
    tk.Label(frame,text='卸载程序和快捷方式，保留全部商品资料。\nRemove application files and shortcuts. Keep all product data.' if remove else '安装到当前用户，无需管理员权限。\nCreates desktop and Start menu shortcuts. Existing data is preserved.',justify='left',bg='white',fg='#6d7e98').pack(anchor='w')
    location=tk.StringVar(value=str(target_dir or default_install_dir()))
    tk.Entry(frame,textvariable=location).pack(fill='x',pady=(14,5),ipady=5)
    tk.Button(frame,text='选择安装位置 / Choose folder',command=lambda:location.set(filedialog.askdirectory() or location.get())).pack(anchor='w')
    tk.Label(frame,text='公开源码，仅限非商业使用；第三方组件保留原许可证。\nSource available for noncommercial use. See LICENSE and third-party notices.',justify='left',bg='white',fg='#8b96a8',font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(16,10))
    def clicked():
        button.configure(state='disabled');root.update_idletasks()
        try:
            if remove:
                uninstall(location.get())
                messagebox.showinfo('卸载完成 / Complete','程序与快捷方式已移除，商品数据保留。\nApplication removed. Product data has been preserved.',parent=root)
            else:
                install(payload_dir(),location.get(),data_dir=data_dir,launch=True,runtime_dir=runtime_dir)
                messagebox.showinfo('安装完成 / Complete','桌面快捷方式已创建。\nDesktop shortcut created. The application is opening.',parent=root)
            root.destroy()
        except Exception as exc:
            messagebox.showerror('安装未完成 / Setup failed',str(exc),parent=root);button.configure(state='normal')
    button=tk.Button(frame,text='卸载并保留数据 / Uninstall, keep data' if remove else '安装并打开 / Install and launch',command=clicked,bg='#5267eb',fg='white',padx=20,pady=8)
    button.pack(anchor='e')
    root.mainloop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true')
    parser.add_argument('--uninstall',action='store_true')
    parser.add_argument('--no-gui',action='store_true')
    parser.add_argument('--package',type=Path,default=payload_dir())
    own_uninstaller=bool(getattr(sys,'frozen',False) and Path(sys.executable).stem.lower()=='uninstall')
    parser.add_argument('--install-dir',type=Path,default=Path(sys.executable).parent if own_uninstaller else default_install_dir())
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--runtime-dir',type=Path)
    parser.add_argument('--no-launch',action='store_true')
    args=parser.parse_args()
    if args.install:
        print(json.dumps(install(args.package,args.install_dir,args.data_dir,not args.no_launch,runtime_dir=args.runtime_dir),ensure_ascii=False))
    elif args.uninstall or Path(sys.executable).stem.lower()=='uninstall':
        if args.no_gui:print(json.dumps(uninstall(args.install_dir),ensure_ascii=False))
        else:gui(remove=True,target_dir=args.install_dir)
    else:gui(target_dir=args.install_dir,data_dir=args.data_dir,runtime_dir=args.runtime_dir)


if __name__=='__main__':
    main()
