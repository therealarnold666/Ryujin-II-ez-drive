#include <windows.h>
#include <setupapi.h>
#include <hidsdi.h>
#include <winusb.h>
#include <usb.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#pragma comment(lib, "setupapi.lib")
#pragma comment(lib, "hid.lib")
#pragma comment(lib, "winusb.lib")
#pragma comment(lib, "ole32.lib")

namespace {

constexpr USHORT kVendorId = 0x0B05;
constexpr USHORT kProductId = 0x1988;
constexpr DWORD kHidTimeoutMs = 1000;
constexpr DWORD kBulkTimeoutMs = 3000;

struct DeviceInfoSet {
    HDEVINFO value = INVALID_HANDLE_VALUE;
    ~DeviceInfoSet() { if (value != INVALID_HANDLE_VALUE) SetupDiDestroyDeviceInfoList(value); }
};

struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    Handle() = default;
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value(other.value) { other.value = INVALID_HANDLE_VALUE; }
    ~Handle() { if (value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};

struct WinUsbHandle {
    WINUSB_INTERFACE_HANDLE value = nullptr;
    ~WinUsbHandle() { if (value) WinUsb_Free(value); }
};

std::runtime_error win_error(const char* operation) {
    return std::runtime_error(std::string(operation) + " failed (Win32 " + std::to_string(GetLastError()) + ")");
}

std::vector<std::wstring> interface_paths(const GUID& guid) {
    DeviceInfoSet set;
    set.value = SetupDiGetClassDevsW(&guid, nullptr, nullptr, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (set.value == INVALID_HANDLE_VALUE) throw win_error("SetupDiGetClassDevsW");
    std::vector<std::wstring> paths;
    for (DWORD index = 0;; ++index) {
        SP_DEVICE_INTERFACE_DATA iface{};
        iface.cbSize = sizeof(iface);
        if (!SetupDiEnumDeviceInterfaces(set.value, nullptr, &guid, index, &iface)) {
            if (GetLastError() == ERROR_NO_MORE_ITEMS) break;
            throw win_error("SetupDiEnumDeviceInterfaces");
        }
        DWORD needed = 0;
        SetupDiGetDeviceInterfaceDetailW(set.value, &iface, nullptr, 0, &needed, nullptr);
        if (GetLastError() != ERROR_INSUFFICIENT_BUFFER) throw win_error("SetupDiGetDeviceInterfaceDetailW(size)");
        std::vector<unsigned char> storage(needed);
        auto* detail = reinterpret_cast<SP_DEVICE_INTERFACE_DETAIL_DATA_W*>(storage.data());
        detail->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W);
        if (!SetupDiGetDeviceInterfaceDetailW(set.value, &iface, detail, needed, nullptr, nullptr)) {
            throw win_error("SetupDiGetDeviceInterfaceDetailW");
        }
        paths.emplace_back(detail->DevicePath);
    }
    return paths;
}

Handle open_hid() {
    GUID guid{};
    HidD_GetHidGuid(&guid);
    for (const auto& path : interface_paths(guid)) {
        Handle handle;
        handle.value = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                                   FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
                                   FILE_FLAG_OVERLAPPED, nullptr);
        if (handle.value == INVALID_HANDLE_VALUE) continue;
        HIDD_ATTRIBUTES attributes{};
        attributes.Size = sizeof(attributes);
        if (HidD_GetAttributes(handle.value, &attributes) &&
            attributes.VendorID == kVendorId && attributes.ProductID == kProductId) return handle;
    }
    throw std::runtime_error("Ryujin HID MI_01 was not found or is busy");
}

Handle open_winusb(const GUID& guid) {
    for (const auto& path : interface_paths(guid)) {
        std::wstring lower = path;
        std::transform(lower.begin(), lower.end(), lower.begin(), towlower);
        if (lower.find(L"vid_0b05&pid_1988") == std::wstring::npos) continue;
        Handle handle;
        handle.value = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                                   FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
                                   FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, nullptr);
        if (handle.value != INVALID_HANDLE_VALUE) return handle;
    }
    throw std::runtime_error("Ryujin WinUSB MI_00 was not found or is busy");
}

