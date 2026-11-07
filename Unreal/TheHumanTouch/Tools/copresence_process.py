import ctypes
import os
import platform
import shutil
import sys
from pathlib import Path


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def brain_executable(brain: str) -> str | None:
    """Where Claude Code or Codex is installed, as a program that starts directly with its arguments,
    or None. On Windows npm installs Codex behind a .cmd wrapper, which would hand everything people
    say to the agent to cmd.exe to parse, so the program inside the npm package is used instead."""
    if sys.platform != "win32":
        return shutil.which(brain)
    found = shutil.which(f"{brain}.exe")
    if found:
        return found
    wrapper = shutil.which(f"{brain}.cmd")
    if wrapper is None:
        return None
    target = "aarch64-pc-windows-msvc" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64-pc-windows-msvc"
    native = Path(wrapper).parent / "node_modules" / "@openai" / brain / "vendor" / target / brain / f"{brain}.exe"
    return str(native) if native.exists() else None
