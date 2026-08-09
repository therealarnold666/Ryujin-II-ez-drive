#!/usr/bin/env bash
set -u
fix=0
[[ "${1:-}" == "--fix" ]] && fix=1

echo '== Ryujin II USB =='
if command -v lsusb >/dev/null; then
  lsusb -d 0b05:1988 || echo 'Ryujin II was not found.'
else
  echo 'Install usbutils for lsusb.'
fi

echo '== Kernel hwmon =='
for node in /sys/class/hwmon/hwmon*; do
  [[ -r "$node/name" ]] || continue
  [[ "$(<"$node/name")" == asus_rog_ryujin ]] || continue
  echo "$node: asus_rog_ryujin"
done

echo '== Possible user-space controllers =='
found=0
for name in liquidctl coolercontrold coolercontrol openrgb; do
  if pgrep -a -x "$name" 2>/dev/null; then found=1; fi
done
[[ $found -eq 0 ]] && echo 'No known competing user-space controller is running.'

echo '== Open HID/USB handles (informational) =='
if command -v fuser >/dev/null; then
  fuser /dev/hidraw* /dev/bus/usb/*/* 2>/dev/null || true
else
  echo 'Install psmisc for fuser.'
fi

if [[ $fix -eq 0 ]]; then
  echo 'Read-only check complete. Re-run with --fix to stop known user services.'
  exit 0
fi

systemctl --user stop coolercontrol.service 2>/dev/null || true
sudo systemctl stop coolercontrold.service 2>/dev/null || true
pkill -x liquidctl 2>/dev/null || true
pkill -x openrgb 2>/dev/null || true
echo 'Known user-space controllers stopped. The asus_rog_ryujin kernel hwmon driver remains loaded.'

