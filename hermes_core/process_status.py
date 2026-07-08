"""Cross-platform process liveness helpers.

These helpers are intentionally independent of the removed messaging gateway.
They are used by terminal, browser, MCP, and process-registry code paths that
still need safe PID handling on Windows and POSIX.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"


def terminate_pid(pid: int, *, force: bool = False) -> None:
    """Terminate a PID with platform-appropriate force semantics."""
    if force and _IS_WINDOWS:
        from hermes_cli._subprocess_compat import windows_hide_flags

        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=windows_hide_flags(),
            )
        except FileNotFoundError:
            os.kill(pid, signal.SIGTERM)
            return

        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise OSError(details or f"taskkill failed for PID {pid}")
        return

    sig = signal.SIGTERM if not force else getattr(signal, "SIGKILL", signal.SIGTERM)
    os.kill(pid, sig)


def _get_process_start_time(pid: int) -> Optional[int]:
    """Return a stable per-process start-time fingerprint, or None."""
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        return int(stat_path.read_text(encoding="utf-8").split()[21])
    except (FileNotFoundError, IndexError, PermissionError, ValueError, OSError):
        pass

    try:
        import psutil  # type: ignore

        return int(round(psutil.Process(pid).create_time() * 100))
    except Exception:
        return None


def get_process_start_time(pid: int) -> Optional[int]:
    """Public wrapper for retrieving a process start time when available."""
    return _get_process_start_time(pid)


def _pid_exists(pid: int) -> bool:
    """Cross-platform PID liveness check that does not signal on Windows."""
    try:
        import psutil  # type: ignore

        try:
            if psutil.Process(int(pid)).status() == psutil.STATUS_ZOMBIE:
                return False
        except getattr(psutil, "NoSuchProcess", ()):
            return False
        except Exception:
            pass
        return bool(psutil.pid_exists(int(pid)))
    except ImportError:
        pass

    if _IS_WINDOWS:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.restype = ctypes.c_uint
            kernel32.GetLastError.restype = ctypes.c_uint
            process_query_limited_information = 0x1000
            synchronize = 0x100000
            wait_timeout = 0x00000102
            error_invalid_parameter = 87
            error_access_denied = 5
            handle = kernel32.OpenProcess(
                process_query_limited_information | synchronize, False, int(pid)
            )
            if not handle:
                err = kernel32.GetLastError()
                if err == error_invalid_parameter:
                    return False
                if err == error_access_denied:
                    return True
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError):
            return False

    try:
        stat_fields = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8").split()
        if len(stat_fields) > 2 and stat_fields[2] == "Z":
            return False
    except FileNotFoundError:
        try:
            result = subprocess.run(
                ["ps", "-o", "state=", "-p", str(int(pid))],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip().startswith("Z"):
                return False
        except Exception:
            pass
    except (IndexError, PermissionError, OSError):
        pass

    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
