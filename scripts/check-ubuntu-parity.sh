#!/usr/bin/env bash
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0

check() {
  if "$@" >/dev/null 2>&1; then
    printf 'OK   %s\n' "$*"
  else
    printf 'MISS %s\n' "$*"
    fail=1
  fi
}

printf '== OS ==\n'
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  printf '%s %s; kernel %s\n' "${NAME:-Linux}" "${VERSION_ID:-unknown}" "$(uname -r)"
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 26.04 ]] || fail=1
else
  printf 'MISS /etc/os-release\n'
  fail=1
fi

printf '== Runtime ==\n'
check python3 -c 'import sys; assert sys.version_info >= (3, 11)'
check python3 -c 'import PIL, psutil, fastapi, uvicorn, usb, hid'
check command -v nvidia-smi
check command -v lsusb
check command -v sensors

printf '== Packaged visual assets ==\n'
check test -s "$root/ryujin_lcd/assets/background.png"
check test -s "$root/ryujin_lcd/assets/ROGFonts-Regular_0.otf"

printf '== Ryujin hardware ==\n'
if lsusb -d 0b05:1988 >/dev/null 2>&1; then
  printf 'OK   USB 0b05:1988\n'
else
  printf 'MISS USB 0b05:1988\n'
  fail=1
fi
if grep -q '^asus_rog_ryujin$' /sys/class/hwmon/hwmon*/name 2>/dev/null; then
  printf 'OK   asus_rog_ryujin hwmon\n'
else
  printf 'WARN asus_rog_ryujin hwmon not exposed (LCD can still be tested)\n'
fi

printf '== Result ==\n'
if [[ $fail -eq 0 ]]; then
  printf 'Software prerequisites are ready. Physical LCD parity still requires an on-device frame test.\n'
else
  printf 'One or more parity prerequisites are missing.\n'
fi
exit "$fail"
