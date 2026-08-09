# Ryujin II LCD

面向 ASUS ROG Ryujin II 360（USB `0b05:1988`、320×240、24-bit）的跨平台本地仪表盘。Windows 11 默认绕过有随机黑闪问题的华硕 `ROGAIOSDK.dll`，直接使用系统 HID + WinUSB API；Ubuntu 26.04 提供同一套 UI、监控和背景管理，并附一个需要实机验证的原生 Linux HID + USB bulk LCD 后端。

## 当前支持状态

| 功能 | Windows 11 | Ubuntu 26.04 |
|---|---|---|
| 本地 Web UI / 自定义背景 | 支持 | 支持；同一份默认背景已内置 |
| CPU 占用、频率、内存 | `psutil` | `psutil` |
| NVIDIA 温度、频率、占用、显存 | NVIDIA 驱动的 `nvidia-smi` | NVIDIA 驱动的 `nvidia-smi` |
| CPU 温度、电压 | 内置 LibreHardwareMonitor 0.9.6；本机 9800X3D 使用 AIDA64 只读 Registry 备用源 | `lm-sensors`/hwmon，经 `psutil` |
| Ryujin 水温、泵速、内置风扇 | 暂不从 SDK读取 | Linux 6.9+ `asus_rog_ryujin` hwmon |
| LCD 写入 | Microsoft HID + WinUSB 直连；SDK 仅为可选回退 | 实验性用户态 HID + libusb 后端；命令时序已与 Windows 对齐 |

重要边界：ASUS 没有发布 Linux 版 LCD SDK。Linux 主线 `asus_rog_ryujin` 驱动支持水温、泵和风扇，但明确不实现 LCD。Windows 直连路径已在固件 `AURJ1-S750-0104` 上做过静态 30 分钟及 600 帧动态实机测试；Linux 路径现已实现与 Windows 相同的状态查询、live-mode、逐帧准备、bulk 发送、ACK 同步及模式恢复，但仍缺少同一台设备上的 Ubuntu 实机验证，因此继续默认关闭，必须用 `--experimental-linux-usb` 明确开启。

当前静态背景位于 `ryujin_lcd/assets/background.png`，程序首次运行会把它复制到用户可写配置目录。`ROGFonts-Regular_0.otf` 也随本地交付包保存，两个系统会优先使用同一字体文件。若对外重新分发本项目，请先自行确认该华硕字体的授权范围。

## Windows 11 安装

要求：Python 3.11+、Visual Studio Build Tools（C++ x64 工具）。直连后端只链接 Windows 自带的 `setupapi.lib`、`hid.lib`、`winusb.lib` 和 `ole32.lib`，不需要 `ROGAIOSDK.dll`。

```powershell
cd ryujin-lcd
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-direct-helper.ps1
```

先只读检查冲突：

```powershell
powershell -File .\scripts\check-conflicts.ps1
```

确定要让本程序成为唯一 LCD 控制端后，以管理员权限执行：

```powershell
powershell -File .\scripts\check-conflicts.ps1 -Fix
ryujin-lcd
```

浏览器会打开 `http://127.0.0.1:8765`。上传 PNG/JPEG/WebP/GIF 后点击“启动 LCD”。动态 GIF 会按原始帧时长和循环规则播放，LCD 动画默认 24 FPS；传感器仍按 `refresh_seconds` 独立更新。没有对固件 Flash 执行媒体写入。

登录后静默自动驱动可使用：

```powershell
RyujinLCD.exe --auto-start --no-browser
```

自动启动仍执行冲突检查；如果 Armoury Crate 更新重新安装了 Ryujin 模块，程序会保留 Web 状态页但不会强行抢占 LCD。

安装或移除当前用户登录计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1 -Remove
```

默认配置为：

```json
{
  "windows_backend": "direct",
  "pixel_order": "BGR"
}
```

可在 `%LOCALAPPDATA%\ryujin-lcd\config.json` 修改刷新周期和像素顺序。仅为故障排查需要回退 SDK 时，将 `windows_backend` 改为 `sdk`，再配置 `sdk_path` 并构建 x86 `ryujin_sdk_helper.exe`；SDK 不再是默认路径。

## Ubuntu 26.04 安装

```bash
sudo apt install python3-venv python3-tk usbutils lm-sensors libhidapi-hidraw0 libusb-1.0-0
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[linux]'
bash scripts/check-conflicts.sh
bash scripts/check-ubuntu-parity.sh
```

Ubuntu 26.04 LTS 的 GA 内核为 7.0，已包含 `asus_rog_ryujin` hwmon 支持。首次安装可使用随项目附带的窄范围 udev 规则，避免长期以 root 身份运行：

```bash
sudo install -m 0644 scripts/99-ryujin-lcd.rules /etc/udev/rules.d/99-ryujin-lcd.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

仅运行 UI/预览，不触碰 USB：

```bash
ryujin-lcd --mock
```

实验性写屏：

```bash
sudo bash scripts/check-conflicts.sh --fix
sudo .venv/bin/ryujin-lcd --experimental-linux-usb --no-browser
```

项目中的 udev 规则仅匹配 `0b05:1988`，使用 `0660`、`plugdev` 和 `uaccess`，不会开放所有 USB/HID 设备。

### Ubuntu 1:1 结论

