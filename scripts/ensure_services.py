"""Start the already installed local services once, hidden, under a Windows lock."""
import json,os,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMFY=Path(os.environ.get('COMFYUI_PORTABLE_ROOT',r'C:\ComfyUI\ComfyUI_windows_portable'))

def online(url):
    try:
        with urllib.request.urlopen(url,timeout=2) as response:
            return response.status==200
    except Exception:
        return False

def ensure_services():
    import msvcrt
    folder=ROOT/'storage'/'runtime'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'startup.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0');lock.flush()
        deadline=time.monotonic()+25
        while True:
            try:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                if time.monotonic()>deadline:
                    raise TimeoutError('Another local service startup is in progress')
                time.sleep(.3)
        try:
            engines=[('comfy', 'http://127.0.0.1:8188/system_stats', COMFY/'python_embeded'/'python.exe',
                      ['-s','ComfyUI/main.py','--windows-standalone-build','--listen','127.0.0.1','--port','8188',
                       '--disable-triton-backend','--use-split-cross-attention'],COMFY),
                     ('hub','http://127.0.0.1:8000/api/v1/system/health',Path(sys.executable),['main.py'],ROOT)]
            for name,url,exe,args,cwd in engines:
                if online(url):
                    continue
                pid_path=folder/(name+'.pid')
                running=False
                if pid_path.exists():
                    try:
                        pid=int(pid_path.read_text())
                        import ctypes
                        from ctypes import wintypes
                        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                        kernel.OpenProcess.restype=wintypes.HANDLE
                        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
                        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
                        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                        handle=kernel.OpenProcess(0x1000,False,pid)
                        if handle:
                            try:
                                code=wintypes.DWORD()
                                buffer=ctypes.create_unicode_buffer(32768)
                                size=wintypes.DWORD(len(buffer))
                                running=(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259
                                    and kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size))
                                    and Path(buffer.value).resolve()==exe.resolve())
                            finally:
                                kernel.CloseHandle(handle)
                    except Exception:
                        pass
                if not running:
                    if not exe.exists():
                        raise FileNotFoundError(f'Installed runtime missing: {exe}')
                    with (folder/(name+'.log')).open('ab') as log:
                        process=subprocess.Popen([str(exe),*args],cwd=str(cwd),stdin=subprocess.DEVNULL,
                            stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
                    pid_path.write_text(str(process.pid))
                until=time.monotonic()+20
                while time.monotonic()<until and not online(url):
                    time.sleep(.3)
                if not online(url):
                    raise TimeoutError(f'{name} is still starting; check {folder/(name+".log")}; no image submitted')
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

if __name__=='__main__':
    ensure_services()
    print(json.dumps({'status':'ready','hub':'http://127.0.0.1:8000','comfyui':'http://127.0.0.1:8188'}))
