"""Keeps the editor from crashing after it has finished quitting.

In Unreal 5.8 a Slate notification can still be queued on the core ticker when the editor
quits. The ticker is a C++ static, so exit() destroys it after the engine has already shut
down ICU, and freeing the notification's text then aborts in ubidi_close and opens the crash
reporter. By then the editor has saved its config, left the Multi-User session and closed
its log, so ending the process at that point loses nothing.
"""
import ctypes
import sys

import unreal


def skip_static_teardown() -> None:
    """Ends the process as soon as exit() starts running static destructors. Exit handlers
    run newest first, so this one runs before the statics the engine created while starting up.
    The process then always exits with status 0."""
    # The crash has only shown up in Mac editors
    if sys.platform != "darwin":
        return
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        register = libc["__cxa_atexit"]
        register.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        # exit() passes the handler its registered argument, which becomes _exit's status
        registered = register(ctypes.cast(libc["_exit"], ctypes.c_void_p), None, None) == 0
    except (OSError, AttributeError):
        registered = False
    if not registered:
        unreal.log_warning("CoPresence: could not register the quit crash workaround")
