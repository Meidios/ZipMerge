// Build the classic Windows Shell menu against a disposable registration.
#include <windows.h>
#include <VersionHelpers.h>
#include <shlobj.h>
#include <wrl/client.h>
#include <iostream>
#include <filesystem>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;
#define CHECK(expr) do { HRESULT hr = (expr); if (FAILED(hr)) { \
    std::cerr << #expr << ": " << std::hex << hr << "\n"; return 1; } } while (0)

static void printLabel(const wchar_t* label) {
    char utf8[8192]{};
    WideCharToMultiByte(CP_UTF8, 0, label, -1, utf8, sizeof(utf8), nullptr, nullptr);
    std::cerr << '[' << utf8 << "]\n";
}

// The product targets the Windows client shell. Server's default menu does not
// enumerate the process-private command on the hosted runner. Exercise the same
// registered COM command, selection, filename and invocation on Server.
static int checkServerCommand(REFCLSID cls, std::vector<PCIDLIST_ABSOLUTE>& selected,
                              const wchar_t* expected, bool invoke) {
    ComPtr<IExplorerCommand> command;
    CHECK(CoCreateInstance(cls, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&command)));
    ComPtr<IShellItemArray> selection;
    CHECK(SHCreateShellItemArrayFromIDLists(static_cast<UINT>(selected.size()), selected.data(), &selection));
    EXPCMDSTATE state = ECS_HIDDEN;
    CHECK(command->GetState(selection.Get(), TRUE, &state));
    if (state != ECS_ENABLED) return 4;
    PWSTR title = nullptr;
    CHECK(command->GetTitle(selection.Get(), &title));
    bool matches = std::wstring(title) == expected;
    if (!matches) printLabel(title);
    CoTaskMemFree(title);
    if (!matches) return 4;
    if (invoke) CHECK(command->Invoke(selection.Get(), nullptr));
    return 0;
}

static int checkClientMenu(IShellFolder* folder, PCIDLIST_ABSOLUTE parent, std::vector<PCUITEMID_CHILD>& children,
                           HKEY association, const wchar_t* expected, bool invoke) {
    DEFCONTEXTMENU params{};
    params.pidlFolder = parent;
    params.psf = folder;
    params.cidl = static_cast<UINT>(children.size());
    params.apidl = children.data();
    params.cKeys = 1;
    params.aKeys = &association;
    ComPtr<IContextMenu> context;
    CHECK(SHCreateDefaultContextMenu(&params, IID_PPV_ARGS(&context)));
    HMENU menu = CreatePopupMenu();
    CHECK(context->QueryContextMenu(menu, 0, 1, 0x7fff, CMF_NORMAL | CMF_SYNCCASCADEMENU | CMF_ITEMMENU));
    int result = 4;
    for (int i = 0; i < GetMenuItemCount(menu); ++i) {
        wchar_t label[1024]{};
        GetMenuStringW(menu, i, label, ARRAYSIZE(label), MF_BYPOSITION);
        if (std::wstring(label) != expected) continue;
        if (invoke) {
            CMINVOKECOMMANDINFO info{sizeof(info)};
            info.lpVerb = MAKEINTRESOURCEA(GetMenuItemID(menu, i) - 1);
            info.nShow = SW_SHOWNORMAL;
            CHECK(context->InvokeCommand(&info));
        }
        result = 0;
        break;
    }
    if (result != 0) {
        std::cerr << "Expected Shell menu item: ";
        printLabel(expected);
        std::cerr << "Actual Shell menu items:\n";
        for (int i = 0; i < GetMenuItemCount(menu); ++i) {
            wchar_t label[1024]{};
            GetMenuStringW(menu, i, label, ARRAYSIZE(label), MF_BYPOSITION);
            printLabel(label);
        }
    }
    DestroyMenu(menu);
    return result;
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 5) return 2;
    CHECK(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    // Bind the real filesystem selection before redirecting this process's HKCU.
    std::vector<PCIDLIST_ABSOLUTE> selected;
    std::vector<PCUITEMID_CHILD> children;
    std::string line;
    while (std::getline(std::cin, line)) {
        PIDLIST_ABSOLUTE pidl = nullptr;
        CHECK(SHParseDisplayName(std::filesystem::u8path(line).c_str(), nullptr, &pidl, 0, nullptr));
        selected.push_back(pidl);
        children.push_back(ILFindLastID(pidl));
    }
    if (selected.empty()) return 2;
    ComPtr<IShellFolder> folder;
    PCUITEMID_CHILD child = nullptr;
    CHECK(SHBindToParent(selected.front(), IID_PPV_ARGS(&folder), &child));
    PIDLIST_ABSOLUTE parent = ILCloneFull(selected.front());
    ILRemoveLastID(parent);
    HKEY testRoot, association;
    CHECK(HRESULT_FROM_WIN32(RegOpenKeyExW(HKEY_CURRENT_USER, argv[1], 0, KEY_READ, &testRoot)));
    CHECK(HRESULT_FROM_WIN32(RegOpenKeyExW(testRoot, L"Software\\Classes\\AllFilesystemObjects",
                                           0, KEY_READ, &association)));
    CHECK(HRESULT_FROM_WIN32(RegOverridePredefKey(HKEY_CURRENT_USER, testRoot)));
    HKEY classes;
    CHECK(HRESULT_FROM_WIN32(RegOpenKeyExW(testRoot, L"Software\\Classes", 0, KEY_READ, &classes)));
    CHECK(HRESULT_FROM_WIN32(RegOverridePredefKey(HKEY_CLASSES_ROOT, classes)));
    // COM activation also consults the system class cache outside the process.
    // Expose this DLL's real factory only for the lifetime of this test host.
    CLSID cls;
    wchar_t registeredClass[40]{};
    DWORD bytes = sizeof(registeredClass);
    CHECK(HRESULT_FROM_WIN32(RegGetValueW(association, L"shell\\ZipMerge.CompressHere",
        L"ExplorerCommandHandler", RRF_RT_REG_SZ, nullptr, registeredClass, &bytes)));
    CHECK(CLSIDFromString(registeredClass, &cls));
    HMODULE library = LoadLibraryW(argv[2]);
    if (!library) return 3;
    auto getClass = reinterpret_cast<decltype(&DllGetClassObject)>(
        GetProcAddress(library, "DllGetClassObject"));
    if (!getClass) return 3;
    ComPtr<IClassFactory> factory;
    CHECK(getClass(cls, IID_PPV_ARGS(&factory)));
    DWORD cookie;
    CHECK(CoRegisterClassObject(cls, factory.Get(), CLSCTX_INPROC_SERVER, REGCLS_MULTIPLEUSE, &cookie));
    int result;
    if (IsWindowsServer()) {
        result = checkServerCommand(cls, selected, argv[3], std::wstring(argv[4]) == L"invoke");
    } else {
        result = checkClientMenu(folder.Get(), parent, children, association, argv[3],
                                 std::wstring(argv[4]) == L"invoke");
    }
    folder.Reset();
    CoRevokeClassObject(cookie);
    factory.Reset();
    RegOverridePredefKey(HKEY_CURRENT_USER, nullptr);
    RegOverridePredefKey(HKEY_CLASSES_ROOT, nullptr);
    RegCloseKey(classes);
    RegCloseKey(association);
    RegCloseKey(testRoot);
    CoTaskMemFree(parent);
    for (auto item : selected) ILFree(const_cast<PIDLIST_ABSOLUTE>(item));
    CoUninitialize();
    return result;
}
