// Build the classic Windows Shell menu against a disposable registration.
#include <windows.h>
#include <shlobj.h>
#include <wrl/client.h>
#include <iostream>
#include <string>

using Microsoft::WRL::ComPtr;
#define CHECK(expr) do { HRESULT hr = (expr); if (FAILED(hr)) { \
    std::cerr << #expr << ": " << std::hex << hr << "\n"; return 1; } } while (0)

int wmain(int argc, wchar_t** argv) {
    if (argc != 7) return 2;
    CHECK(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    // Bind the real filesystem selection before redirecting this process's HKCU.
    PIDLIST_ABSOLUTE absolute = nullptr;
    CHECK(SHParseDisplayName(argv[3], nullptr, &absolute, 0, nullptr));
    ComPtr<IShellFolder> folder;
    PCUITEMID_CHILD child = nullptr;
    CHECK(SHBindToParent(absolute, IID_PPV_ARGS(&folder), &child));
    PIDLIST_ABSOLUTE parent = ILCloneFull(absolute);
    ILRemoveLastID(parent);
    HKEY testRoot, association;
    CHECK(HRESULT_FROM_WIN32(RegOpenKeyExW(HKEY_CURRENT_USER, argv[1], 0, KEY_READ, &testRoot)));
    std::wstring key = std::wstring(L"Software\\Classes\\") + argv[4];
    CHECK(HRESULT_FROM_WIN32(RegOpenKeyExW(testRoot, key.c_str(), 0, KEY_READ, &association)));
    CHECK(HRESULT_FROM_WIN32(RegOverridePredefKey(HKEY_CURRENT_USER, testRoot)));
    HKEY classes;
    CHECK(HRESULT_FROM_WIN32(RegOpenKeyExW(testRoot, L"Software\\Classes", 0, KEY_READ, &classes)));
    CHECK(HRESULT_FROM_WIN32(RegOverridePredefKey(HKEY_CLASSES_ROOT, classes)));
    // COM activation also consults the system class cache outside the process.
    // Expose this DLL's real factory only for the lifetime of this test host.
    CLSID cls;
    CHECK(CLSIDFromString(L"{785B95F6-22B9-42BE-AC96-3F6F14720D9A}", &cls));
    HMODULE library = LoadLibraryW(argv[2]);
    if (!library) return 3;
    auto getClass = reinterpret_cast<decltype(&DllGetClassObject)>(
        GetProcAddress(library, "DllGetClassObject"));
    if (!getClass) return 3;
    ComPtr<IClassFactory> factory;
    CHECK(getClass(cls, IID_PPV_ARGS(&factory)));
    DWORD cookie;
    CHECK(CoRegisterClassObject(cls, factory.Get(), CLSCTX_INPROC_SERVER, REGCLS_MULTIPLEUSE, &cookie));
    DEFCONTEXTMENU params{};
    params.pidlFolder = parent;
    params.psf = folder.Get();
    params.cidl = 1;
    params.apidl = &child;
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
        if (std::wstring(label) != argv[5]) continue;
        if (std::wstring(argv[6]) == L"invoke") {
            CMINVOKECOMMANDINFO info{sizeof(info)};
            info.lpVerb = MAKEINTRESOURCEA(GetMenuItemID(menu, i) - 1);
            info.nShow = SW_SHOWNORMAL;
            CHECK(context->InvokeCommand(&info));
        }
        result = 0;
        break;
    }
    DestroyMenu(menu);
    context.Reset();
    folder.Reset();
    CoRevokeClassObject(cookie);
    factory.Reset();
    RegOverridePredefKey(HKEY_CURRENT_USER, nullptr);
    RegOverridePredefKey(HKEY_CLASSES_ROOT, nullptr);
    RegCloseKey(classes);
    RegCloseKey(association);
    RegCloseKey(testRoot);
    CoTaskMemFree(parent);
    CoTaskMemFree(absolute);
    CoUninitialize();
    return result;
}
