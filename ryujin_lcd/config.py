from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


STAT_MODULE_IDS = ("temperature", "load", "clock", "voltage", "power")
DEFAULT_STAT_MODULES = list(STAT_MODULE_IDS)


def normalize_modules(value: object) -> list[str]:
    """Keep a unique, ordered list; its index is the LCD module position."""
    if not isinstance(value, list):
        return list(DEFAULT_STAT_MODULES)
    result = [item for item in value if isinstance(item, str) and item in STAT_MODULE_IDS]
    return list(dict.fromkeys(result))


def app_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "ryujin-lcd"


def bundled_background() -> Path | None:
    roots = [
        Path(__file__).resolve().parent,
        Path(getattr(sys, "_MEIPASS", "")) / "ryujin_lcd",
        Path(sys.executable).resolve().parent / "_internal" / "ryujin_lcd",
    ]
    return next(
        (root / "assets" / "background.png" for root in roots
         if (root / "assets" / "background.png").is_file()),
        None,
    )


def ensure_default_background() -> Path | None:
    """Install the bundled background into the per-user writable directory."""
    source = bundled_background()
    if source is None:
        return None
    target = app_dir() / "background.png"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target


@dataclass
class Settings:
    width: int = 320
    height: int = 240
    refresh_seconds: float = 2.0
    animation_fps: float = 24.0
    background: str = ""
    device_index: int = 0
    pixel_order: str = "BGR"
    linux_experimental_usb: bool = False
    windows_backend: str = "direct"
    direct_helper_path: str = ""
    sdk_path: str = r"C:\Program Files (x86)\FinalWire\AIDA64 Extreme\ROGAIOSDK.dll"
    helper_path: str = ""
    cpu_modules: list[str] = field(default_factory=lambda: list(DEFAULT_STAT_MODULES))
    gpu_modules: list[str] = field(default_factory=lambda: list(DEFAULT_STAT_MODULES))

    @classmethod
    def load(cls) -> "Settings":
        path = app_dir() / "config.json"
        default_background = ensure_default_background()
        if not path.exists():
            return cls(background=str(default_background) if default_background else "")
        values = json.loads(path.read_text(encoding="utf-8"))
        allowed = cls.__dataclass_fields__.keys()
        settings = cls(**{k: v for k, v in values.items() if k in allowed})
        settings.cpu_modules = normalize_modules(settings.cpu_modules)
        settings.gpu_modules = normalize_modules(settings.gpu_modules)
        if (not settings.background or not Path(settings.background).is_file()) and default_background:
            settings.background = str(default_background)
        return settings

    def save(self) -> None:
        root = app_dir()
        root.mkdir(parents=True, exist_ok=True)
        (root / "config.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
