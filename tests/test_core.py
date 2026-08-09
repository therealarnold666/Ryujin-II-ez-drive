from pathlib import Path

from PIL import Image

from ryujin_lcd.config import Settings, bundled_background, normalize_modules
from ryujin_lcd.conflicts import armoury_ryujin_modules_present
from ryujin_lcd.drivers import MockDriver
from ryujin_lcd.render import background_is_animated, frame_bytes, render
from ryujin_lcd.sensors import Metrics, collect, short_cpu_name, short_gpu_name


def test_render_frame_shape(tmp_path: Path):
    bg = tmp_path / "background.png"
    Image.new("RGB", (800, 600), "navy").save(bg)
    image = render(Metrics(cpu_usage=42, gpu_usage=23), str(bg))
    assert image.size == (320, 240)
    assert len(frame_bytes(image, "BGR")) == 320 * 240 * 3


def test_mock_driver_records_frame():
    driver = MockDriver()
    driver.start()
    driver.write(b"abc")
    assert driver.frames == 1
    assert driver.last_frame == b"abc"


def test_animated_gif_changes_frames(tmp_path: Path):
    bg = tmp_path / "animated.gif"
    frames = [Image.new("RGB", (32, 24), "red"), Image.new("RGB", (32, 24), "blue")]
    frames[0].save(bg, save_all=True, append_images=frames[1:], duration=[100, 100], loop=0)
    assert background_is_animated(str(bg))
    first = render(Metrics(), str(bg), animation_time=0.05)
    second = render(Metrics(), str(bg), animation_time=0.15)
    assert first.getpixel((0, 0)) != second.getpixel((0, 0))


def test_settings_defaults():
    settings = Settings()
    assert (settings.width, settings.height) == (320, 240)
    assert settings.pixel_order in {"RGB", "BGR"}
    assert settings.windows_backend == "direct"
    assert 1 <= settings.animation_fps <= 30
    assert settings.cpu_modules == ["temperature", "load", "clock", "voltage", "power"]
    assert settings.gpu_modules == ["temperature", "load", "clock", "voltage", "power"]


def test_stat_module_normalization_preserves_positions():
    assert normalize_modules(["power", "temperature", "power", "unknown"]) == ["power", "temperature"]


def test_bundled_visual_assets():
    background = bundled_background()
    font = Path(__file__).resolve().parents[1] / "ryujin_lcd" / "assets" / "ROGFonts-Regular_0.otf"
    assert background and background.is_file()
    assert font.is_file()


def test_collect_returns_metrics():
    metrics = collect()
    assert isinstance(metrics, Metrics)
    assert 0 <= metrics.cpu_usage <= 100
    assert metrics.memory_total_mb and metrics.memory_total_mb > 0
    assert metrics.memory_used_mb is not None


def test_armoury_module_probe_returns_bool():
    assert isinstance(armoury_ryujin_modules_present(), bool)


def test_short_hardware_names():
    assert short_cpu_name("AMD Ryzen 7 9800X3D") == "9800X3D"
    assert short_gpu_name("NVIDIA GeForce RTX 5090") == "RTX 5090"


def test_linux_protocol_matches_verified_commands():
    from ryujin_lcd.drivers import LinuxExperimentalDriver

    class FakeHid:
        def __init__(self):
            self.writes = []
            self.responses = [
                bytes([0xEC, 0x50, 0, 0, 0, 0]) + bytes(59),
                bytes(65),
                bytes(65),
            ]

        def write(self, command):
            self.writes.append(bytes(command))
            return len(command)

        def read(self, size, timeout_ms):
            return self.responses.pop(0)

    class FakeEndpoint:
        def __init__(self):
            self.frames = []

        def write(self, frame, timeout):
            self.frames.append(bytes(frame))
            return len(frame)

    driver = LinuxExperimentalDriver(Settings())
    driver.hid = FakeHid()
    driver.endpoint = FakeEndpoint()
    driver._ensure_display_mode()
    driver._send_frame(bytes(320 * 240 * 3))
    assert driver.hid.writes[0][:2] == bytes([0xEC, 0xD0])
    assert driver.hid.writes[1][:3] == bytes([0xEC, 0x51, 0x20])
    assert driver.hid.writes[2][:6] == bytes([0xEC, 0x7F, 0x03, 0, 0x84, 0x03])


def test_linux_hid_read_accepts_64_bytes_after_short_report():
    from ryujin_lcd.drivers import LinuxExperimentalDriver

    class FakeHid:
        def __init__(self):
            self.responses = [b"\0", bytes([0xEC, 0x50]) + bytes(62)]

        def read(self, size, timeout_ms):
            return self.responses.pop(0)

    driver = LinuxExperimentalDriver(Settings())
    driver.hid = FakeHid()
    assert driver._hid_read(100) == bytes([0xEC, 0x50]) + bytes(62)
