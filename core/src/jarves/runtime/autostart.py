"""Start JARVES at login: one small adapter per platform (the user's choice; default off).

Windows: a per-user `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` value. Chosen over a Startup-folder
shortcut because it needs no COM/pywin32 to write, no admin rights, and Windows lists it under Settings > Apps >
Startup / Task Manager > Startup apps, where the user can also switch it off. The command runs `pythonw.exe`
(no console window) with `-m jarves run --background`; a second Core is refused by the instance lock.
Linux / Raspberry Pi: a systemd *user* service (~/.config/systemd/user/jarves.service, non-root) started with the
user session. IMPLEMENTED BUT UNVERIFIED on a Pi (no device yet); headless boot without a login needs
`loginctl enable-linger <user>`, which JARVES never runs by itself.

Every adapter: enabled() -> bool, enable() -> str (what was written), disable() -> bool, describe() -> dict.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "JARVES"
UNIT = "jarves.service"


def background_python() -> Path:
    """The interpreter for a window-less Core: pythonw.exe next to python.exe on Windows (no console)."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.is_file():
            return w
    return exe


def _profile() -> str:
    from ..profiles import DEFAULT, current_name, ProfileError
    try:
        return current_name()
    except ProfileError:
        return DEFAULT


def core_command(extra: list[str] | None = None) -> list[str]:
    """The background Core of the CURRENT profile (Issue #17: the default profile's command is unchanged)."""
    from ..profiles import DEFAULT
    p = _profile()
    prof = [] if p == DEFAULT else ["--profile", p]
    return [str(background_python()), "-m", "jarves", *prof, "run", "--background", *(extra or [])]


def entry_name(base: str) -> str:
    """Run-key value / systemd unit name: one per profile ("JARVES", "JARVES-dana"; jarves.service, jarves-dana...)."""
    from ..profiles import DEFAULT
    p = _profile()
    if p == DEFAULT:
        return base
    stem, dot, ext = base.partition(".")
    return f"{stem}-{p}{dot}{ext}"


class _WinReg:
    """The real HKCU Run key (tests pass a dict-like fake instead)."""

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> bool:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
            return True
        except OSError:
            return False


class WindowsAutostart:
    platform = "windows"

    def __init__(self, reg=None, command: list[str] | None = None) -> None:
        self.reg = reg if reg is not None else _WinReg()      # `is not None`: an empty fake registry is falsy
        self.command = command or core_command()

    def command_line(self) -> str:
        return subprocess.list2cmdline(self.command)

    def enabled(self) -> bool:
        return bool(self.reg.get(entry_name(VALUE)))

    def enable(self) -> str:
        line = self.command_line()
        self.reg.set(entry_name(VALUE), line)
        return f"HKCU\\{RUN_KEY}\\{entry_name(VALUE)} = {line}"

    def disable(self) -> bool:
        return self.reg.delete(entry_name(VALUE))

    def describe(self) -> dict:
        cur = self.reg.get(entry_name(VALUE))
        return {"platform": self.platform, "mechanism": "HKCU Run key", "enabled": bool(cur), "entry": cur,
                "current_install": cur == self.command_line() if cur else None}


Runner = Callable[[list[str]], int]


def _systemctl(args: list[str]) -> int:
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, timeout=20).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


class SystemdUserAutostart:
    platform = "linux"

    def __init__(self, unit_dir: Path | None = None, command: list[str] | None = None,
                 runner: Runner | None = None) -> None:
        cfg_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.unit_dir = unit_dir or cfg_home / "systemd" / "user"
        self.command = command or core_command()
        self.run = runner or _systemctl

    @property
    def path(self) -> Path:
        return self.unit_dir / entry_name(UNIT)

    def unit_text(self) -> str:
        exec_start = " ".join(shlex.quote(c) for c in self.command)
        return ("[Unit]\n"
                "Description=JARVES assistant Core (background, local wake word)\n"
                "After=default.target pipewire.service pulseaudio.service\n\n"
                "[Service]\n"
                "Type=simple\n"
                f"ExecStart={exec_start}\n"
                "Restart=on-failure\n"
                "RestartSec=10\n"
                "# exit 3 = another Core already runs for this user (instance lock): not a failure to restart\n"
                "RestartPreventExitStatus=3\n"
                "Environment=PYTHONUNBUFFERED=1\n\n"
                "[Install]\n"
                "WantedBy=default.target\n")

    def enabled(self) -> bool:
        return self.path.is_file() and (self.unit_dir / "default.target.wants" / entry_name(UNIT)).exists()

    def enable(self) -> str:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.unit_text(), encoding="utf-8")
        self.run(["daemon-reload"])
        rc = self.run(["enable", entry_name(UNIT)])
        if rc != 0:     # no systemd user manager (container, WSL): link it ourselves, as `enable` would
            wants = self.unit_dir / "default.target.wants"
            wants.mkdir(exist_ok=True)
            link = wants / entry_name(UNIT)
            if not link.exists():
                try:
                    link.symlink_to(self.path)
                except OSError:
                    link.write_text(self.unit_text(), encoding="utf-8")
        return str(self.path)

    def disable(self) -> bool:
        had = self.path.exists()
        self.run(["disable", entry_name(UNIT)])
        for p in (self.unit_dir / "default.target.wants" / entry_name(UNIT), self.path):
            try:
                p.unlink()
            except OSError:
                pass
        self.run(["daemon-reload"])
        return had

    def describe(self) -> dict:
        return {"platform": self.platform, "mechanism": "systemd user service", "enabled": self.enabled(),
                "entry": str(self.path) if self.path.exists() else None, "verified": False,
                "note": "headless boot without login also needs: loginctl enable-linger $USER"}


class Unsupported:
    platform = sys.platform

    def enabled(self) -> bool:
        return False

    def enable(self) -> str:
        raise NotImplementedError(f"autostart is not supported on {sys.platform}")

    def disable(self) -> bool:
        return False

    def describe(self) -> dict:
        return {"platform": self.platform, "mechanism": None, "enabled": False}


def adapter():
    if sys.platform == "win32":
        return WindowsAutostart()
    if sys.platform.startswith("linux"):
        return SystemdUserAutostart()
    return Unsupported()
