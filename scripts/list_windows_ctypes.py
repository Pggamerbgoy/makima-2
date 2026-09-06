import ctypes
from ctypes import wintypes
import psutil

user32 = ctypes.windll.user32

# On 64-bit Windows, LPARAM is LONG_PTR (c_ssize_t)
LPARAM = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, LPARAM)

def list_windows():
    results = []
    
    @WNDENUMPROC
    def enum_cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    pname = psutil.Process(pid.value).name()
                except Exception:
                    pname = "unknown"
                results.append((hwnd, pid.value, pname, buf.value))
        return True

    user32.EnumWindows(enum_cb, 0)
    for hwnd, pid, pname, title in results:
        print(f"HWND: {hwnd} | PID: {pid} ({pname}) | TITLE: {title}")

if __name__ == "__main__":
    list_windows()
