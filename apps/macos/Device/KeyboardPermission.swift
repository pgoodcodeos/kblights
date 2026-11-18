import AppKit
import Combine
import IOKit.hid

@MainActor final class KeyboardPermission: ObservableObject {
    enum Access {
        case unknown
        case denied
        case granted
    }

    @Published private(set) var access: Access
    private let check: @MainActor () -> Access
    private let request: @MainActor () -> Void
    private let showSettings: @MainActor () -> Void
    private let showApplication: @MainActor () -> Void
    private var didRequest = false

    init(
        check: @escaping @MainActor () -> Access = { KeyboardPermission.systemAccess() },
        request: @escaping @MainActor () -> Void = { _ = IOHIDRequestAccess(kIOHIDRequestTypeListenEvent) },
        showSettings: @escaping @MainActor () -> Void = { KeyboardPermission.showSystemSettings() },
        showApplication: @escaping @MainActor () -> Void = {
            let bundle = Bundle.main.bundleURL
            let runningCopy =
                bundle.pathExtension == "app"
                ? bundle : Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])
            NSWorkspace.shared.activateFileViewerSelecting([runningCopy])
        }
    ) {
        self.check = check
        self.request = request
        self.showSettings = showSettings
        self.showApplication = showApplication
        access = check()
    }

    var isGranted: Bool { access == .granted }

    static var hasSystemAccess: Bool { systemAccess() == .granted }

    static func openSettings() {
        if !hasSystemAccess { _ = IOHIDRequestAccess(kIOHIDRequestTypeListenEvent) }

        showSystemSettings()
    }

    private static func showSystemSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent")
        else { return }

        NSWorkspace.shared.open(url)
    }

    func refresh() { access = check() }

    func enableInputMonitoring() {
        requestIfNeeded(retry: true)
        if !isGranted { showSettings() }
    }

    func revealApplication() { showApplication() }

    func requestIfNeeded(retry: Bool = false) {
        refresh()

        // Denied access can also mean the app has no visible entry in Settings.
        // Ask macOS to register the requesting app before directing the user there.
        guard !isGranted, retry || !didRequest else { return }

        didRequest = true
        request()
        refresh()
    }

    private static func systemAccess() -> Access {
        switch IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) {
        case kIOHIDAccessTypeGranted: .granted
        case kIOHIDAccessTypeDenied: .denied
        default: .unknown
        }
    }
}
