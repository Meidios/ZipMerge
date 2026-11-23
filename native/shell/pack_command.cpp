// The Explorer process only formats the menu and launches ZipMerge.
#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <wrl.h>
#include <filesystem>
#include <memory>
#include <string>

using namespace Microsoft::WRL;
namespace fs = std::filesystem;

constexpr wchar_t settingsKey[] =
    L"Software\\Classes\\CLSID\\{785B95F6-22B9-42BE-AC96-3F6F14720D9A}";

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

static HRESULT selectedPath(IShellItemArray* items, fs::path& path) {
    DWORD count = 0;
    if (!items || FAILED(items->GetCount(&count)) || count != 1) return E_INVALIDARG;
    ComPtr<IShellItem> item;
    HRESULT hr = items->GetItemAt(0, &item);
    if (FAILED(hr)) return hr;
    PWSTR raw = nullptr;
    hr = item->GetDisplayName(SIGDN_FILESYSPATH, &raw);
    if (FAILED(hr)) return hr;
    std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> owner(raw, &CoTaskMemFree);
    path = raw;
    return path.filename().empty() || PathIsRootW(raw) ? E_INVALIDARG : S_OK;
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

class __declspec(uuid("785B95F6-22B9-42BE-AC96-3F6F14720D9A")) PackCommand final :
    public RuntimeClass<RuntimeClassFlags<ClassicCom>, IExplorerCommand> {
public:
    IFACEMETHODIMP GetTitle(IShellItemArray* items, PWSTR* title) override {
        *title = nullptr;
        return guarded([&]() -> HRESULT {
            fs::path source;
            HRESULT hr = selectedPath(items, source);
            if (FAILED(hr)) return hr;
            const auto parent = source.parent_path();
            const DWORD attrs = fileAttributes(source);
            if (attrs == INVALID_FILE_ATTRIBUTES) return HRESULT_FROM_WIN32(GetLastError());
            const auto base = (attrs & FILE_ATTRIBUTE_DIRECTORY) ? source.filename() :
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
            fs::path source;
            if (SUCCEEDED(selectedPath(items, source))) *state = ECS_ENABLED;
            return S_OK;
        });
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        return guarded([&]() -> HRESULT {
            fs::path source;
            HRESULT hr = selectedPath(items, source);
            if (FAILED(hr)) return hr;
            const auto exe = setting(L"Application");
            auto command = setting(L"PackCommand");
            if (exe.empty() || command.empty()) return E_UNEXPECTED;
            command += L" \"" + source.native() + L"\"";
            STARTUPINFOW startup{};
            startup.cb = sizeof(startup);
            PROCESS_INFORMATION process{};
            if (!CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, FALSE,
                                CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process))
                return HRESULT_FROM_WIN32(GetLastError());
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
            return S_OK;
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