bool overlapped_file_io(HANDLE handle, bool write, unsigned char* data, DWORD bytes, DWORD timeout_ms) {
    OVERLAPPED ov{};
    ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!ov.hEvent) throw win_error("CreateEventW(HID)");
    DWORD transferred = 0;
    BOOL ok = write ? WriteFile(handle, data, bytes, &transferred, &ov)
                    : ReadFile(handle, data, bytes, &transferred, &ov);
    if (!ok && GetLastError() != ERROR_IO_PENDING) { CloseHandle(ov.hEvent); return false; }
    if (WaitForSingleObject(ov.hEvent, timeout_ms) != WAIT_OBJECT_0) {
        CancelIoEx(handle, &ov);
        CloseHandle(ov.hEvent);
        return false;
    }
    ok = GetOverlappedResult(handle, &ov, &transferred, FALSE);
    CloseHandle(ov.hEvent);
    return ok && transferred == bytes;
}

bool hid_exchange(HANDLE hid, std::vector<unsigned char> command,
                  std::vector<unsigned char>* response, DWORD timeout_ms = kHidTimeoutMs) {
    if (!overlapped_file_io(hid, true, command.data(), static_cast<DWORD>(command.size()), timeout_ms)) return false;
    if (!response) return true;
    response->assign(65, 0);
    return overlapped_file_io(hid, false, response->data(), 65, timeout_ms);
}

std::vector<unsigned char> query_status(HANDLE hid) {
    std::vector<unsigned char> command(65, 0), response;
    command[0] = 0xEC;
    command[1] = 0xD0;
    if (!hid_exchange(hid, command, &response)) throw std::runtime_error("HID status query timed out");
    if (response.size() < 6 || response[0] != 0xEC || response[1] != 0x50) {
        throw std::runtime_error("Unexpected HID status response");
    }
    return response;
}

void set_live_mode(HANDLE hid) {
    std::vector<unsigned char> command(65, 0), response;
    command[0] = 0xEC;
    command[1] = 0x51;
    command[2] = 0x20;
    if (!hid_exchange(hid, command, &response)) throw std::runtime_error("Setting live mode 0x20 timed out");
}

void prepare_frame(HANDLE hid) {
    std::vector<unsigned char> command(65, 0), response;
    command[0] = 0xEC;
    command[1] = 0x7F;
    command[2] = 0x03;
    command[4] = 0x84;
    command[5] = 0x03;
    if (!hid_exchange(hid, command, &response)) throw std::runtime_error("Preparing frame transfer timed out");
}

bool write_bulk(WINUSB_INTERFACE_HANDLE usb, UCHAR pipe, unsigned char* data, ULONG bytes, ULONG* written) {
    OVERLAPPED ov{};
    ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!ov.hEvent) throw win_error("CreateEventW(WinUSB)");
    *written = 0;
    BOOL ok = WinUsb_WritePipe(usb, pipe, data, bytes, written, &ov);
    if (!ok && GetLastError() != ERROR_IO_PENDING) { CloseHandle(ov.hEvent); return false; }
    if (WaitForSingleObject(ov.hEvent, kBulkTimeoutMs) != WAIT_OBJECT_0) {
        WinUsb_AbortPipe(usb, pipe);
        CloseHandle(ov.hEvent);
        return false;
    }
    ok = WinUsb_GetOverlappedResult(usb, &ov, written, FALSE);
    CloseHandle(ov.hEvent);
    return ok;
}

void drain_ack(HANDLE hid) {
    std::vector<unsigned char> ack(65, 0);
    overlapped_file_io(hid, false, ack.data(), 65, 250);
}

