# Ryujin II LCD 源码交付说明

此文件夹是 2026-08-09 的完整本地交付副本，目标设备为 ASUS ROG Ryujin II 360（USB `0b05:1988`，LCD 320×240）。

## 已包含

- Windows 11 与 Ubuntu 26.04 共用的 Python UI、渲染、传感器和驱动调度源码
- Windows HID + WinUSB 直连助手的 C++ 源码和已编译程序
- Windows LibreHardwareMonitor 传感器助手源码、程序和运行依赖
- Ubuntu 用户态 HID + libusb 后端、冲突检查、udev 规则和环境检查脚本
- 当前使用的静态背景：`ryujin_lcd/assets/background.png`
- 当前使用的 ROG 字体：`ryujin_lcd/assets/ROGFonts-Regular_0.otf`
- Windows 9800X3D 实时 boost 频率：使用原生 PDH `Processor Performance` 计数器，避免固定显示 4.7 GHz 基准频率
- Windows 可执行程序构建配置：`RyujinLCD.spec`
- Windows 登录自动启动任务脚本：`scripts/install-autostart.ps1`
- 测试：`tests/`

完整功能、安装命令、传感器来源和 Ubuntu 限制请先阅读同目录的 `README.md`。文件完整性及 Ubuntu 1:1 边界见 `PACKAGE-AUDIT.md`。

## Windows 快速构建

在本文件夹打开 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e '.[dev]'
pytest
pip install pyinstaller
pyinstaller --noconfirm --clean .\RyujinLCD.spec
```

构建结果位于 `dist\RyujinLCD\`。安装后可创建当前用户登录启动任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1
```

## Ubuntu 26.04 快速检查

```bash
sudo apt install python3-venv usbutils lm-sensors libhidapi-hidraw0 libusb-1.0-0
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[linux,dev]'
bash scripts/check-ubuntu-parity.sh
pytest
```

Linux 渲染、背景、字体和帧格式与 Windows 共用；LCD USB 协议路径仍需在 Ubuntu 26.04 裸机连接同一台 Ryujin II 后完成实机验证。CPU 电压在标准 Linux `k10temp` 下可能没有等价来源。

## 关键资源校验值

- `background.png` SHA-256：`82258A2BE4652CFC5052FA2F8E6A9ACB4A8C716990E7BBD24E8E03B2C3C654D4`
- `ROGFonts-Regular_0.otf` SHA-256：`2C5C3CCCA9A8992C37D89C936E82CB0435F6088F4AAC9EA2C0617CDCA2EE6CC1`

ROG 字体随此本地交付副本保存；如需将项目公开发布，请先确认字体再分发许可。