- 可做到 1:1：320×240 布局、颜色、文字位置、ROG 字体、静态/动态背景、24 FPS 合成策略、Web UI，以及主机生成的 BGR 帧字节。
- 协议实现已对齐但尚未实机证明：HID/USB LCD 传输。只有在 Ubuntu 26.04 裸机连接同一台 Ryujin II 后，连续播放并确认无 mode loss/timeout，才能把它从“实验性”改成“已验证”。WSL/虚拟机不能代替这一项，除非完整透传 HID 与 USB bulk 两个复合接口。
- 不能天然 1:1：CPU 电压。Windows 本机来自 AIDA64 Registry 备用源，Ubuntu 的标准 `k10temp` 通常不提供等价 CPU 核心电压；缺少额外 hwmon provider 时会显示 `--`。CPU 温度、占用、频率、GPU 温度/占用/频率、VRAM、RAM 可使用 Linux 原生来源保持同项目含义。

## 冲突排除设计

- 程序用系统级文件锁防止自身多开。
- Windows 检测 AIDA64、Armoury Crate device framework 与相关辅助进程；发现后先弹出进程清单，只有用户确认才停止服务/进程并继续写屏。若 AIDA64 配置中 `HWMonEnableRALCD=0`，它只作为传感器源运行，不视为 LCD 冲突。
- 若 `ROG RYUJIN II` 设备模块和 `ASUS AIOFan HAL` 已卸载，普通 Armoury Crate/`asus_framework` 进程会被允许共存；模块被 Armoury 更新中心重新安装后会自动恢复冲突拦截。
- Linux 检测 `liquidctl`、CoolerControl、OpenRGB，并显示 `fuser` 打开的 HID/USB句柄。
- `check-conflicts` 默认只读；只有显式 `-Fix`/`--fix` 才停止已知用户态控制端。
- Linux 脚本不会卸载/卸载绑定 `asus_rog_ryujin` 内核 hwmon 驱动，避免破坏水温与泵/风扇监控。

## 传感器来源说明

- `psutil`：CPU 占用、RAM 占用及 Linux CPU 频率；Linux 上也从标准 hwmon 暴露 CPU 温度。
- Windows CPU 频率：Windows PDH 的 `Processor Information(_Total)\\% Processor Performance` 乘以 `psutil` 返回的名义频率。这样 9800X3D 的 4.7 GHz 基准频率在 112% boost 时显示约 5.3 GHz，而不是固定显示 4.7 GHz。
- NVIDIA `nvidia-smi`：GPU 名称、核心温度、利用率、图形频率、显存已用/总量；支持 Windows 和 Linux。
- Linux `/sys/class/hwmon/*/name == asus_rog_ryujin`：冷却液温度、泵 RPM、内置风扇 RPM。
- Windows 内置官方 LibreHardwareMonitor 0.9.6（MPL-2.0）helper，读取 CPU/GPU 温度、电压和频率。本机 9800X3D 的 AMD SMU 温度通道返回空值，因此额外使用已安装 AIDA64 的 Registry sensor export 读取 `CPU Package` 与 `CPU Core` 电压；AIDA64 的 `HWMonEnableRALCD` 必须保持为 `0`，因此它不会调用 ROG AIO LCD。CPU 占用/频率仍来自 `psutil`，GPU 占用/显存来自 `nvidia-smi`。
- AMD GPU 可在后续增加 AMD ADLX（Windows）和 ROCm SMI/sysfs（Linux）provider；当前 NVIDIA 路径已经完成。

## 安全与已知限制

- 不刷固件，不写 `User_GIF/JPG/AVI` Flash 槽，只发送实时帧。
- 动态背景通过主机逐帧合成并发送，不写入设备 Flash。可在配置中用 `animation_fps` 调节，程序限制在 1–30 FPS，默认 24 FPS；USB 调度保留最高 4 Hz 余量抵消 Windows 唤醒开销，GIF 时间轴仍按原始帧时长运行。状态 API 会报告目标、传输调度和实际输出 FPS。
- 固件 `AURJ1-S750-0104` 使用 SDK 时曾随机出现显示模式 `0x20 → 0x00 → 0x20` 并伴随黑闪。默认直连 helper 每 5 秒查询一次模式；掉出 `0x20` 时自动恢复并重发最后一帧。实机直连测试未观察到模式掉落、黑屏或查询超时。
- Windows 冲突脚本停止 ArmouryCrateService 后，结束本程序可用管理员 PowerShell 执行 `Start-Service ArmouryCrateService` 恢复。
- Ubuntu 后端的 `EC D0` 状态查询、`EC 51 20` live-mode 设置和 bulk frame write 来自对本机 ASUS SDK 的静态分析，仍需 USBPcap/实机对照验证。

## 开发测试

```bash
pip install -e '.[dev]'
pytest
ryujin-lcd --mock
```

参考资料：[Linux 内核 `asus_rog_ryujin` 文档](https://docs.kernel.org/hwmon/asus_rog_ryujin.html)、[liquidctl Ryujin 指南](https://github.com/liquidctl/liquidctl/blob/main/docs/asus-ryujin-guide.md)、[AIDA64 LCD Guide](https://download.aida64.com/resources/lcd/aida64_lcd_guide.pdf)、[LibreHardwareMonitor 官方项目](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)。
