from __future__ import annotations

import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil


WINDOWS_CONTROLLERS = {
    "aida64.exe": "AIDA64 ROG AIO LCD",
    "armourycrate.exe": "Armoury Crate",
    "armourycrate.usersessionhelper.exe": "Armoury Crate helper",
    "armouryhtmldebugserver.exe": "Armoury device page",
    "armourysocketserver.exe": "Armoury socket service",
    "asus_framework.exe": "ASUS device framework",
}
WINDOWS_ARMOURY_CONTROLLERS = {
    "armourycrate.exe",
    "armourycrate.usersessionhelper.exe",
    "armouryhtmldebugserver.exe",
    "armourysocketserver.exe",
    "asus_framework.exe",
}
LINUX_CONTROLLERS = {
    "liquidctl": "liquidctl",
    "coolercontrold": "CoolerControl daemon",
    "coolercontrol": "CoolerControl UI",
    "openrgb": "OpenRGB",
}


@dataclass
class Conflict:
    pid: int
    process: str
    description: str


def armoury_ryujin_modules_present() -> bool:
    if os.name != "nt":
        return False
    return any(
        path.exists()
        for path in (
            Path(r"C:\Program Files\ASUS\Aac_AIOFan\AacAIOFanHal_x64.dll"),
            Path(r"C:\Program Files\ASUS\Aac_AIOFan\AacAIOFanHal_x86.dll"),
            Path(r"C:\Program Files (x86)\ASUS\ArmouryDevice\View\6536"),
        )
    )


def aida_lcd_output_disabled() -> bool:
    """AIDA64 can coexist when it only exports sensors and ROG LCD is off."""
    if os.name != "nt":
        return False
    try:
        config = Path(r"C:\Program Files (x86)\FinalWire\AIDA64 Extreme\aida64.ini").read_text(
            encoding="mbcs", errors="ignore"
        )
        return "HWMonEnableRALCD=0" in config
    except OSError:
        return False


def process_conflicts() -> list[Conflict]:
    names = WINDOWS_CONTROLLERS if os.name == "nt" else LINUX_CONTROLLERS
    ryujin_armoury_active = armoury_ryujin_modules_present()
    found: list[Conflict] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
            if os.name == "nt" and name in WINDOWS_ARMOURY_CONTROLLERS and not ryujin_armoury_active:
                continue
            if os.name == "nt" and name == "aida64.exe" and aida_lcd_output_disabled():
                continue
            if name in names:
                found.append(Conflict(proc.info["pid"], name, names[name]))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return found


def stop_process_conflicts() -> dict:
    """Stop only the known LCD/UI controller processes after user consent."""
    requested = process_conflicts()
    errors: list[str] = []
    must_stop_armoury = any(x.process in WINDOWS_ARMOURY_CONTROLLERS for x in requested)
    if os.name == "nt" and must_stop_armoury:
        try:
            result = subprocess.run(
                ["sc.exe", "stop", "ArmouryCrateService"],
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
            # 1062 means the service was already stopped.
            if result.returncode not in (0, 1062) and "1062" not in (result.stdout + result.stderr):
                errors.append("无法停止 ArmouryCrateService；可能需要管理员权限")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"停止 ArmouryCrateService 失败：{exc}")

    for item in requested:
        try:
            psutil.Process(item.pid).kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            errors.append(f"无权结束 {item.process} (PID {item.pid})")
        except OSError as exc:
            errors.append(f"结束 {item.process} (PID {item.pid}) 失败：{exc}")

    deadline = time.monotonic() + 4
    remaining = process_conflicts()
    while remaining and time.monotonic() < deadline:
        time.sleep(0.2)
        remaining = process_conflicts()
    return {
        "requested": [asdict(x) for x in requested],
        "remaining": [asdict(x) for x in remaining],
        "errors": errors,
    }


def linux_open_handles() -> list[str]:
    if os.name == "nt":
        return []
    matches: list[str] = []
    for pattern in ("/dev/hidraw*", "/dev/bus/usb/*/*"):
        try:
            out = subprocess.run(
                ["sh", "-c", f"command -v fuser >/dev/null && fuser {pattern} 2>/dev/null || true"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
            if out:
                matches.append(f"{pattern}: {out}")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return matches


def diagnostic() -> dict:
    return {
        "platform": platform.platform(),
        "armoury_ryujin_modules_present": armoury_ryujin_modules_present(),
        "process_conflicts": [asdict(x) for x in process_conflicts()],
        "linux_open_handles": linux_open_handles(),
        "note": "检测结果是保守名单；启动 LCD 前应只保留一个控制端。",
    }


class InstanceLock:
    def __init__(self) -> None:
        self._file = None
        self.path = Path(os.environ.get("TEMP", "/tmp")) / "ryujin-lcd.lock"

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Linux flock locks the open file descriptor, not its contents.  Open
        # an existing lock read-only so an application launched with sudo can
        # share a lock file created by the desktop user (notably on root-squash
        # or similarly restricted temporary filesystems).
        if os.name == "nt":
            self._file = self.path.open("a+b")
        else:
            try:
                self._file = self.path.open("rb")
            except FileNotFoundError:
                self.path.touch(exist_ok=True)
                self._file = self.path.open("rb")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            raise RuntimeError("另一个 ryujin-lcd 实例已经持有屏幕锁") from exc

    def release(self) -> None:
        if not self._file:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
