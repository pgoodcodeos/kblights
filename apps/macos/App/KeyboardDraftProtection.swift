import AppKit
import SwiftUI

@MainActor final class KeyboardDraftProtection {
    static let shared = KeyboardDraftProtection()
    private let windowGuards = NSHashTable<KeyboardCloseGuardView>.weakObjects()
    private static var isPresentingConfirmation = false
    private let confirmDiscard: @MainActor () -> Bool

    init(confirmDiscard: @escaping @MainActor () -> Bool = { KeyboardDraftProtection.confirmDiscard(action: "quit") }) {
        self.confirmDiscard = confirmDiscard
    }

    fileprivate func register(_ view: KeyboardCloseGuardView) { windowGuards.add(view) }

    func confirmQuit() -> Bool {
        guard windowGuards.allObjects.contains(where: { $0.needsQuitConfirmation }) else { return true }

        return confirmDiscard()
    }

    static func confirmDiscard(action: String) -> Bool {
        guard !isPresentingConfirmation else { return false }

        isPresentingConfirmation = true
        defer { isPresentingConfirmation = false }

        let alert = NSAlert()
        alert.messageText = "Discard preview changes?"
        alert.informativeText =
            "Your preview has changes that have not been applied to the keyboard. Save a profile first if you want to keep them."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Keep editing")
        alert.addButton(withTitle: "Discard and " + action)
        return alert.runModal() == .alertSecondButtonReturn
    }
}

struct KeyboardCloseGuard: NSViewRepresentable {
    let activity: KeyboardDeviceActivity
    let hasChanges: @MainActor () -> Bool

    func makeNSView(context: Context) -> KeyboardCloseGuardView {
        KeyboardCloseGuardView(activity: activity, hasChanges: hasChanges)
    }

    func updateNSView(_ view: KeyboardCloseGuardView, context: Context) {
        view.hasChanges = hasChanges
        view.installDelegate()
    }

    static func dismantleNSView(_ view: KeyboardCloseGuardView, coordinator: ()) { view.removeDelegate() }
}

final class KeyboardCloseGuardView: NSView, NSWindowDelegate {
    private let activity: KeyboardDeviceActivity
    var hasChanges: @MainActor () -> Bool
    private let confirmClose: @MainActor () -> Bool
    private weak var guardedWindow: NSWindow?
    private var isClosed = false
    private var isConfirmingClose = false

    // NSObject's forwarding hooks are nonisolated; only consult the AppKit delegate on the main thread.
    nonisolated(unsafe) private weak var originalDelegate: (any NSWindowDelegate)?

    init(
        activity: KeyboardDeviceActivity, hasChanges: @escaping @MainActor () -> Bool,
        protection: KeyboardDraftProtection = .shared,
        confirmClose: @escaping @MainActor () -> Bool = { KeyboardDraftProtection.confirmDiscard(action: "close") }
    ) {
        self.activity = activity
        self.hasChanges = hasChanges
        self.confirmClose = confirmClose
        super.init(frame: .zero)
        protection.register(self)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        installDelegate()
    }

    var needsQuitConfirmation: Bool {
        guard let window, !isClosed || window.isVisible || window.isMiniaturized else { return false }

        return hasChanges()
    }

    func installDelegate() {
        guard let window else {
            removeDelegate()
            return
        }

        guard window.delegate !== self else { return }

        if guardedWindow !== window { isClosed = false }

        removeDelegate()
        guardedWindow = window
        originalDelegate = window.delegate
        window.delegate = self
    }

    func removeDelegate() {
        if guardedWindow?.delegate === self { guardedWindow?.delegate = originalDelegate }

        guardedWindow = nil
        originalDelegate = nil
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard !activity.isBusy, !activity.isQuitting, !isConfirmingClose else { return false }

        isConfirmingClose = true
        defer { isConfirmingClose = false }

        guard !hasChanges() || confirmClose() else { return false }

        let accepted = originalDelegate?.windowShouldClose?(sender) ?? true

        // Another window's queued work can start while the modal alert is open.
        return accepted && !activity.isBusy && !activity.isQuitting
    }

    func windowWillClose(_ notification: Notification) {
        isClosed = true
        originalDelegate?.windowWillClose?(notification)
    }

    func windowDidBecomeKey(_ notification: Notification) {
        isClosed = false
        originalDelegate?.windowDidBecomeKey?(notification)
    }

    override func responds(to selector: Selector!) -> Bool {
        if super.responds(to: selector) { return true }

        guard Thread.isMainThread else { return false }

        return originalDelegate?.responds(to: selector) == true
    }

    override func forwardingTarget(for selector: Selector!) -> Any? {
        guard Thread.isMainThread, originalDelegate?.responds(to: selector) == true else {
            return super.forwardingTarget(for: selector)
        }

        return originalDelegate
    }
}
