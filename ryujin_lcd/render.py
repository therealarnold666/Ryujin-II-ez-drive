from __future__ import annotations

import bisect
import io
import threading
import time
from collections import OrderedDict
from datetime import datetime
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .sensors import Metrics, short_cpu_name, short_gpu_name


STAT_MODULES = {
    "temperature": ("TEMP", "temp_c", " °C", 0, "white"),
    "load": ("LOAD", "usage", " %", 0, "#7ce8ff"),
    "clock": ("CLOCK", "freq_mhz", " GHz", 1, "#ffc56e"),
    "voltage": ("VOLT", "voltage_v", " V", 2, "#c7a7ff"),
    "power": ("POWER", "power_w", " W", 0, "#8ff0a4"),
}


class _AnimatedBackground:
    def __init__(self, path: Path) -> None:
        # BytesIO prevents an open GIF from locking the upload destination on Windows.
        self._source = Image.open(io.BytesIO(path.read_bytes()))
        self.frames = int(getattr(self._source, "n_frames", 1))
        self.loop = int(self._source.info.get("loop", 0) or 0)
        self.durations: list[float] = []
        self._frames: OrderedDict[tuple[int, int, int], Image.Image] = OrderedDict()
        self._cache_bytes = 0
        self._cache_limit = 48 * 1024 * 1024
        cumulative = 0.0
        self.ends: list[float] = []
        for index in range(self.frames):
            self._source.seek(index)
            duration = max(0.02, float(self._source.info.get("duration", 100)) / 1000.0)
            self.durations.append(duration)
            cumulative += duration
            self.ends.append(cumulative)
            # Ryujin II is fixed at 320x240. Decoding sequentially here avoids
            # expensive random GIF seeks in the real-time playback loop.
            preview = ImageOps.fit(
                self._source.convert("RGB"), (320, 240), method=Image.Resampling.BILINEAR
            )
            size = 320 * 240 * 3
            if self._cache_bytes + size <= self._cache_limit:
                self._frames[(index, 320, 240)] = preview
                self._cache_bytes += size
        self.total = cumulative
        self.started = time.monotonic()
        self._source.seek(0)

    @property
    def animated(self) -> bool:
        return self.frames > 1 and self.total > 0

    def frame(self, width: int, height: int, animation_time: float | None) -> Image.Image:
        elapsed = time.monotonic() - self.started if animation_time is None else max(0.0, animation_time)
        index = 0
        if self.animated:
            if self.loop > 0 and elapsed >= self.total * (self.loop + 1):
                index = self.frames - 1
            else:
                index = bisect.bisect_right(self.ends, elapsed % self.total)
                index = min(index, self.frames - 1)
        key = (index, width, height)
        cached = self._frames.get(key)
        if cached is not None:
            self._frames.move_to_end(key)
            return cached
        self._source.seek(index)
        method = Image.Resampling.BILINEAR if self.animated else Image.Resampling.LANCZOS
        image = ImageOps.fit(self._source.convert("RGB"), (width, height), method=method)
        size = width * height * 3
        while self._frames and self._cache_bytes + size > self._cache_limit:
            _, old = self._frames.popitem(last=False)
            self._cache_bytes -= old.width * old.height * 3
        self._frames[key] = image
        self._cache_bytes += size
        return image


_background_lock = threading.RLock()
_background_key: tuple[str, int, int] | None = None
_background_source: _AnimatedBackground | None = None


def _background(path_text: str) -> _AnimatedBackground | None:
    global _background_key, _background_source
    if not path_text:
        return None
    path = Path(path_text)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _background_lock:
        if key != _background_key:
            try:
                _background_source = _AnimatedBackground(path)
                _background_key = key
            except (OSError, ValueError):
                _background_source = None
                _background_key = key
        return _background_source


def background_is_animated(path: str) -> bool:
    source = _background(path)
    return bool(source and source.animated)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    package_font = Path(__file__).resolve().parent / "assets" / "ROGFonts-Regular_0.otf"
    candidates = [
        package_font,
        Path("C:/Windows/Fonts/ROGFonts-Regular_0.otf"),
        Path.home() / ".local/share/fonts/ROGFonts-Regular_0.otf",
        Path("/usr/local/share/fonts/ROGFonts-Regular_0.otf"),
        Path("/usr/share/fonts/truetype/rog/ROGFonts-Regular_0.otf"),
        Path("C:/Windows/Fonts") / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _metric(value: float | int | None, unit: str, digits: int = 0) -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}{unit}" if isinstance(value, float) else f"{value}{unit}"


