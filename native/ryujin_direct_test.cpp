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
constexpr int kWidth = 320;
constexpr int kHeight = 240;
constexpr int kFrameBytes = kWidth * kHeight * 3;

struct DeviceInfoSet {
    HDEVINFO value = INVALID_HANDLE_VALUE;
    ~DeviceInfoSet() {
        if (value != INVALID_HANDLE_VALUE) SetupDiDestroyDeviceInfoList(value);
    }
};

struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    Handle() = default;
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value(other.value) { other.value = INVALID_HANDLE_VALUE; }
    Handle& operator=(Handle&& other) noexcept {
        if (this != &other) {
            if (value != INVALID_HANDLE_VALUE) CloseHandle(value);
            value = other.value;
            other.value = INVALID_HANDLE_VALUE;
        }
        return *this;
    }
    ~Handle() {
        if (value != INVALID_HANDLE_VALUE) CloseHandle(value);
    }
};

struct WinUsbHandle {
    WINUSB_INTERFACE_HANDLE value = nullptr;
    ~WinUsbHandle() {
        if (value) WinUsb_Free(value);
    }
};

std::runtime_error win_error(const char* operation) {
    return std::runtime_error(std::string(operation) + " failed: " + std::to_string(GetLastError()));
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
    GUID hid_guid{};
    HidD_GetHidGuid(&hid_guid);
    for (const auto& path : interface_paths(hid_guid)) {
        Handle handle;
        handle.value = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                                   FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
                                   FILE_FLAG_OVERLAPPED, nullptr);
        if (handle.value == INVALID_HANDLE_VALUE) continue;
        HIDD_ATTRIBUTES attributes{};
        attributes.Size = sizeof(attributes);
        if (HidD_GetAttributes(handle.value, &attributes) &&
            attributes.VendorID == kVendorId && attributes.ProductID == kProductId) {
            return handle;
        }
    }
    throw std::runtime_error("Ryujin HID interface was not found or is busy");
}

Handle open_winusb_device(const GUID& guid) {
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
    throw std::runtime_error("Ryujin WinUSB interface was not found or is busy");
}

bool overlapped_io(HANDLE handle, bool write, unsigned char* data, DWORD bytes, DWORD timeout_ms) {
    OVERLAPPED ov{};
    ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!ov.hEvent) throw win_error("CreateEventW");
    DWORD transferred = 0;
    BOOL ok = write ? WriteFile(handle, data, bytes, &transferred, &ov)
                    : ReadFile(handle, data, bytes, &transferred, &ov);
    if (!ok && GetLastError() != ERROR_IO_PENDING) {
        CloseHandle(ov.hEvent);
        return false;
    }
    const DWORD wait = WaitForSingleObject(ov.hEvent, timeout_ms);
    if (wait != WAIT_OBJECT_0) {
        CancelIoEx(handle, &ov);
        CloseHandle(ov.hEvent);
        return false;
    }
    ok = GetOverlappedResult(handle, &ov, &transferred, FALSE);
    CloseHandle(ov.hEvent);
    return ok && transferred == bytes;
}

bool hid_exchange(HANDLE hid, const std::vector<unsigned char>& command,
                  std::vector<unsigned char>* response, DWORD timeout_ms = 1000) {
    std::vector<unsigned char> outbound = command;
    if (!overlapped_io(hid, true, outbound.data(), static_cast<DWORD>(outbound.size()), timeout_ms)) return false;
    if (!response) return true;
    response->assign(65, 0);
    return overlapped_io(hid, false, response->data(), static_cast<DWORD>(response->size()), timeout_ms);
}

bool winusb_write(WINUSB_INTERFACE_HANDLE usb, UCHAR pipe, unsigned char* data,
                  ULONG bytes, ULONG* transferred, DWORD timeout_ms = 5000) {
    OVERLAPPED ov{};
    ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!ov.hEvent) throw win_error("CreateEventW(WinUSB)");
    *transferred = 0;
    BOOL ok = WinUsb_WritePipe(usb, pipe, data, bytes, transferred, &ov);
    if (!ok && GetLastError() != ERROR_IO_PENDING) {
        CloseHandle(ov.hEvent);
        return false;
    }
    const DWORD wait = WaitForSingleObject(ov.hEvent, timeout_ms);
    if (wait != WAIT_OBJECT_0) {
        WinUsb_AbortPipe(usb, pipe);
        CloseHandle(ov.hEvent);
        return false;
    }
    ok = WinUsb_GetOverlappedResult(usb, &ov, transferred, FALSE);
    CloseHandle(ov.hEvent);
    return ok;
}

