import AppKit
import XCTest

@testable import KeyboardLights

final class KeyboardTerminationTests: XCTestCase {
    @MainActor func testKeepEditingCancelsQuitAndAllowsFurtherWork() throws {
        let activity = KeyboardDeviceActivity()
        var prompts = 0
        let termination = KeyboardTermination(
            activity: activity,
            confirmDiscard: {
                prompts += 1
                return false
            })
        XCTAssertEqual(termination.request { _ in XCTFail("Quit was cancelled") }, .terminateCancel)
        XCTAssertEqual(prompts, 1)
        XCTAssertFalse(activity.isQuitting)
        try activity.begin()
        activity.finish()
    }

    @MainActor func testDiscardAllowsQuit() {
        let activity = KeyboardDeviceActivity()
        let termination = KeyboardTermination(activity: activity, confirmDiscard: { true })
        XCTAssertEqual(termination.request { _ in XCTFail("Quit should be immediate") }, .terminateNow)
        XCTAssertTrue(activity.isQuitting)
    }

    @MainActor func testDeferredQuitChecksDraftAfterOperationAndCanBeCancelled() async throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        var prompts = 0
        var replies: [Bool] = []
        let termination = KeyboardTermination(
            activity: activity,
            confirmDiscard: {
                prompts += 1
                return false
            })
        XCTAssertEqual(termination.request { replies.append($0) }, .terminateLater)
        XCTAssertEqual(prompts, 0)
        activity.finish()
        try await waitForCondition { replies == [false] }

