import XCTest

@testable import KeyboardLights

final class KeyboardSessionTests: XCTestCase {
    @MainActor private func fixture(
        connected: Bool = true, access: KeyboardPermission.Access = .granted, activity: KeyboardDeviceActivity? = nil,
        lightingGate: SessionReadGate? = nil, connection: KeyboardConnection? = nil
    ) -> SessionFixture {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }

        return SessionFixture(
            root: root, connected: connected, access: access, activity: activity, lightingGate: lightingGate,
            connection: connection)
    }

    @MainActor func testStartupWaitsForApprovalAndResumesWithoutDuplicateReads() async throws {
        let rig = fixture(access: .unknown)
        await rig.session.start()
        var events = await rig.log.events
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(rig.permission.requests, 1)
        rig.permission.access = .granted
        rig.session.applicationBecameActive()
        try await waitForCondition {
            let events = await rig.log.events
            return events == ["lighting", "assignments"] && rig.session.assignmentWorkflow.snapshot != nil
        }

        await rig.session.start()
        rig.session.applicationBecameActive()
        events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
        XCTAssertEqual(rig.permission.requests, 1)
    }

    @MainActor func testQuittingDuringPermissionSetupDoesNotStartReadsAfterApproval() async throws {
        let rig = fixture(access: .denied)
        await rig.session.start()
        XCTAssertEqual(rig.permission.requests, 1)
        rig.session.activity.prepareToQuit()
        rig.permission.access = .granted
        rig.session.applicationBecameActive()
        await rig.session.refresh(assignments: true)
        await Task.yield()
        let events = await rig.log.events
        XCTAssertTrue(events.isEmpty)
        XCTAssertFalse(rig.session.activity.isBusy)
    }

    @MainActor func testQuittingWhileDetectionIsPendingDoesNotRequestPermission() async throws {
        let probe = BlockingDetection()
        defer { probe.release.signal() }

        let connection = KeyboardConnection(detect: { try probe.detect() })
        let rig = fixture(access: .denied, connection: connection)
        let startup = Task { await rig.session.start() }

        try await waitForCondition { probe.hasStarted }

        rig.session.activity.prepareToQuit()
        probe.release.signal()
        await startup.value
        XCTAssertEqual(rig.permission.requests, 0)
        let events = await rig.log.events
        XCTAssertTrue(events.isEmpty)
    }

    @MainActor func testQuittingDuringInitialReadDoesNotStartFollowupAssignmentRead() async throws {
        let gate = SessionReadGate()
        let rig = fixture(lightingGate: gate)
        let startup = Task { await rig.session.start() }

        defer {
            startup.cancel()
            Task { await gate.release() }
        }

        try await waitForCondition { await rig.log.events == ["lighting"] }

        rig.session.activity.prepareToQuit()
        await gate.release()
        await startup.value
        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting"])
        XCTAssertFalse(rig.session.activity.isBusy)
    }

    @MainActor func testCancellingQuitResumesStartupAfterApprovalArrivesDuringConfirmation() async throws {
        let rig = fixture(access: .unknown)
        await rig.session.start()
        rig.session.activity.prepareToQuit()
        rig.permission.access = .granted
        rig.session.applicationBecameActive()
        await Task.yield()
        let paused = await rig.log.events
        XCTAssertTrue(paused.isEmpty)
        rig.session.activity.cancelQuit()
        try await waitForCondition {
            await rig.log.events == ["lighting", "assignments"] && rig.session.assignmentWorkflow.snapshot != nil
        }
    }

    @MainActor func testCancellingQuitResumesOnlyRemainingInitialReadAndPreservesPreview() async throws {
        let gate = SessionReadGate()
        let rig = fixture(lightingGate: gate)
        let startup = Task { await rig.session.start() }

        defer {
            startup.cancel()
            Task { await gate.release() }
        }

        try await waitForCondition { await rig.log.events == ["lighting"] }

        rig.session.activity.prepareToQuit()
        await gate.release()
        await startup.value
        rig.session.state.brightness = 15
        rig.session.state.draftChanged()
        let draft = try rig.session.state.savedLightingDraft()
        rig.session.activity.cancelQuit()
        await rig.session.start()
        try await waitForCondition { rig.session.assignmentWorkflow.snapshot != nil }

        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
        XCTAssertEqual(try rig.session.state.savedLightingDraft(), draft)
        XCTAssertTrue(rig.session.state.hasUnsavedLightingChanges)
    }

    @MainActor func testCancellingQuitLetsWaitingWindowResumeItsInitialReads() async throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        let rig = fixture(activity: activity)
        await rig.session.start()
        activity.prepareToQuit()
        activity.finish()
        await Task.yield()
        activity.cancelQuit()
        try await waitForCondition {
            await rig.log.events == ["lighting", "assignments"] && rig.session.assignmentWorkflow.snapshot != nil
        }
    }

    @MainActor func testMissingKeyboardSkipsPermissionAndRefreshReconnects() async throws {
        let rig = fixture(connected: false)
        await rig.session.start()
        var events = await rig.log.events
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(rig.permission.requests, 0)
        XCTAssertEqual(rig.session.connection.message, "Keyboard not connected")
        rig.probe.setConnected(true)
        await rig.session.refresh(assignments: false)
        events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
        rig.probe.setConnected(false)
        await rig.session.refresh(assignments: true)
        XCTAssertNil(rig.session.assignmentWorkflow.snapshot)
        events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
    }

    @MainActor func testConfirmedLightingRefreshRechecksConnectionWithoutDiscardingDraftWhenUnplugged() async throws {
        let rig = fixture()
        await rig.session.start()
        rig.session.state.brightness = 15
        rig.session.state.prepareReview()
        let review = try XCTUnwrap(rig.session.state.review)
        let draft = try rig.session.state.savedLightingDraft()
        await rig.session.refresh(assignments: false)
        XCTAssertTrue(rig.session.state.confirmsLightingDiscard)
        rig.probe.setConnected(false)
        await rig.session.refresh(assignments: false, discardingLightingEdits: true)
        XCTAssertEqual(rig.session.connection.state, .disconnected)
        XCTAssertFalse(rig.session.state.confirmsLightingDiscard)
        XCTAssertEqual(try rig.session.state.savedLightingDraft(), draft)
        XCTAssertEqual(rig.session.state.review?.id, review.id)
        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
    }

    @MainActor func testConfirmedLightingRefreshRechecksPermissionAndCanResumeAfterApproval() async throws {
        let rig = fixture()
        await rig.session.start()
        rig.session.state.brightness = 15
        let draft = try rig.session.state.savedLightingDraft()
        await rig.session.refresh(assignments: false)
        XCTAssertTrue(rig.session.state.confirmsLightingDiscard)
        rig.permission.access = .denied
        await rig.session.refresh(assignments: false, discardingLightingEdits: true)
        XCTAssertFalse(rig.session.permission.isGranted)
        XCTAssertFalse(rig.session.state.confirmsLightingDiscard)
        XCTAssertEqual(try rig.session.state.savedLightingDraft(), draft)
        var events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
        rig.permission.access = .granted
        await rig.session.refresh(assignments: false, discardingLightingEdits: true)
        XCTAssertTrue(rig.session.permission.isGranted)
        XCTAssertFalse(rig.session.state.hasUnsavedLightingChanges)
        events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments", "lighting"])
    }

    @MainActor func testReturningToAssignmentsLoadsOnlyWhenItsSnapshotIsMissing() async throws {
        let rig = fixture()
        await rig.session.start()
        await rig.session.loadAssignmentsIfNeeded()
        var events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
        rig.session.assignmentWorkflow.invalidateIfKeyboardChanged(nil)
        await rig.session.loadAssignmentsIfNeeded()
        events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments", "assignments"])
        XCTAssertNotNil(rig.session.assignmentWorkflow.snapshot)
    }

    @MainActor func testApprovalAfterUnpluggingDoesNotReadTheKeyboard() async throws {
        let rig = fixture(access: .unknown)
        await rig.session.start()
        rig.probe.setConnected(false)
        rig.permission.access = .granted
        rig.session.applicationBecameActive()
        try await waitForCondition { rig.session.connection.state == .disconnected }

        let events = await rig.log.events
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(rig.permission.requests, 1)
    }

    @MainActor func testWaitingWindowRechecksConnectionBeforeItsDeferredRead() async throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        let rig = fixture(activity: activity)
        await rig.session.start()
        rig.probe.setConnected(false)
        activity.finish()
        try await waitForCondition { rig.session.connection.state == .disconnected }

        let events = await rig.log.events
        XCTAssertTrue(events.isEmpty)
    }

    @MainActor func testCancelledInitialReadCanRestartWithoutBackgroundRetry() async throws {
        let gate = SessionReadGate()
        let rig = fixture(lightingGate: gate)
        let startup = Task { await rig.session.start() }

        defer {
            startup.cancel()
            Task { await gate.release() }
        }

        try await waitForCondition { await rig.log.events == ["lighting"] }

        startup.cancel()
        await gate.release()
        await startup.value
        XCTAssertNil(rig.session.state.snapshot)
        var events = await rig.log.events
        XCTAssertEqual(events, ["lighting"])
        await rig.session.start()
        events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "lighting", "assignments"])
        XCTAssertNotNil(rig.session.state.snapshot)
        XCTAssertNotNil(rig.session.assignmentWorkflow.snapshot)
    }

    @MainActor func testSecondWindowResumesStartupAfterFirstWindowFinishes() async throws {
        let activity = KeyboardDeviceActivity()
        let gate = SessionReadGate()
        let first = fixture(activity: activity, lightingGate: gate)
        let second = fixture(activity: activity)
        let startup = Task { await first.session.start() }

        defer {
            startup.cancel()
            Task { await gate.release() }
        }

        try await waitForCondition { await first.log.events == ["lighting"] }

        await second.session.start()
        let waiting = await second.log.events
        XCTAssertTrue(waiting.isEmpty)
        await gate.release()
        await startup.value
        try await waitForCondition {
            let events = await second.log.events
            return events == ["lighting", "assignments"] && second.session.assignmentWorkflow.snapshot != nil
        }

        XCTAssertNotNil(second.session.state.snapshot)
        XCTAssertNotNil(second.session.assignmentWorkflow.snapshot)
    }
}
