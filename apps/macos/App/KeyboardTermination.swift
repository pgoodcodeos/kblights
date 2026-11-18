import AppKit
import Combine

@MainActor final class KeyboardTermination {
    private let activity: KeyboardDeviceActivity
    private var idleObservation: AnyCancellable?
    private var isWaiting = false
    private var isConfirming = false
    private let confirmDiscard: @MainActor () -> Bool

    init(
        activity: KeyboardDeviceActivity,
        confirmDiscard: @escaping @MainActor () -> Bool = { KeyboardDraftProtection.shared.confirmQuit() }
    ) {
        self.activity = activity
        self.confirmDiscard = confirmDiscard
    }

    func request(reply: @escaping @MainActor (Bool) -> Void) -> NSApplication.TerminateReply {
        guard !isWaiting else { return .terminateLater }

        guard !isConfirming else { return .terminateCancel }

        // Modal confirmation runs a nested event loop. Block fresh work before opening it.
        activity.prepareToQuit()
        guard activity.isBusy else { return confirm() ? .terminateNow : .terminateCancel }

        isWaiting = true
        idleObservation = activity.$isBusy.removeDuplicates().filter { !$0 }.sink { [weak self] _ in
            // AppKit waits for terminateLater replies in modalPanel mode. Schedule
            // explicitly for that mode as well as the normal and event-tracking loops.
            RunLoop.main.perform(inModes: [.default, .modalPanel, .eventTracking]) { [weak self] in
                MainActor.assumeIsolated {
                    guard let self, self.isWaiting, !self.activity.isBusy else { return }

                    self.idleObservation = nil
                    let accepted = self.confirm()
                    self.isWaiting = false
                    reply(accepted)
                }
            }

            CFRunLoopWakeUp(CFRunLoopGetMain())
        }

        return .terminateLater
    }

    private func confirm() -> Bool {
        isConfirming = true
        defer { isConfirming = false }

        let accepted = confirmDiscard()
        if !accepted { activity.cancelQuit() }

        return accepted
    }
}
