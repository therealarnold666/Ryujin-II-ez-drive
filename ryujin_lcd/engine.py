from __future__ import annotations

import io
import threading
import time
from dataclasses import asdict

from .config import Settings
from .conflicts import process_conflicts
from .drivers import LCDDriver, create_driver
from .render import background_is_animated, frame_bytes, render
from .sensors import Metrics, collect


class Engine:
    def __init__(self, settings: Settings, mock: bool = False) -> None:
        self.settings = settings
        self.mock = mock
        self.driver: LCDDriver | None = None
        self.metrics = Metrics()
        self.preview = b""
        self.error = ""
        self.running = False
        self.frames_sent = 0
        self.actual_fps = 0.0
        self.animation_active = False
        self._thread: threading.Thread | None = None
        self._sensor_thread: threading.Thread | None = None
        self._stop = threading.Event()
        psutil_warmup = collect()
        self.metrics = psutil_warmup

    def _tick(self, send: bool = True, update_metrics: bool = True) -> None:
        if update_metrics:
            self.metrics = collect()
        image = render(
            self.metrics, self.settings.background, self.settings.width, self.settings.height,
            cpu_modules=self.settings.cpu_modules, gpu_modules=self.settings.gpu_modules,
        )
        output = io.BytesIO()
        image.save(output, "PNG")
        self.preview = output.getvalue()
        if send and self.driver:
            self.driver.write(frame_bytes(image, self.settings.pixel_order))

    def refresh_preview(self) -> None:
        self._tick(send=False)

    def start(self) -> None:
        if self.running:
            return
        self.error = ""
        driver = create_driver(self.settings, self.mock)
        # A Linux USB claim can succeed before opening the companion HID
        # interface fails.  Always release that partial claim so the user can
        # correct the condition and press “start” again without reconnecting
        # the cooler or restarting the web service.
        try:
            driver.start()
        except Exception:
            driver.close()
            raise
        self.driver = driver
        self._stop.clear()
        self.running = True
        self.frames_sent = 0
        self.actual_fps = 0.0

        def sensor_loop() -> None:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    self.metrics = collect()
                except Exception as exc:
                    # Keep the animation alive when an optional sensor source fails.
                    self.error = f"传感器读取失败：{exc}"
                delay = max(0.1, self.settings.refresh_seconds - (time.monotonic() - started))
                self._stop.wait(delay)

        self._sensor_thread = threading.Thread(target=sensor_loop, name="ryujin-sensors", daemon=True)
        self._sensor_thread.start()

        def loop() -> None:
            try:
                next_conflict_check = 0.0
                fps_started = time.monotonic()
                fps_frames = 0
                while not self._stop.is_set():
                    started = time.monotonic()
                    if not self.mock and started >= next_conflict_check:
                        conflicts = process_conflicts()
                        if conflicts:
                            names = ", ".join(f"{x.process} (PID {x.pid})" for x in conflicts)
                            raise RuntimeError(f"运行中检测到新的 LCD 控制冲突，已停止写屏：{names}")
                        next_conflict_check = started + 2.0
                    self._tick(send=True, update_metrics=False)
                    animated = background_is_animated(self.settings.background)
                    self.animation_active = animated
                    self.frames_sent += 1
                    fps_frames += 1
                    fps_now = time.monotonic()
                    if fps_now - fps_started >= 2.0:
                        self.actual_fps = fps_frames / (fps_now - fps_started)
                        fps_started = fps_now
                        fps_frames = 0
                    fps = max(1.0, min(30.0, self.settings.animation_fps))
                    # A small transport headroom offsets Windows wake-up and
                    # thread scheduling overhead. GIF frame selection remains
                    # time-based, so a native 24 FPS animation keeps its speed.
                    transport_fps = min(30.0, fps + 4.0) if animated else fps
                    interval = 1.0 / transport_fps if animated else max(0.1, self.settings.refresh_seconds)
                    delay = max(0.01, interval - (time.monotonic() - started))
                    if animated:
                        time.sleep(delay)
                    else:
                        self._stop.wait(delay)
            except Exception as exc:  # propagated through status for UI operation
                self.error = str(exc)
            finally:
                self._stop.set()
                if self.driver:
                    self.driver.close()
                self.running = False

        self._thread = threading.Thread(target=loop, name="ryujin-lcd", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._sensor_thread:
            self._sensor_thread.join(timeout=5)
        self.running = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "error": self.error,
            "backend": type(self.driver).__name__ if self.driver else None,
            "driver_status": getattr(self.driver, "last_error", "") if self.driver else "",
            "animation_active": self.animation_active,
            "target_fps": max(1.0, min(30.0, self.settings.animation_fps)) if self.animation_active else None,
            "transport_fps": min(30.0, max(1.0, self.settings.animation_fps) + 4.0) if self.animation_active else None,
            "actual_fps": round(self.actual_fps, 1) if self.animation_active else None,
            "frames_sent": self.frames_sent,
            "metrics": asdict(self.metrics),
        }