        XCTAssertEqual(prompts, 1)
        XCTAssertFalse(activity.isQuitting)
        try activity.begin()
        activity.finish()
    }

    @MainActor func testCloseKeepsDraftUntilDiscardAndBlocksDuringDeviceOperation() throws {
        let activity = KeyboardDeviceActivity()
        let confirmation = CloseConfirmation()
        let view = KeyboardCloseGuardView(
            activity: activity, hasChanges: { confirmation.dirty },
            confirmClose: {
                confirmation.prompts += 1
                return confirmation.discard
            })
        let window = NSWindow()
        window.isReleasedWhenClosed = false
        XCTAssertTrue(view.windowShouldClose(window))
        XCTAssertEqual(confirmation.prompts, 0)
        confirmation.dirty = true
        XCTAssertFalse(view.windowShouldClose(window))
        XCTAssertTrue(confirmation.dirty)
        confirmation.discard = true
        XCTAssertTrue(view.windowShouldClose(window))
        XCTAssertEqual(confirmation.prompts, 2)
        try activity.begin()
        XCTAssertFalse(view.windowShouldClose(window))
        XCTAssertEqual(confirmation.prompts, 2)
        activity.finish()
    }

    @MainActor func testCloseGuardInstallsAndRestoresExistingWindowDelegate() {
        let activity = KeyboardDeviceActivity()
        let window = NSWindow()
        window.isReleasedWhenClosed = false
        let original = TestWindowDelegate()
        window.delegate = original
        let view = KeyboardCloseGuardView(activity: activity, hasChanges: { false })
        window.contentView = view
        XCTAssertTrue(window.delegate === view)
        XCTAssertFalse(view.windowShouldClose(window))
        XCTAssertEqual(original.closeRequests, 1)
        XCTAssertTrue(view.responds(to: #selector(NSWindowDelegate.windowDidBecomeMain(_:))))
        view.windowDidBecomeKey(Notification(name: NSWindow.didBecomeKeyNotification, object: window))
        view.windowWillClose(Notification(name: NSWindow.willCloseNotification, object: window))
        XCTAssertEqual(original.keyNotifications, 1)
        XCTAssertEqual(original.closeNotifications, 1)
        view.removeDelegate()
        XCTAssertTrue(window.delegate === original)
    }

    @MainActor func testIdleQuitBlocksFreshWorkWhileConfirmingAndRejectsNestedQuit() throws {
        let activity = KeyboardDeviceActivity()
        var prompts = 0
        var termination: KeyboardTermination!
        termination = KeyboardTermination(
            activity: activity,
            confirmDiscard: {
                prompts += 1
                XCTAssertTrue(activity.isQuitting)
                do {
                    try activity.begin()
                    activity.finish()
                    XCTFail("Fresh device work started during quit confirmation")
                } catch {}

                do {
                    let owner = try activity.reserve()
                    activity.release(owner)
                    XCTFail("Fresh reservation started during quit confirmation")
                } catch {}

                if prompts == 1 {
                    XCTAssertEqual(
                        termination.request { _ in XCTFail("Nested quit must not be deferred") }, .terminateCancel)
                }

                return false
            })
        defer { termination = nil }

        XCTAssertEqual(termination.request { _ in XCTFail("Quit was cancelled") }, .terminateCancel)
        XCTAssertEqual(prompts, 1)
        XCTAssertFalse(activity.isQuitting)
        try activity.begin()
        activity.finish()
    }

    @MainActor func testDeferredQuitKeepsOriginalRequestPendingThroughoutConfirmation() async throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        var termination: KeyboardTermination!
        var prompts = 0
        var replies: [Bool] = []
        termination = KeyboardTermination(
            activity: activity,
            confirmDiscard: {
                prompts += 1
                XCTAssertTrue(activity.isQuitting)
                if prompts == 1 {
                    XCTAssertEqual(
                        termination.request { _ in XCTFail("The original quit reply owns completion") }, .terminateLater
                    )
                }

                return false
            })
        defer { termination = nil }

        XCTAssertEqual(termination.request { replies.append($0) }, .terminateLater)
        activity.finish()
        try await waitForCondition { replies == [false] }

        XCTAssertEqual(prompts, 1)
        XCTAssertFalse(activity.isQuitting)
    }

    @MainActor func testCloseRechecksDeviceActivityAfterModalConfirmation() throws {
        let activity = KeyboardDeviceActivity()
        let view = KeyboardCloseGuardView(
            activity: activity, hasChanges: { true },
            confirmClose: {
                do {
                    try activity.begin()
                    return true
                } catch {
                    XCTFail("Could not simulate queued device work: \(error)")
                    return false
                }
            })
        let window = NSWindow()
        window.isReleasedWhenClosed = false
        XCTAssertFalse(view.windowShouldClose(window))
        activity.finish()
    }

    @MainActor func testDetachingGuardRestoresOriginalWindowDelegate() {
        let window = NSWindow()
        window.isReleasedWhenClosed = false
        let original = TestWindowDelegate()
        window.delegate = original
        let view = KeyboardCloseGuardView(activity: KeyboardDeviceActivity(), hasChanges: { false })
        window.contentView = view
        XCTAssertTrue(window.delegate === view)
        window.contentView = NSView()
        XCTAssertTrue(window.delegate === original)
    }

    @MainActor func testHiddenDraftStillPromptsButClosedDraftDoesNot() {
        let window = NSWindow()
        window.isReleasedWhenClosed = false
        defer { window.close() }

        var prompts = 0
        let protection = KeyboardDraftProtection(confirmDiscard: {
            prompts += 1
            return false
        })
        let view = KeyboardCloseGuardView(
            activity: KeyboardDeviceActivity(), hasChanges: { true }, protection: protection)
        window.contentView = view
        window.orderFront(nil)
        window.orderOut(nil)
        XCTAssertFalse(window.isVisible)
        XCTAssertFalse(protection.confirmQuit())
        XCTAssertEqual(prompts, 1)
        window.close()
        XCTAssertTrue(protection.confirmQuit())
        XCTAssertEqual(prompts, 1)
        window.makeKeyAndOrderFront(nil)
        XCTAssertFalse(protection.confirmQuit())
        XCTAssertEqual(prompts, 2)
    }

    @MainActor func testCloseDoesNotOpenNestedConfirmation() {
        let window = NSWindow()
        window.isReleasedWhenClosed = false
        var view: KeyboardCloseGuardView!
        var prompts = 0
        view = KeyboardCloseGuardView(
            activity: KeyboardDeviceActivity(), hasChanges: { true },
            confirmClose: {
                prompts += 1
                if prompts == 1 { XCTAssertFalse(view.windowShouldClose(window)) }

                return false
            })
        defer { view = nil }

        XCTAssertFalse(view.windowShouldClose(window))
        XCTAssertEqual(prompts, 1)
    }

    @MainActor func testIdlePermissionSetupAllowsImmediateSystemQuitAndRejectsFreshWork() {
        let activity = KeyboardDeviceActivity()
        let termination = KeyboardTermination(activity: activity)
        var replies: [Bool] = []
        XCTAssertEqual(termination.request { replies.append($0) }, .terminateNow)
        XCTAssertTrue(activity.isQuitting)
        XCTAssertTrue(replies.isEmpty)
        XCTAssertThrowsError(try activity.begin())
        XCTAssertThrowsError(try activity.reserve())
    }

    @MainActor func testBusyOperationDefersQuitWithoutCancellingAndRepliesOnceWhenFinished() async throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        let termination = KeyboardTermination(activity: activity)
        var replies: [Bool] = []
        XCTAssertEqual(termination.request { replies.append($0) }, .terminateLater)
        XCTAssertEqual(termination.request { _ in XCTFail("Quit was already pending") }, .terminateLater)
        XCTAssertTrue(activity.isBusy)
        XCTAssertTrue(replies.isEmpty)
        activity.finish()
        XCTAssertEqual(
            termination.request { _ in XCTFail("The original quit reply must finish first") }, .terminateLater)
        try await waitForCondition { replies == [true] }

        XCTAssertThrowsError(try activity.begin())
        XCTAssertThrowsError(try activity.reserve())
        activity.finish()
        await Task.yield()
        XCTAssertEqual(replies, [true])
    }

    @MainActor func testDeferredQuitRepliesWhileOnlyAppKitTerminationModeIsRunning() throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        let termination = KeyboardTermination(activity: activity)
        var replies: [Bool] = []
        XCTAssertEqual(termination.request { replies.append($0) }, .terminateLater)
        activity.finish()

        // AppKit switches to this mode after terminateLater. A queued main-actor
        // task must not be the only way to acknowledge the completed operation.
        let deadline = Date().addingTimeInterval(0.25)
        while replies.isEmpty && Date() < deadline { _ = RunLoop.main.run(mode: .modalPanel, before: deadline) }

        XCTAssertEqual(replies, [true])
    }

    @MainActor func testExistingReservedUpdateCanFinishBeforeSystemQuit() async throws {
        let activity = KeyboardDeviceActivity()
        let owner = try activity.reserve()
        try activity.begin(owner: owner)
        let termination = KeyboardTermination(activity: activity)
        var replies: [Bool] = []
        XCTAssertEqual(termination.request { replies.append($0) }, .terminateLater)
        activity.finish()
        await Task.yield()
        XCTAssertTrue(replies.isEmpty)
        XCTAssertTrue(activity.isBusy)
        XCTAssertThrowsError(try activity.begin())
        try activity.begin(owner: owner)
        activity.finish()
        activity.release(owner)
        try await waitForCondition { replies == [true] }
    }
}

@MainActor private final class TestWindowDelegate: NSObject, NSWindowDelegate {
    var closeRequests = 0
    var keyNotifications = 0
    var closeNotifications = 0

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        closeRequests += 1
        return false
    }

    func windowDidBecomeMain(_ notification: Notification) {}

    func windowDidBecomeKey(_ notification: Notification) { keyNotifications += 1 }

    func windowWillClose(_ notification: Notification) { closeNotifications += 1 }
}

@MainActor private final class CloseConfirmation {
    var dirty = false
    var discard = false
    var prompts = 0
}
