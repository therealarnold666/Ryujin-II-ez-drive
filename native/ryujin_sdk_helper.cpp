#include <windows.h>

#include <fcntl.h>
#include <io.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

using CheckDevicesFn = int(__cdecl*)();
using CapabilityStrFn = const char*(__cdecl*)();
using UpdatePanelFn = bool(__cdecl*)(int device_index, const unsigned char* buffer, int byte_count);

struct Sdk {
    HMODULE module = nullptr;
    CheckDevicesFn check = nullptr;
    CapabilityStrFn capability = nullptr;
    UpdatePanelFn update = nullptr;

    explicit Sdk(const wchar_t* path) {
        module = LoadLibraryW(path);
        if (!module) throw std::runtime_error("LoadLibraryW failed: " + std::to_string(GetLastError()));
        check = reinterpret_cast<CheckDevicesFn>(GetProcAddress(module, "CheckNumberOfSupportedDevices"));
        capability = reinterpret_cast<CapabilityStrFn>(GetProcAddress(module, "GetLCDCapabilityStr"));
        update = reinterpret_cast<UpdatePanelFn>(GetProcAddress(module, "UpdatePanelFromBuffer"));
        if (!check || !capability || !update) throw std::runtime_error("Required ROGAIOSDK exports are missing");
    }
    ~Sdk() { if (module) FreeLibrary(module); }
};

static bool read_exact(std::istream& in, unsigned char* target, size_t size) {
    size_t offset = 0;
    while (offset < size) {
        in.read(reinterpret_cast<char*>(target + offset), static_cast<std::streamsize>(size - offset));
        const auto got = static_cast<size_t>(in.gcount());
        if (got == 0) return false;
        offset += got;
    }
    return true;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) {
        std::wcerr << L"usage: ryujin_sdk_helper <info|stream> <ROGAIOSDK.dll> [device-index frame-size]\n";
        return 2;
    }
    try {
        Sdk sdk(argv[2]);
        if (std::wstring(argv[1]) == L"info") {
            const int devices = sdk.check();
            const char* capability = sdk.capability();
            std::cout << "{\"devices\":" << devices << ",\"capability\":";
            if (capability) {
                std::cout << "\"";
                for (const char* p = capability; *p; ++p) {
                    if (*p == '"' || *p == '\\') std::cout << '\\';
                    if (*p == '\n') std::cout << "\\n"; else std::cout << *p;
                }
                std::cout << "\"";
            } else std::cout << "null";
            std::cout << "}\n";
            return devices > 0 ? 0 : 3;
        }
        if (std::wstring(argv[1]) != L"stream" || argc != 5) return 2;
        const int index = _wtoi(argv[3]);
        const int frame_size = _wtoi(argv[4]);
        if (frame_size <= 0 || frame_size > 16 * 1024 * 1024) throw std::runtime_error("invalid frame size");
        const int devices = sdk.check();
        if (index < 0 || index >= devices) throw std::runtime_error("Ryujin device index is unavailable");
        _setmode(_fileno(stdin), _O_BINARY);
        std::vector<unsigned char> frame(static_cast<size_t>(frame_size));
        while (read_exact(std::cin, frame.data(), frame.size())) {
            if (!sdk.update(index, frame.data(), frame_size)) {
                std::cerr << "UpdatePanelFromBuffer returned false\n";
                return 5;
            }
        }
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << ex.what() << "\n";
        return 1;
    }
}
