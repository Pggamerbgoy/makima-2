import win32gui
import win32process
import psutil

def list_windows():
    print("=== VISIBLE WINDOWS ===")
    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            txt = win32gui.GetWindowText(hwnd)
            if txt:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    pname = psutil.Process(pid).name()
                except Exception:
                    pname = "unknown"
                print(f"HWND: {hwnd} | PID: {pid} ({pname}) | TITLE: {txt}")
        return True
    win32gui.EnumWindows(enum_cb, None)

if __name__ == "__main__":
    list_windows()
