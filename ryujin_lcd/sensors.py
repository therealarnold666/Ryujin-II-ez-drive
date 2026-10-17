from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .sensor_providers import SensorProvider, apply_providers

_lhm_cache: tuple[float, dict] = (0.0, {})
_helper_cache: tuple[float, dict] = (0.0, {})
_aida_started = False
_pdh_query = None
_pdh_counter = None
_pdh_primed = False
_pdh_lock = threading.Lock()
_rapl_cache: dict[str, tuple[float, int]] = {}
_cpu_name_cache: str | None = None


def _hidden_subprocess_kwargs() -> dict:
    """Prevent periodic Windows telemetry commands from flashing a console."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


@dataclass
class Metrics:
    cpu_name: str | None = None
    cpu_usage: float = 0.0
    cpu_freq_mhz: float | None = None
    cpu_temp_c: float | None = None
    cpu_voltage_v: float | None = None
    cpu_power_w: float | None = None
    gpu_name: str | None = None
    gpu_usage: float | None = None
    gpu_freq_mhz: float | None = None
    gpu_temp_c: float | None = None
    gpu_voltage_v: float | None = None
    gpu_power_w: float | None = None
    gpu_memory_used_mb: float | None = None
    gpu_memory_total_mb: float | None = None
    coolant_temp_c: float | None = None
    pump_rpm: int | None = None
    internal_fan_rpm: int | None = None
    memory_usage: float = 0.0
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    sources: dict[str, str] = field(default_factory=dict)
    gpus: list[dict] = field(default_factory=list)


def _cpu_name() -> str | None:
    """Return the OS CPU model name, including Linux where platform may be blank."""
    global _cpu_name_cache
    if _cpu_name_cache is not None:
        return _cpu_name_cache
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
                if value:
                    _cpu_name_cache = value
                    return _cpu_name_cache
        except (FileNotFoundError, OSError):
            pass
    if sys.platform == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().lower() == "model name" and value.strip():
                    _cpu_name_cache = value.strip()
                    return _cpu_name_cache
        except OSError:
            pass
    return None


def _temperatures() -> tuple[float | None, float | None, int | None, int | None]:
    cpu_temp = coolant = None
    pump = internal = None
    try:
        values = psutil.sensors_temperatures(fahrenheit=False)
    except (AttributeError, OSError):
        values = {}
    preferred = ("coretemp", "k10temp", "zenpower", "cpu_thermal")
    for name in preferred:
        entries = values.get(name, [])
        valid = [x.current for x in entries if x.current is not None]
        if valid:
            cpu_temp = max(valid)
            break
    if os.name != "nt":
        for node in Path("/sys/class/hwmon").glob("hwmon*"):
            try:
                # Module name: asus_rog_ryujin; registered hwmon name: rog_ryujin.
                if (node / "name").read_text().strip() not in {"rog_ryujin", "asus_rog_ryujin"}:
                    continue
                coolant = int((node / "temp1_input").read_text()) / 1000
                pump = int((node / "fan1_input").read_text())
                internal = int((node / "fan2_input").read_text())
            except (OSError, ValueError):
                pass
    return cpu_temp, coolant, pump, internal


def _nvidia() -> dict:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    fields = "index,uuid,name,temperature.gpu,utilization.gpu,clocks.current.graphics,memory.used,memory.total,power.draw"
    try:
        output = subprocess.run(
            [exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
            **_hidden_subprocess_kwargs(),
        ).stdout.splitlines()
        gpus = []
        def number(value):
            try:
                return float(value)
            except ValueError:
                return None
        for line in output:
            parts = [x.strip() for x in line.split(",")]
            if len(parts) != 9:
                continue
            index, uuid, name, temp, usage, clock, used, total, power = parts
            gpus.append({
                "index": int(index), "uuid": uuid, "gpu_name": name,
                "gpu_temp_c": number(temp), "gpu_usage": number(usage),
                "gpu_freq_mhz": number(clock), "gpu_memory_used_mb": number(used),
                "gpu_memory_total_mb": number(total), "gpu_power_w": number(power),
                "gpu_voltage_v": None,
            })
        if not gpus:
            return {}
        return {**{k: v for k, v in gpus[0].items() if k.startswith("gpu_")}, "gpus": gpus}
    except (IndexError, OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _windows_realtime_cpu_freq(base_mhz: float | None) -> float | None:
    """Use Windows' processor-performance counter to include Ryzen boost.

    psutil/CallNtPowerInformation reports the 9800X3D nominal 4700 MHz on
    this machine. Windows exposes the active P-state as a percentage of that
    nominal clock, so 112% correctly represents about 5.26 GHz.
    """
    global _pdh_query, _pdh_counter, _pdh_primed
    if os.name != "nt" or not base_mhz:
        return base_mhz
    try:
        import ctypes
        from ctypes import wintypes

        class _ValueUnion(ctypes.Union):
            _fields_ = [
                ("long_value", wintypes.LONG),
                ("double_value", ctypes.c_double),
                ("large_value", ctypes.c_longlong),
                ("ansi_string", ctypes.c_char_p),
                ("wide_string", wintypes.LPWSTR),
            ]

        class _CounterValue(ctypes.Structure):
            _fields_ = [("status", wintypes.DWORD), ("value", _ValueUnion)]

        with _pdh_lock:
            pdh = ctypes.WinDLL("pdh")
            pdh.PdhOpenQueryW.argtypes = [
                wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)
            ]
            pdh.PdhOpenQueryW.restype = wintypes.LONG
            pdh.PdhAddEnglishCounterW.argtypes = [
                ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
            pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
            pdh.PdhCollectQueryData.restype = wintypes.LONG
            pdh.PdhGetFormattedCounterValue.argtypes = [
                ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(_CounterValue),
            ]
            pdh.PdhGetFormattedCounterValue.restype = wintypes.LONG

            if _pdh_query is None:
                query, counter = ctypes.c_void_p(), ctypes.c_void_p()
                if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
                    return base_mhz
                path = r"\Processor Information(_Total)\% Processor Performance"
                if pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter)) != 0:
                    pdh.PdhCloseQuery(query)
                    return base_mhz
                _pdh_query, _pdh_counter = query, counter

            if pdh.PdhCollectQueryData(_pdh_query) != 0:
                return base_mhz
            if not _pdh_primed:
                time.sleep(0.1)
                if pdh.PdhCollectQueryData(_pdh_query) != 0:
                    return base_mhz
                _pdh_primed = True
            value, value_type = _CounterValue(), wintypes.DWORD()
            result = pdh.PdhGetFormattedCounterValue(
                _pdh_counter, 0x00000200, ctypes.byref(value_type), ctypes.byref(value)
            )
            performance = value.value.double_value
            if result == 0 and value.status == 0 and 1.0 <= performance <= 250.0:
                return base_mhz * performance / 100.0
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return base_mhz


def _windows_lhm() -> dict:
    """Read LibreHardwareMonitor's WMI provider when the user runs it.

    This is optional and read-only. A short cache avoids starting PowerShell for
    every request when the UI and LCD loop refresh at nearly the same time.
    """
    global _lhm_cache
    if os.name != "nt":
        return {}
    now = time.monotonic()
    if now - _lhm_cache[0] < 3:
        return _lhm_cache[1]
    script = (
        "$ErrorActionPreference='Stop';"
        "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor |"
        "Where-Object {$_.Value -ne $null} |"
        "Select-Object Name,SensorType,Value,Identifier,Parent | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=4,
            **_hidden_subprocess_kwargs(),
        ).stdout.strip()
        rows = json.loads(raw) if raw else []
        if isinstance(rows, dict):
            rows = [rows]
        result: dict = {}
        cpu_temps: list[float] = []
        gpu_temps: list[float] = []
        gpu_loads: list[float] = []
        gpu_clocks: list[float] = []
        cpu_powers: list[float] = []
        gpu_powers: list[float] = []
        for row in rows:
            ident = str(row.get("Identifier") or row.get("Parent") or "").lower()
            kind = str(row.get("SensorType") or "").lower()
            name = str(row.get("Name") or "").lower()
            value = float(row["Value"])
            if "cpu" in ident and kind == "temperature" and ("package" in name or "core" in name or "tdie" in name):
                cpu_temps.append(value)
            if "cpu" in ident and kind == "power" and ("package" in name or "cpu" in name):
                cpu_powers.append(value)
            if "gpu" in ident:
                if kind == "temperature" and ("core" in name or "gpu" in name):
                    gpu_temps.append(value)
                elif kind == "load" and (name in {"gpu core", "gpu d3d 3d", "gpu"} or "core" in name):
                    gpu_loads.append(value)
                elif kind == "clock" and ("core" in name or "graphics" in name):
                    gpu_clocks.append(value)
                elif kind == "power" and ("gpu" in name or "package" in name or "total" in name):
                    gpu_powers.append(value)
        if cpu_temps:
            result["cpu_temp_c"] = max(cpu_temps)
        if gpu_temps:
            result["gpu_temp_c"] = max(gpu_temps)
        if gpu_loads:
            result["gpu_usage"] = max(gpu_loads)
        if gpu_clocks:
            result["gpu_freq_mhz"] = max(gpu_clocks)
        if cpu_powers:
            result["cpu_power_w"] = max(cpu_powers)
        if gpu_powers:
            result["gpu_power_w"] = max(gpu_powers)
        _lhm_cache = (now, result)
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        _lhm_cache = (now, {})
        return {}


def _sensor_helper() -> dict:
    """Read the bundled official LibreHardwareMonitor sensor helper."""
    global _helper_cache
    if os.name != "nt":
        return {}
    now = time.monotonic()
    if now - _helper_cache[0] < 3:
        return _helper_cache[1]
    roots = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(__file__).resolve().parent.parent,
        Path(sys.executable).resolve().parent / "_internal",
    ]
    helper = next(
        (root / "native" / "sensors" / "ryujin_sensor_helper.exe" for root in roots
         if (root / "native" / "sensors" / "ryujin_sensor_helper.exe").exists()),
        None,
    )
    if helper is None:
        _helper_cache = (now, {})
        return {}
    try:
        run = subprocess.run(
            [str(helper)], cwd=str(helper.parent), capture_output=True,
            text=True, check=False, timeout=8,
            **_hidden_subprocess_kwargs(),
        )
        raw = json.loads(run.stdout.strip()) if run.returncode == 0 and run.stdout.strip() else {}
        result = {key: value for key, value in raw.items() if value is not None}
        _helper_cache = (now, result)
        return result
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        _helper_cache = (now, {})
        return {}


def short_cpu_name(name: str | None) -> str:
    if not name:
        return "CPU"
    import re
    match = re.search(r"\b(\d{4,5}X3D)\b", name, re.IGNORECASE)
    return match.group(1).upper() if match else name[:18]


def short_gpu_name(name: str | None) -> str:
    if not name:
        return "GPU"
    import re
    match = re.search(r"\bRTX\s*\d{4}\b", name, re.IGNORECASE)
    return " ".join(match.group(0).upper().split()) if match else name[:18]


def _aida_registry() -> dict:
    """Use AIDA64 as a read-only CPU fallback; its ROG LCD output stays off."""
    global _aida_started
    if os.name != "nt":
        return {}
    aida = Path(r"C:\Program Files (x86)\FinalWire\AIDA64 Extreme\aida64.exe")
    ini = aida.with_name("aida64.ini")
    try:
        config = ini.read_text(encoding="mbcs", errors="ignore")
        safe = "HWMonEnableRALCD=0" in config and "HWMonEnableRegistry=1" in config
    except OSError:
        safe = False
    if safe and not _aida_started and not any(
        (proc.info.get("name") or "").lower() == "aida64.exe"
        for proc in psutil.process_iter(["name"])
    ):
        _aida_started = True
        try:
            subprocess.Popen(
                [str(aida), "/SILENT"], cwd=str(aida.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\FinalWire\AIDA64\SensorValues") as key:
            def value(*names: str) -> float | None:
                for name in names:
                    try:
                        return float(winreg.QueryValueEx(key, f"Value.{name}")[0])
                    except (FileNotFoundError, OSError, TypeError, ValueError):
                        pass
                return None
            result = {
                "cpu_temp_c": value("TCPUPKG", "TCPUDIO", "TCPU", "TCPUTCTL"),
                "cpu_voltage_v": value("VCPU", "VCPUVID"),
                "cpu_power_w": value("PCPUPKG"),
                "gpu_temp_c": value("TGPU1"),
                "gpu_voltage_v": value("VGPU1"),
                "gpu_power_w": value("PGPU1"),
            }
            return {key: item for key, item in result.items() if item is not None}
    except (FileNotFoundError, OSError):
        return {}


def _linux_cpu_power() -> float | None:
    """Read package power from powercap energy deltas, then hwmon power."""
    if os.name == "nt":
        return None
    now = time.monotonic()
    for energy_path in Path("/sys/class/powercap").glob("**/energy_uj"):
        try:
            energy = int(energy_path.read_text().strip())
            key = str(energy_path)
            previous = _rapl_cache.get(key)
            _rapl_cache[key] = (now, energy)
            if previous and now > previous[0]:
                delta = energy - previous[1]
                if delta < 0:
                    maximum = int((energy_path.parent / "max_energy_range_uj").read_text().strip())
                    delta += maximum
                watts = delta / 1_000_000 / (now - previous[0])
                if 0 < watts < 1000:
                    return watts
        except (OSError, ValueError):
            continue
    for node in Path("/sys/class/hwmon").glob("hwmon*/power*_input"):
        try:
            watts = int(node.read_text().strip()) / 1_000_000
            if 0 < watts < 1000:
                return watts
        except (OSError, ValueError):
            continue
    return None


def _system_metrics() -> dict:
    freq = psutil.cpu_freq()
    cpu_freq_mhz = _windows_realtime_cpu_freq(freq.current if freq else None)
    memory = psutil.virtual_memory()
    cpu_temp, coolant, pump, internal = _temperatures()
    return {
        "cpu_name": _cpu_name(),
        "cpu_usage": psutil.cpu_percent(interval=None),
        "cpu_freq_mhz": cpu_freq_mhz,
        "cpu_temp_c": cpu_temp,
        "cpu_power_w": _linux_cpu_power(),
        "coolant_temp_c": coolant,
        "pump_rpm": pump,
        "internal_fan_rpm": internal,
        "memory_usage": memory.percent,
        "memory_used_mb": (memory.total - memory.available) / (1024 * 1024),
        "memory_total_mb": memory.total / (1024 * 1024),
    }


def collect() -> Metrics:
    metrics = Metrics()
    primary_providers = [
        SensorProvider("system", "psutil, PDH and Linux hwmon/powercap", _system_metrics),
        SensorProvider("aida64", "read-only AIDA64 Registry fallback", _aida_registry, os.name == "nt"),
        SensorProvider("nvidia", "nvidia-smi telemetry including power.draw", _nvidia),
    ]
    metrics.sources.update(apply_providers(metrics, primary_providers))
    multi_gpu = len(metrics.gpus) > 1
    if multi_gpu:
        # Unmatched registry/helper GPU numbers are not stable PCI identities.
        for key, value in metrics.gpus[0].items():
            if key.startswith("gpu_"):
                setattr(metrics, key, value)

    # LibreHardwareMonitor's console helper and PowerShell WMI query are kept
    # as compatibility fallbacks only. Re-running either on every refresh can
    # create short-lived conhost windows on Windows even when the parent was
    # launched hidden. AIDA Registry + PDH + NVIDIA cover this installation
    # without spawning those periodic fallback processes.
    if os.name == "nt" and any(
        getattr(metrics, field) is None
        for field in ("cpu_temp_c", "cpu_voltage_v", "cpu_power_w")
    ):
        fallbacks = [
            SensorProvider("windows-helper", "bundled LibreHardwareMonitor helper fallback", _sensor_helper),
            SensorProvider("windows-lhm", "LibreHardwareMonitor WMI fallback", _windows_lhm),
        ]
        metrics.sources.update(apply_providers(metrics, fallbacks))
        if multi_gpu:
            for key, value in metrics.gpus[0].items():
                if key.startswith("gpu_"):
                    setattr(metrics, key, value)
    return metrics


def as_dict(metrics: Metrics) -> dict:
    return json.loads(json.dumps(metrics, default=lambda o: o.__dict__))