std::vector<unsigned char> query_status(HANDLE hid) {
    std::vector<unsigned char> query(65, 0);
    query[0] = 0xEC;
    query[1] = 0xD0;
    std::vector<unsigned char> response;
    if (!hid_exchange(hid, query, &response)) throw std::runtime_error("Ryujin HID status query timed out");
    if (response.size() < 5) throw std::runtime_error("Ryujin HID status response was too short");
    return response;
}

void set_live_mode(HANDLE hid, unsigned char panel_id, unsigned char token) {
    (void)panel_id;
    (void)token;
    std::vector<unsigned char> command(65, 0);
    command[0] = 0xEC;
    command[1] = 0x51;
    command[2] = 0x20;
    std::vector<unsigned char> response;
    if (!hid_exchange(hid, command, &response)) throw std::runtime_error("Setting Ryujin live mode failed");
}

void prepare_frame_transfer(HANDLE hid) {
    std::vector<unsigned char> command(65, 0);
    command[0] = 0xEC;
    command[1] = 0x7F;
    command[2] = 0x03;
    command[4] = 0x84;
    command[5] = 0x03;
    std::vector<unsigned char> response;
    if (!hid_exchange(hid, command, &response)) throw std::runtime_error("Preparing Ryujin frame transfer failed");
}

void drain_frame_ack(HANDLE hid) {
    std::vector<unsigned char> ack(65, 0);
    // Firmware normally sends EE 20 after accepting a complete bulk frame.
    // A missing acknowledgement is logged by the next status query, so do not
    // fail the visual test solely because this best-effort drain timed out.
    overlapped_io(hid, false, ack.data(), static_cast<DWORD>(ack.size()), 250);
}

