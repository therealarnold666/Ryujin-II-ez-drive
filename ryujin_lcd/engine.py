from __future__ import annotations

import io
import threading
import time
from dataclasses import asdict

from .config import Settings
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
        self.reconnects = 0
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
        # Release a partial Linux USB claim if the companion HID open fails.
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
                fps_started = time.monotonic()
                fps_frames = 0
                while not self._stop.is_set():
                    started = time.monotonic()
                    try:
                        self._tick(send=True, update_metrics=False)
                    except Exception as exc:
                        # A USB reset, sleep/resume or a short Armoury probe
                        # must not permanently stop the dashboard. Release the
                        # failed helper and retry the real device open until it
                        # returns or the user explicitly stops the engine.
                        self.error = f"LCD 暂时断开，正在自动重连：{exc}"
                        if self.driver:
                            self.driver.close()
                        self.driver = None
                        while not self._stop.wait(3.0):
                            try:
                                replacement = create_driver(self.settings, self.mock)
                                replacement.start()
                                self.driver = replacement
                                self.reconnects += 1
                                self.error = ""
                                break
                            except Exception as reconnect_exc:
                                self.error = f"LCD 自动重连中：{reconnect_exc}"
                        if self._stop.is_set():
                            break
                        continue
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
            "reconnects": self.reconnects,
            "metrics": asdict(self.metrics),
        }
