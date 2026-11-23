// The Explorer process only formats the menu and launches ZipMerge.
#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <wrl.h>
#include <filesystem>
#include <memory>
#include <string>
#include <type_traits>
#include <vector>

using namespace Microsoft::WRL;
namespace fs = std::filesystem;

constexpr wchar_t settingsKey[] =
    L"Software\\Classes\\CLSID\\{4F6B6046-26A1-4185-9124-5DE914CFD89B}";

static std::wstring setting(const wchar_t* name) {
    DWORD bytes = 0;
    if (RegGetValueW(HKEY_CURRENT_USER, settingsKey, name, RRF_RT_REG_SZ,
                     nullptr, nullptr, &bytes) != ERROR_SUCCESS) return {};
    std::wstring value(bytes / sizeof(wchar_t), L'\0');
    if (RegGetValueW(HKEY_CURRENT_USER, settingsKey, name, RRF_RT_REG_SZ,
                     nullptr, value.data(), &bytes) != ERROR_SUCCESS) return {};
    value.resize(wcslen(value.c_str()));
    return value;
}

static HRESULT selectedPaths(IShellItemArray* items, std::vector<fs::path>& paths) {
    DWORD count = 0;
    if (!items || FAILED(items->GetCount(&count)) || count == 0) return E_INVALIDARG;
    paths.reserve(count);
    for (DWORD i = 0; i < count; ++i) {
        ComPtr<IShellItem> item;
        HRESULT hr = items->GetItemAt(i, &item);
        if (FAILED(hr)) return hr;
        PWSTR raw = nullptr;
        hr = item->GetDisplayName(SIGDN_FILESYSPATH, &raw);
        if (FAILED(hr)) return hr;
        std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> owner(raw, &CoTaskMemFree);
        fs::path& path = paths.emplace_back(raw);
        if (path.filename().empty() || PathIsRootW(raw)) return E_INVALIDARG;
    }
    return S_OK;
}

// A delete-on-close file carries the UTF-16 selection through a single inherited
// handle. It survives startup and is cleaned up even if either process exits early.
static HRESULT launchPack(const std::wstring& exe, std::wstring command,
                          const std::vector<fs::path>& paths) {
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(STARTUPINFOW);
    DWORD flags = CREATE_NO_WINDOW;
    Wrappers::FileHandle selection;
    HANDLE handle = nullptr;
    std::vector<BYTE> attributeStorage;
    std::unique_ptr<std::remove_pointer_t<LPPROC_THREAD_ATTRIBUTE_LIST>, decltype(&DeleteProcThreadAttributeList)>
        attributes(nullptr, DeleteProcThreadAttributeList);
    if (paths.size() == 1) {
        command += L" \"" + paths.front().native() + L"\"";
    } else {
        wchar_t directory[MAX_PATH + 1];
        DWORD length = GetTempPathW(ARRAYSIZE(directory), directory);
        if (!length) return HRESULT_FROM_WIN32(GetLastError());
        if (length >= ARRAYSIZE(directory)) return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);
        GUID guid;
        HRESULT hr = CoCreateGuid(&guid);
        if (FAILED(hr)) return hr;
        wchar_t id[40]{};
        StringFromGUID2(guid, id, ARRAYSIZE(id));
        const auto filename = fs::path(directory) / (std::wstring(L"ZipMerge-") + id + L".lst");
        SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
        selection.Attach(CreateFileW(filename.c_str(), GENERIC_READ | GENERIC_WRITE, 0, &security,
            CREATE_NEW, FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE, nullptr));
        if (!selection.IsValid()) return HRESULT_FROM_WIN32(GetLastError());
        for (const auto& path : paths) {
            DWORD size = static_cast<DWORD>((path.native().size() + 1) * sizeof(wchar_t));
            DWORD written;
            if (!WriteFile(selection.Get(), path.c_str(), size, &written, nullptr))
                return HRESULT_FROM_WIN32(GetLastError());
            if (written != size) return HRESULT_FROM_WIN32(ERROR_WRITE_FAULT);
        }
        if (!SetFilePointerEx(selection.Get(), {}, nullptr, FILE_BEGIN))
            return HRESULT_FROM_WIN32(GetLastError());
        SIZE_T bytes = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
        attributeStorage.resize(bytes);
        startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributeStorage.data());
        if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &bytes))
            return HRESULT_FROM_WIN32(GetLastError());
        attributes.reset(startup.lpAttributeList);
        handle = selection.Get();
        if (!UpdateProcThreadAttribute(attributes.get(), 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                       &handle, sizeof(handle), nullptr, nullptr))
            return HRESULT_FROM_WIN32(GetLastError());
        command += L" --selection-handle " + std::to_wstring(reinterpret_cast<UINT_PTR>(handle));
        startup.StartupInfo.cb = sizeof(startup);
        flags |= EXTENDED_STARTUPINFO_PRESENT;
    }
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, selection.IsValid(),
                        flags, nullptr, nullptr, &startup.StartupInfo, &process))
        return HRESULT_FROM_WIN32(GetLastError());
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return S_OK;
}