std::string timestamp() {
    SYSTEMTIME st{};
    GetLocalTime(&st);
    char text[64]{};
    std::snprintf(text, sizeof(text), "%04u-%02u-%02u %02u:%02u:%02u.%03u",
                  st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
    return text;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    std::cout << std::unitbuf;
    std::cerr << std::unitbuf;
    int duration_seconds = 1800;
    if (argc >= 2) duration_seconds = (std::max)(10, _wtoi(argv[1]));
    int refresh_seconds = 0;
    if (argc >= 3) refresh_seconds = (std::max)(0, _wtoi(argv[2]));
    try {
        GUID winusb_guid{};
        if (FAILED(CLSIDFromString(L"{6DE38B4B-569D-47F4-830E-6E84B65F0B50}", &winusb_guid))) {
            throw std::runtime_error("Invalid Ryujin WinUSB interface GUID");
        }

        auto hid = open_hid();
        auto usb_file = open_winusb_device(winusb_guid);
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
        if (!bulk_out) throw std::runtime_error("Ryujin bulk OUT pipe was not found");

        BOOL enabled = TRUE;
        WinUsb_SetPipePolicy(usb.value, bulk_out, AUTO_CLEAR_STALL, sizeof(enabled), &enabled);
        ULONG timeout = 3000;
        WinUsb_SetPipePolicy(usb.value, bulk_out, PIPE_TRANSFER_TIMEOUT, sizeof(timeout), &timeout);

        auto status = query_status(hid.value);
        std::cout << timestamp() << " HID status bytes:";
        for (size_t i = 0; i < 16 && i < status.size(); ++i) {
            std::cout << " " << std::hex << std::setw(2) << std::setfill('0')
                      << static_cast<int>(status[i]);
        }
        std::cout << std::dec << "\n";
        // EC 50 response layout on Ryujin II firmware AURJ1-S750-0104:
        // [3] token, [4] panel id, [5] active display mode.
        unsigned char token = status[3];
        unsigned char panel_id = status[4];
        unsigned char mode = status[5];
        std::cout << timestamp() << " direct backend opened; initial mode=0x"
                  << std::hex << static_cast<int>(mode) << " panel=0x" << static_cast<int>(panel_id)
                  << " pipe=0x" << static_cast<int>(bulk_out) << std::dec << "\n";
        if (mode != 0x20) {
            set_live_mode(hid.value, panel_id, token);
            std::cout << timestamp() << " set live mode to 0x20\n";
        }

        // Deliberately simple full-screen colors. With periodic refresh enabled,
        // blue and orange alternate so frame delivery is obvious to the eye.
        std::vector<unsigned char> frame(kFrameBytes);
        for (size_t i = 0; i < frame.size(); i += 3) {
            frame[i] = 0x30;
            frame[i + 1] = 0x70;
            frame[i + 2] = 0xD0;
        }
        ULONG written = 0;
        prepare_frame_transfer(hid.value);
        if (!winusb_write(usb.value, bulk_out, frame.data(), static_cast<ULONG>(frame.size()), &written) ||
            written != frame.size()) {
            throw win_error("WinUsb_WritePipe(initial frame)");
        }
        drain_frame_ack(hid.value);
        unsigned int frames_sent = 1;
        std::cout << timestamp() << " static frame sent; monitoring for " << duration_seconds << " seconds"
                  << "; refresh_seconds=" << refresh_seconds << "\n";

        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(duration_seconds);
        auto next_status = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        auto next_frame = refresh_seconds > 0
            ? std::chrono::steady_clock::now() + std::chrono::seconds(refresh_seconds)
            : deadline;
        unsigned int mode_losses = 0;
        unsigned int query_timeouts = 0;
        while (std::chrono::steady_clock::now() < deadline) {
            const auto wake = (std::min)({deadline, next_status, next_frame});
            std::this_thread::sleep_until(wake);
            const auto now = std::chrono::steady_clock::now();

            if (now >= next_status && now < deadline) {
                next_status = now + std::chrono::seconds(5);
                try {
                    status = query_status(hid.value);
                } catch (const std::exception&) {
                    ++query_timeouts;
                    if (query_timeouts <= 3 || query_timeouts % 12 == 0) {
                        std::cout << timestamp() << " HID status query timeout (count=" << query_timeouts << ")\n";
                    }
                    continue;
                }
                token = status[3];
                panel_id = status[4];
                mode = status[5];
                if (mode != 0x20) {
                    ++mode_losses;
                    std::cout << timestamp() << " MODE LOSS: 0x" << std::hex << static_cast<int>(mode)
                              << " -> 0x20" << std::dec << "\n";
                    set_live_mode(hid.value, panel_id, token);
                    next_frame = now;
                }
            }

            if (refresh_seconds > 0 && std::chrono::steady_clock::now() >= next_frame && now < deadline) {
                const bool orange = (frames_sent % 2) != 0;
                for (size_t i = 0; i < frame.size(); i += 3) {
                    frame[i] = orange ? 0x20 : 0xD0;
                    frame[i + 1] = orange ? 0x80 : 0x70;
                    frame[i + 2] = orange ? 0xD0 : 0x30;
                }
                prepare_frame_transfer(hid.value);
                written = 0;
                if (!winusb_write(usb.value, bulk_out, frame.data(), static_cast<ULONG>(frame.size()), &written) ||
                    written != frame.size()) {
                    throw win_error("WinUsb_WritePipe(periodic frame)");
                }
                drain_frame_ack(hid.value);
                ++frames_sent;
                if (frames_sent % 60 == 0) {
                    std::cout << timestamp() << " progress: frames_sent=" << frames_sent
                              << " mode_losses=" << mode_losses
                              << " query_timeouts=" << query_timeouts << "\n";
                }
                next_frame = std::chrono::steady_clock::now() + std::chrono::seconds(refresh_seconds);
            }
        }
        std::cout << timestamp() << " finished; mode_losses=" << mode_losses
                  << " query_timeouts=" << query_timeouts
                  << " frames_sent=" << frames_sent << "\n";
        return mode_losses == 0 ? 0 : 4;
    } catch (const std::exception& ex) {
        std::cerr << timestamp() << " ERROR: " << ex.what() << "\n";
        return 1;
    }
}
