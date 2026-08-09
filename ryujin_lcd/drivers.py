from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings

VID = 0x0B05
PID = 0x1988


class DriverError(RuntimeError):
    pass


class LCDDriver(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def write(self, frame: bytes) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class MockDriver(LCDDriver):
    def __init__(self) -> None:
        self.frames = 0
        self.last_frame = b""

    def start(self) -> None:
        pass

    def write(self, frame: bytes) -> None:
        self.frames += 1
        self.last_frame = frame

    def close(self) -> None:
        pass


def _bundled_native(name: str) -> Path:
    roots = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(__file__).resolve().parent.parent,
        Path(sys.executable).resolve().parent / "_internal",
    ]
    return next((root / "native" / name for root in roots if (root / "native" / name).exists()), roots[1] / "native" / name)


class WindowsDirectDriver(LCDDriver):
    """Stable Windows backend using system HID and WinUSB, not ROGAIOSDK.dll."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.last_error = ""

    def _helper(self) -> Path:
        return Path(self.settings.direct_helper_path) if self.settings.direct_helper_path else _bundled_native("ryujin_direct_helper.exe")

    def start(self) -> None:
        if os.name != "nt":
            raise DriverError("Windows direct backend can only run on Windows")
        helper = self._helper()
        if not helper.exists():
            raise DriverError(f"Direct helper not found: {helper}; run scripts/build-windows-direct-helper.ps1")
        frame_size = self.settings.width * self.settings.height * 3
        self.proc = subprocess.Popen(
            [str(helper), str(frame_size)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def drain() -> None:
            assert self.proc and self.proc.stderr
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode(errors="replace").strip()
                self.last_error = line
                if line.startswith(("READY", "ERROR")):
                    self._ready.set()

        self._stderr_thread = threading.Thread(target=drain, name="ryujin-direct-log", daemon=True)
        self._stderr_thread.start()
        if not self._ready.wait(6) or not self.proc or self.proc.poll() is not None or self.last_error.startswith("ERROR"):
            self.close()
            raise DriverError(self.last_error or "Direct HID+WinUSB helper did not become ready")

    def write(self, frame: bytes) -> None:
        expected = self.settings.width * self.settings.height * 3
        if len(frame) != expected:
            raise DriverError(f"Invalid direct frame size: {len(frame)}/{expected}")
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            raise DriverError(self.last_error or "Direct HID+WinUSB helper is not running")
        try:
            self.proc.stdin.write(frame)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise DriverError(self.last_error or str(exc)) from exc

    def close(self) -> None:
        if not self.proc:
            return
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
        self.proc = None


class WindowsSDKDriver(LCDDriver):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self.last_error = ""

    def _helper(self) -> Path:
        if self.settings.helper_path:
            return Path(self.settings.helper_path)
        return _bundled_native("ryujin_sdk_helper.exe")

    def start(self) -> None:
        if os.name != "nt":
            raise DriverError("Windows SDK backend can only run on Windows")
        helper, sdk = self._helper(), Path(self.settings.sdk_path)
        if not helper.exists():
            raise DriverError(f"SDK helper not found: {helper}; run scripts/build-windows-helper.ps1")
        if not sdk.exists():
            raise DriverError(f"ASUS ROGAIOSDK.dll not found: {sdk}")
        frame_size = self.settings.width * self.settings.height * 3
        self.proc = subprocess.Popen(
            [str(helper), "stream", str(sdk), str(self.settings.device_index), str(frame_size)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        def drain() -> None:
            assert self.proc and self.proc.stderr
            for line in iter(self.proc.stderr.readline, b""):
                self.last_error = line.decode(errors="replace").strip()

        self._stderr_thread = threading.Thread(target=drain, daemon=True)
        self._stderr_thread.start()

    def write(self, frame: bytes) -> None:
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            raise DriverError(self.last_error or "ASUS SDK helper is not running")
        try:
            self.proc.stdin.write(frame)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise DriverError(self.last_error or str(exc)) from exc

    def close(self) -> None:
        if not self.proc:
            return
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
        self.proc = None


class LinuxExperimentalDriver(LCDDriver):
    """User-space port of the ROGAIOSDK data path.

    The SDK uses a 65-byte HID control channel and a vendor WinUSB bulk OUT
    interface. This backend mirrors that shape, but is intentionally opt-in
    until it has been verified on the user's exact firmware/interface layout.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dev = None
        self.endpoint = None
        self.hid = None
        self.interface_number: int | None = None
        self.hid_interface_name: str | None = None
        self.detached_kernel_driver = False
        self.frame_ack_pending = False
        self.last_frame = b""
        self.next_status = 0.0
        self.mode_losses = 0

    def start(self) -> None:
        if sys.platform != "linux":
            raise DriverError("Experimental USB backend is Linux-only")
        if not self.settings.linux_experimental_usb:
            raise DriverError("Linux LCD writes are disabled; pass --experimental-linux-usb after reading README")
        try:
            import hid
            import usb.core
            import usb.util
        except ImportError as exc:
            raise DriverError("Install Linux dependencies: pip install -e '.[linux]'") from exc

        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise DriverError("Ryujin II 0b05:1988 was not found")
        try:
            cfg = self.dev.get_active_configuration()
        except usb.core.USBError:
            self.dev.set_configuration()
            cfg = self.dev.get_active_configuration()
        selected = None
        for intf in cfg:
            for ep in intf:
                if (usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT
                        and usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK):
                    selected = (intf, ep)
                    break
            if selected:
                break
        if not selected:
            raise DriverError("No vendor bulk OUT endpoint was found")
        intf, self.endpoint = selected
        self.interface_number = int(intf.bInterfaceNumber)
        if self.dev.is_kernel_driver_active(self.interface_number):
            self.dev.detach_kernel_driver(self.interface_number)
            self.detached_kernel_driver = True
        usb.util.claim_interface(self.dev, self.interface_number)

        paths = hid.enumerate(VID, PID)
        if not paths:
            raise DriverError("No Ryujin HID control interface was found")
        # The verified Windows path uses the composite device's HID MI_01.
        hid_path = next((item for item in paths if item.get("interface_number") == 1), paths[0])
        raw_path = hid_path["path"]
        self.hid_interface_name = raw_path.decode() if isinstance(raw_path, bytes) else str(raw_path)
        self.hid = hid.device()
        self.hid.open_path(raw_path)
        self.hid.set_nonblocking(False)
        # On Linux the device may not surface a status reply through hidapi
        # even though it accepts output reports.  Live mode is idempotent, so
        # set it directly instead of making display startup depend on a query.
        self._set_live_mode()
        self.next_status = float("inf")

    def _hid_write(self, command: bytes) -> None:
        if not self.hid or self.hid.write(command) != len(command):
            raise DriverError("Ryujin HID command write failed or was short")

    def _hid_read(self, timeout_ms: int) -> bytes:
        if not self.hid:
            raise DriverError("Ryujin HID control interface is not open")
        # This device can emit a one-byte 00 report immediately before the
        # response.  Windows' raw handle hides that detail; hidapi exposes it.
        # Ignore short reports while there is still time for the requested
        # 64/65-byte response to arrive.
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            response = bytes(self.hid.read(65, timeout_ms=remaining_ms))
            # Windows returns the report ID plus 64 bytes. hidapi on Linux
            # strips the report ID for this device and returns 64 bytes.
            if len(response) in (64, 65):
                return response
            if time.monotonic() >= deadline:
                raise DriverError("Ryujin HID response timed out")

    def _exchange(self, command: bytes, timeout_ms: int = 1000) -> bytes:
        self._hid_write(command)
        return self._hid_read(timeout_ms)

    def _query_status(self) -> bytes:
        self._hid_write(bytes([0xEC, 0xD0]) + bytes(63))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            response = self._hid_read(max(1, int((deadline - time.monotonic()) * 1000)))
            if response[:2] == bytes([0xEC, 0x50]):
                return response
        raise DriverError("Unexpected Ryujin HID status response")

    def _set_live_mode(self) -> None:
        self._hid_write(bytes([0xEC, 0x51, 0x20]) + bytes(62))

    def _prepare_frame(self) -> None:
        command = bytearray(65)
        command[0:3] = bytes([0xEC, 0x7F, 0x03])
        command[4:6] = bytes([0x84, 0x03])
        # The prepare reply is optional on this firmware. Waiting for it after
        # every frame can consume a delayed ACK instead and stalls the next
        # transfer, while the bulk endpoint has already accepted the frame.
        self._hid_write(bytes(command))

    def _drain_frame_ack(self) -> None:
        if self.frame_ack_pending:
            # Firmware normally emits EE 20 after a bulk frame, but some
            # Linux HID stacks do not surface that optional acknowledgement.
            # It is only synchronization, not evidence that the frame failed
            # (the bulk transfer itself already completed successfully).
            try:
                self._hid_read(250)
            except DriverError:
                pass
            self.frame_ack_pending = False

    def _ensure_display_mode(self) -> None:
        # Match the verified Windows direct protocol byte-for-byte.
        response = self._query_status()
        if response[5] != 0x20:
            self._set_live_mode()

    def _send_frame(self, frame: bytes) -> None:
        if self.endpoint is None:
            raise DriverError("Linux USB bulk endpoint is not open")
        self._prepare_frame()
        written = self.endpoint.write(frame, timeout=3000)
        if written != len(frame):
            raise DriverError(f"Short bulk write: {written}/{len(frame)}")
        self.frame_ack_pending = True

    def write(self, frame: bytes) -> None:
        if self.endpoint is None:
            raise DriverError("Linux USB backend is not started")
        expected = self.settings.width * self.settings.height * 3
        if len(frame) != expected:
            raise DriverError(f"Invalid Linux frame size: {len(frame)}/{expected}")
        now = time.monotonic()
        # The initial status query establishes live mode.  On Linux this
        # device does not reliably distinguish delayed frame ACKs from later
        # status replies, so continuous bulk streaming is safer than periodic
        # mode probing (the Windows raw-HID path can retain that extra check).
        self._drain_frame_ack()
        self._send_frame(frame)
        self.last_frame = frame

    def close(self) -> None:
        try:
            self._drain_frame_ack()
        except DriverError:
            pass
        if self.hid:
            self.hid.close()
            self.hid = None
        if self.dev:
            try:
                import usb.util

                if self.interface_number is not None:
                    usb.util.release_interface(self.dev, self.interface_number)
                    if self.detached_kernel_driver:
                        try:
                            self.dev.attach_kernel_driver(self.interface_number)
                        except Exception:
                            pass
                usb.util.dispose_resources(self.dev)
            finally:
                self.dev = None
                self.endpoint = None
                self.interface_number = None
        # The hidapi libusb backend temporarily detaches the kernel HID
        # driver. Rebind it after all userspace handles are closed so the
        # in-kernel rog_ryujin hwmon driver is available again on exit.
        if sys.platform == "linux" and self.hid_interface_name:
            interface = Path("/sys/bus/usb/devices") / self.hid_interface_name
            try:
                if interface.is_dir() and not (interface / "driver").exists():
                    Path("/sys/bus/usb/drivers/usbhid/bind").write_text(self.hid_interface_name + "\n")
            except OSError:
                # Non-root operation remains valid when udev grants only the
                # device handles, not sysfs driver administration.
                pass
        self.hid_interface_name = None


def create_driver(settings: Settings, mock: bool = False) -> LCDDriver:
    if mock:
        return MockDriver()
    if os.name == "nt":
        if settings.windows_backend.lower() == "sdk":
            return WindowsSDKDriver(settings)
        if settings.windows_backend.lower() != "direct":
            raise DriverError(f"Unknown Windows backend: {settings.windows_backend}")
        return WindowsDirectDriver(settings)
    return LinuxExperimentalDriver(settings)