static DWORD fileAttributes(const fs::path& path) {
    const auto& text = path.native();
    if (text.size() < MAX_PATH || text.compare(0, 4, L"\\\\?\\") == 0)
        return GetFileAttributesW(text.c_str());
    const auto extended = text.compare(0, 2, L"\\\\") == 0
        ? L"\\\\?\\UNC\\" + text.substr(2) : L"\\\\?\\" + text;
    return GetFileAttributesW(extended.c_str());
}

// No C++ exception may cross a COM boundary into Explorer.
template<class Function> static HRESULT guarded(Function action) noexcept {
    try { return action(); }
    catch (const std::bad_alloc&) { return E_OUTOFMEMORY; }
    catch (...) { return E_FAIL; }
}

class __declspec(uuid("4F6B6046-26A1-4185-9124-5DE914CFD89B")) PackCommand final :
    public RuntimeClass<RuntimeClassFlags<ClassicCom>, IExplorerCommand> {
public:
    IFACEMETHODIMP GetTitle(IShellItemArray* items, PWSTR* title) override {
        *title = nullptr;
        return guarded([&]() -> HRESULT {
            std::vector<fs::path> paths;
            HRESULT hr = selectedPaths(items, paths);
            if (FAILED(hr)) return hr;
            const auto& source = paths.front();
            const auto parent = source.parent_path();
            const DWORD attrs = fileAttributes(source);
            if (attrs == INVALID_FILE_ATTRIBUTES) return HRESULT_FROM_WIN32(GetLastError());
            const auto base = paths.size() == 1 && (attrs & FILE_ATTRIBUTE_DIRECTORY) ? source.filename() :
                (PathIsRootW(parent.c_str()) ? source.stem() : parent.filename());
            std::wstring name = base.native() + L".zip";
            for (unsigned n = 2; fileAttributes(parent / name) != INVALID_FILE_ATTRIBUTES; ++n)
                name = base.native() + L"_" + std::to_wstring(n) + L".zip";
            std::wstring label = setting(L"TitleFormat");
            const auto slot = label.find(L"{0}");
            if (slot == std::wstring::npos) return E_FAIL;
            // Escape literal ampersands in a Windows menu label.
            for (size_t pos = 0; (pos = name.find(L'&', pos)) != std::wstring::npos; pos += 2)
                name.insert(pos, 1, L'&');
            label.replace(slot, 3, name);
            return SHStrDupW(label.c_str(), title);
        });
    }
    IFACEMETHODIMP GetIcon(IShellItemArray*, PWSTR* icon) override {
        *icon = nullptr;
        return guarded([&]() { return SHStrDupW(setting(L"Icon").c_str(), icon); });
    }
    IFACEMETHODIMP GetState(IShellItemArray* items, BOOL, EXPCMDSTATE* state) override {
        *state = ECS_HIDDEN;
        return guarded([&]() {
            std::vector<fs::path> paths;
            if (SUCCEEDED(selectedPaths(items, paths))) *state = ECS_ENABLED;
            return S_OK;
        });
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        return guarded([&]() -> HRESULT {
            std::vector<fs::path> paths;
            HRESULT hr = selectedPaths(items, paths);
            if (FAILED(hr)) return hr;
            const auto exe = setting(L"Application");
            auto command = setting(L"PackCommand");
            if (exe.empty() || command.empty()) return E_UNEXPECTED;
            return launchPack(exe, command, paths);
        });
    }
    IFACEMETHODIMP GetCanonicalName(GUID* id) override { *id = __uuidof(PackCommand); return S_OK; }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override { *flags = ECF_DEFAULT; return S_OK; }
    IFACEMETHODIMP GetToolTip(IShellItemArray*, PWSTR* tip) override { *tip = nullptr; return E_NOTIMPL; }
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** items) override { *items = nullptr; return E_NOTIMPL; }
};

CoCreatableClass(PackCommand);

STDAPI DllGetClassObject(REFCLSID id, REFIID iid, void** object) {
    return Module<InProc>::GetModule().GetClassObject(id, iid, object);
}
STDAPI DllCanUnloadNow() {
    return Module<InProc>::GetModule().GetObjectCount() ? S_FALSE : S_OK;
}
