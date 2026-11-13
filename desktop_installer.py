"""Current-user Windows installer; never stops services or deletes product data."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TITLE = '电商产品管理系统'
MARKER = '.ecommerce-product-manager-install.json'
ALLOW_FILES = {'Midoo.exe','Uninstall.exe','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md','README.en.md','VERSION','Install-Desktop.cmd','Uninstall-Desktop.cmd'}
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
    return raw.resolve()


def install(package, target, data_dir=None, launch=True):
    package=ordinary_path(package);target=ordinary_path(target)
    if not (package/'Midoo.exe').is_file():
        raise RuntimeError('The complete application payload is missing.')
    if target==Path(target.anchor) or target.is_symlink() or (hasattr(target,'is_junction') and target.is_junction()):
        raise RuntimeError('Choose an ordinary installation directory, not a drive root or link.')
    marker=target/MARKER
    if (target/'Midoo.exe').exists() and not marker.is_file():
        raise RuntimeError('An unrecognized application already exists in this directory. Choose another location.')
    if marker.is_file():
        state=json.loads(marker.read_text(encoding='utf-8-sig'))
        if state.get('product')!='EcommerceProductManager' or Path(state.get('install_path','')).resolve()!=target:
            raise RuntimeError('Invalid installation marker. Choose another directory.')
    if installation_running(target/'Midoo.exe'):
        raise RuntimeError('Please close this installed desktop application normally before updating it. No processes were stopped.')
    previous_data=None
    if not data_dir and marker.is_file() and (target/'desktop-config.json').is_file():
        previous_data=json.loads((target/'desktop-config.json').read_text(encoding='utf-8-sig')).get('data_dir')
    selected=data_dir or previous_data
    data=Path(selected).expanduser() if selected else target/'data'
    if not data.is_absolute():data=target/data
    data=data.resolve()
    if data_dir and not data.is_dir():
        raise RuntimeError('The existing product data directory was not found. Check the selected location.')
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
        copies.append((source,destination,relative))
    ordinary_path(marker);ordinary_path(target/'desktop-config.json')
    target.mkdir(parents=True,exist_ok=True)
    for source,destination,relative in copies:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if source!=destination:shutil.copy2(source,destination)
        files.append(str(relative))
    data.mkdir(parents=True,exist_ok=True)
    (target/'desktop-config.json').write_text(json.dumps({'data_dir':str(data)},ensure_ascii=False,indent=2),encoding='utf-8')
    files.append('desktop-config.json')
    marker.write_text(json.dumps({'product':'EcommerceProductManager','schema':1,'install_path':str(target),'data_path':str(data),'files':files,'shortcuts':[]},ensure_ascii=False,indent=2),encoding='utf-8')
    exe=target/'Midoo.exe'
    script=("$s=New-Object -ComObject WScript.Shell; "
            "$d=[Environment]::GetFolderPath('Desktop'); "
            "$m=Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\\Ecommerce Product Manager'; "
            "New-Item -ItemType Directory -Path $m -Force | Out-Null; "
            "$n="+ps_literal(TITLE)+"; $e="+ps_literal(exe)+"; $w="+ps_literal(target)+"; "
            "$links=@((Join-Path $d ($n+'.lnk')),(Join-Path $m ($n+'.lnk'))); "
            "foreach($p in $links){$l=$s.CreateShortcut($p);$l.TargetPath=$e;$l.WorkingDirectory=$w;$l.IconLocation=$e+',0';$l.Description='E-commerce Product Management System';$l.Save()}; "
            "$u=Join-Path $m 'Uninstall.lnk';$l=$s.CreateShortcut($u);if(Test-Path (Join-Path $w 'Uninstall.exe')){$l.TargetPath=Join-Path $w 'Uninstall.exe';$l.Arguments='--uninstall'}else{$l.TargetPath=Join-Path $w 'Uninstall-Desktop.cmd'};$l.WorkingDirectory=$w;$l.Save(); "
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
    marker.write_text(json.dumps({'product':'EcommerceProductManager','schema':1,'install_path':str(target),'data_path':str(data),'files':files,'shortcuts':links},ensure_ascii=False,indent=2),encoding='utf-8')
    if launch:
        subprocess.Popen([str(exe)],cwd=target,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'installed':str(exe),'data_dir':str(data),'shortcuts':links}


def uninstall(target):
    target=ordinary_path(target);marker=ordinary_path(target/MARKER)
    if not marker.is_file():raise RuntimeError('No recognized installation was found. No files were removed.')
    state=json.loads(marker.read_text(encoding='utf-8-sig'))
    if state.get('product')!='EcommerceProductManager' or Path(state.get('install_path','')).resolve()!=target:
        raise RuntimeError('Invalid installation marker. No files were removed.')
    if installation_running(target/'Midoo.exe'):
        raise RuntimeError('Close the desktop application normally before uninstalling. No processes were stopped.')
    data=Path(state['data_path']).resolve()
    resolved=[]
    for relative in state['files']:
        item=ordinary_path(target/relative)
        if not item.is_relative_to(target) or item==data or item.is_relative_to(data):
            raise RuntimeError('A protected or out-of-directory file was listed. No files were removed.')
        resolved.append(item)
    # Shortcut paths are matched against this application's target before removal.
    script="$s=New-Object -ComObject WScript.Shell;$targets=@("+','.join(ps_literal(target/name) for name in ('Midoo.exe','Uninstall.exe','Uninstall-Desktop.cmd'))+");"
    for path in state.get('shortcuts',[]):
        script+="$p="+ps_literal(path)+";if(Test-Path -LiteralPath $p){$l=$s.CreateShortcut($p);if($targets -contains $l.TargetPath){Remove-Item -LiteralPath $p}};"
    powershell(script)
    for path in resolved:
        try:path.unlink(missing_ok=True)
        except PermissionError:
            if path.name.lower()!='uninstall.exe':raise
    marker.unlink()
    return {'uninstalled':str(target),'data_preserved':str(data)}


def gui(remove=False,target_dir=None):
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
                install(payload_dir(),location.get(),launch=True)
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
    parser.add_argument('--no-launch',action='store_true')
    args=parser.parse_args()
    if args.install:
        print(json.dumps(install(args.package,args.install_dir,args.data_dir,not args.no_launch),ensure_ascii=False))
    elif args.uninstall or Path(sys.executable).stem.lower()=='uninstall':
        if args.no_gui:print(json.dumps(uninstall(args.install_dir),ensure_ascii=False))
        else:gui(remove=True,target_dir=args.install_dir)
    else:gui(target_dir=args.install_dir)


if __name__=='__main__':
    main()