void send_frame(HANDLE hid, WINUSB_INTERFACE_HANDLE usb, UCHAR pipe, std::vector<unsigned char>& frame) {
    prepare_frame(hid);
    ULONG written = 0;
    if (!write_bulk(usb, pipe, frame.data(), static_cast<ULONG>(frame.size()), &written) || written != frame.size()) {
        throw std::runtime_error("WinUSB bulk frame write failed or was short");
    }
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    std::cerr << std::unitbuf;
    const size_t frame_size = argc > 1 ? static_cast<size_t>(_wtoi(argv[1])) : 320u * 240u * 3u;
    if (frame_size != 320u * 240u * 3u) {
        std::cerr << "ERROR unsupported frame size: " << frame_size << "\n";
        return 2;
    }
    try {
        GUID guid{};
        if (FAILED(CLSIDFromString(L"{6DE38B4B-569D-47F4-830E-6E84B65F0B50}", &guid))) {
            throw std::runtime_error("Invalid Ryujin WinUSB interface GUID");
        }
        auto hid = open_hid();
        auto usb_file = open_winusb(guid);
        WinUsbHandle usb;
        if (!WinUsb_Initialize(usb_file.value, &usb.value)) throw win_error("WinUsb_Initialize");

        USB_INTERFACE_DESCRIPTOR descriptor{};
        if (!WinUsb_QueryInterfaceSettings(usb.value, 0, &descriptor)) throw win_error("WinUsb_QueryInterfaceSettings");
        UCHAR bulk_out = 0;
        for (UCHAR i = 0; i < descriptor.bNumEndpoints; ++i) {
            WINUSB_PIPE_INFORMATION pipe{};
            if (!WinUsb_QueryPipe(usb.value, 0, i, &pipe)) throw win_error("WinUsb_QueryPipe");
            if (pipe.PipeType == UsbdPipeTypeBulk && !(pipe.PipeId & 0x80)) bulk_out = pipe.PipeId;
        }
        if (!bulk_out) throw std::runtime_error("Bulk OUT endpoint was not found");
        BOOL enabled = TRUE;
        WinUsb_SetPipePolicy(usb.value, bulk_out, AUTO_CLEAR_STALL, sizeof(enabled), &enabled);
        ULONG timeout = kBulkTimeoutMs;
        WinUsb_SetPipePolicy(usb.value, bulk_out, PIPE_TRANSFER_TIMEOUT, sizeof(timeout), &timeout);

        auto status = query_status(hid.value);
        if (status[5] != 0x20) set_live_mode(hid.value);
        std::cerr << "READY direct HID+WinUSB backend; mode=0x20 pipe=0x"
                  << std::hex << static_cast<int>(bulk_out) << std::dec << "\n";

        HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
        std::vector<unsigned char> pending(frame_size), last_frame;
        size_t filled = 0;
        bool frame_ack_pending = false;
        auto next_status = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        unsigned long frames = 0, mode_losses = 0, query_timeouts = 0;
        for (;;) {
            const auto now = std::chrono::steady_clock::now();
            if (now >= next_status) {
                next_status = now + std::chrono::seconds(5);
                try {
                    if (frame_ack_pending) {
                        drain_ack(hid.value);
                        frame_ack_pending = false;
                    }
                    status = query_status(hid.value);
                    if (status[5] != 0x20) {
                        ++mode_losses;
                        std::cerr << "WARN mode loss 0x" << std::hex << static_cast<int>(status[5])
                                  << " -> 0x20" << std::dec << " count=" << mode_losses << "\n";
                        set_live_mode(hid.value);
                        if (!last_frame.empty()) {
                            send_frame(hid.value, usb.value, bulk_out, last_frame);
                            frame_ack_pending = true;
                        }
                    }
                } catch (const std::exception& ex) {
                    ++query_timeouts;
                    std::cerr << "WARN status query " << query_timeouts << ": " << ex.what() << "\n";
                }
            }

            DWORD available = 0;
            if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) {
                if (GetLastError() == ERROR_BROKEN_PIPE) break;
                throw win_error("PeekNamedPipe(stdin)");
            }
            if (!available) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
                continue;
            }
            const DWORD wanted = static_cast<DWORD>((std::min)(static_cast<size_t>(available), frame_size - filled));
            DWORD received = 0;
            if (!ReadFile(input, pending.data() + filled, wanted, &received, nullptr)) {
                if (GetLastError() == ERROR_BROKEN_PIPE) break;
                throw win_error("ReadFile(stdin)");
            }
            filled += received;
            if (filled == frame_size) {
                // The previous frame acknowledgement has had the whole Python
                // render interval to arrive, so this read is normally immediate.
                // Keeping it here preserves protocol synchronization without
                // putting its latency on the previous frame's critical path.
                if (frame_ack_pending) {
                    drain_ack(hid.value);
                    frame_ack_pending = false;
                }
                send_frame(hid.value, usb.value, bulk_out, pending);
                frame_ack_pending = true;
                last_frame = pending;
                filled = 0;
                ++frames;
                if (frames % 60 == 0) {
                    std::cerr << "INFO frames=" << frames << " mode_losses=" << mode_losses
                              << " query_timeouts=" << query_timeouts << "\n";
                }
            }
        }
        if (frame_ack_pending) drain_ack(hid.value);
        std::cerr << "STOP frames=" << frames << " mode_losses=" << mode_losses
                  << " query_timeouts=" << query_timeouts << "\n";
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "ERROR " << ex.what() << "\n";
        return 1;
    }
}