def render(metrics: Metrics, background: str, width: int = 320, height: int = 240,
           animation_time: float | None = None, cpu_modules: list[str] | None = None,
           gpu_modules: list[str] | None = None, clock_text: str | None = None) -> Image.Image:
    dual_gpu = len(metrics.gpus) > 1
    source = _background(background)
    if source:
        with _background_lock:
            canvas = source.frame(width, height, animation_time)
    else:
        canvas = Image.new("RGB", (width, height), "#090d18")
        base = ImageDraw.Draw(canvas)
        for y in range(height):
            base.line((0, y, width, y), fill=(8 + y // 18, 12 + y // 13, 28 + y // 7))

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    title = _font(15, True)
    number = _font(15, True)
    compact_number = _font(14, True)
    small = _font(9)
    tiny = _font(9, True)

    def text(position: tuple[int, int], value: str, font: ImageFont.ImageFont, fill: str) -> None:
        draw.text(position, value, font=font, fill=fill, stroke_width=1, stroke_fill="#02050a")

    def centered(center_x: int, y: int, value: str, font: ImageFont.ImageFont, fill: str) -> None:
        x = int(center_x - draw.textlength(value, font=font) / 2)
        text((x, y), value, font, fill)

    def right_aligned(right: int, y: int, value: str, font: ImageFont.ImageFont, fill: str) -> None:
        x = int(right - draw.textlength(value, font=font))
        text((x, y), value, font, fill)

    def sensor_row(top: int, prefix: str, modules: list[str], sample: Metrics | None = None) -> None:
        sample = sample if sample is not None else metrics
        active = [module for module in modules if module in STAT_MODULES]
        if not active:
            return
        columns = [int((index + 0.5) * width / len(active)) for index in range(len(active))]
        for x, module in zip(columns, active):
            label, field, unit, digits, color = STAT_MODULES[module]
            value = getattr(sample, f"{prefix}_{field}")
            if module == "clock" and value is not None:
                value = value / 1000
            centered(x, top, label, small, "#9aa8bd")
            value_font = compact_number if module in {"clock", "voltage"} else number
            centered(x, top + 13, _metric(value, unit, digits), value_font, color)

    if dual_gpu:
        # Three equal sensor bands leave 70 px for independent capacity bars.
        title = _font(13, True)
        number = _font(13, True)
        compact_number = _font(12, True)
        small = _font(8)
        text((16, 4), short_cpu_name(metrics.cpu_name), title, "#7ce8ff")
        right_aligned(304, 4, clock_text or datetime.now().strftime("%H:%M"), title, "#eaf3ff")
        sensor_row(23, "cpu", cpu_modules or list(STAT_MODULES))
        colors = ("#8ff0a4", "#8ff0a4")
        for position, gpu in enumerate(metrics.gpus[:2]):
            top = 57 + position * 53
            sample = replace(metrics, **{k: v for k, v in gpu.items() if k.startswith("gpu_")})
            text((16, top), f"GPU {gpu['index'] + 1}  {short_gpu_name(sample.gpu_name)}", title, colors[position])
            sensor_row(top + 19, "gpu", gpu_modules or list(STAT_MODULES), sample)

        def compact_bar(y, label, used_mb, total_mb, color):
            font = _font(8, True)
            text((16, y), label, font, "#c6d0df")
            value = "-- / --" if not total_mb else f"{(used_mb or 0) / 1024:.1f}/{total_mb / 1024:.1f} GB"
            right_aligned(304, y, value, font, "#eaf3ff")
            left, right = 74, 194
            draw.rounded_rectangle((left, y + 2, right, y + 9), 3, fill=(5, 10, 18, 205), outline="#617087")
            ratio = min(1.0, max(0.0, (used_mb or 0) / total_mb)) if total_mb else 0
            if ratio:
                draw.rounded_rectangle((left, y + 2, left + max(1, int((right-left)*ratio)), y + 9), 3, fill=color)

        for position, gpu in enumerate(metrics.gpus[:2]):
            compact_bar(173 + position * 22, f"VRAM {gpu['index'] + 1}", gpu.get("gpu_memory_used_mb"), gpu.get("gpu_memory_total_mb"), colors[position])
        compact_bar(217, "RAM", metrics.memory_used_mb, metrics.memory_total_mb, "#c7a7ff")
        return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    # Divide the 240 px display into three balanced 80 px bands:
    # CPU, GPU, then the two compact capacity bars.
    text((16, 8), short_cpu_name(metrics.cpu_name), title, "#7ce8ff")
    right_aligned(304, 8, clock_text or datetime.now().strftime("%H:%M"), title, "#eaf3ff")
    sensor_row(37, "cpu", cpu_modules or list(STAT_MODULES))

    text((16, 88), short_gpu_name(metrics.gpu_name), title, "#7ce8ff")
    sensor_row(117, "gpu", gpu_modules or list(STAT_MODULES))

    def capacity_bar(y: int, label: str, used_mb: float | None, total_mb: float | None, color: str) -> None:
        used = used_mb or 0.0
        total = total_mb or 0.0
        ratio = max(0.0, min(1.0, used / total)) if total else 0.0
        text((16, y), label, tiny, "#c6d0df")
        value = "-- / --" if not total else f"{used / 1024:.1f} / {total / 1024:.1f} GB"
        right_aligned(304, y, value, tiny, "#eaf3ff")
        left, top, right, bottom = 16, y + 17, 304, y + 25
        draw.rounded_rectangle((left, top, right, bottom), 4, fill=(5, 10, 18, 205), outline=(150, 170, 195, 150), width=1)
        fill_right = left + int((right - left) * ratio)
        if fill_right > left:
            draw.rounded_rectangle((left, top, fill_right, bottom), 4, fill=color)

    capacity_bar(163, "VRAM", metrics.gpu_memory_used_mb, metrics.gpu_memory_total_mb, "#7ce8ff")
    capacity_bar(201, "RAM", metrics.memory_used_mb, metrics.memory_total_mb, "#c7a7ff")
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def frame_bytes(image: Image.Image, order: str = "BGR") -> bytes:
    image = image.convert("RGB")
    if order.upper() == "RGB":
        return image.tobytes("raw", "RGB")
    if order.upper() == "BGR":
        return image.tobytes("raw", "BGR")
    raise ValueError("pixel_order must be RGB or BGR")
