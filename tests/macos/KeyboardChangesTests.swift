import XCTest

@testable import KeyboardLights

final class KeyboardChangesTests: XCTestCase {
    @MainActor private func fixture(verificationGate: SessionReadGate? = nil, failsVerification: Bool = false)
        async throws -> (KeyboardChangesWorkflow, KeyboardState, AssignmentWorkflow, KeyAssignmentEditor, ChangesStore)
    {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }

        let activity = KeyboardDeviceActivity()
        let store = ChangesStore()
        let state = KeyboardState(
            activity: activity, service: ChangesLightingService(store: store), root: root, checkAccess: { true })
        let assignments = AssignmentWorkflow(
            service: ChangesAssignmentService(
                store: store, verificationGate: verificationGate, failsVerification: failsVerification), root: root,
            activity: activity, checkAccess: { true })
        await state.read()
        await assignments.read()
        let editor = KeyAssignmentEditor()
        state.brightness = 8
        state.draftChanged()
        try editor.set(KeyAction(kind: .disabled), for: 39)
        let changes = KeyboardChangesWorkflow(
            state: state, assignments: assignments, editor: editor, activity: activity)
        return (changes, state, assignments, editor, store)
    }

    @MainActor func testPendingPreviewChangesTrackBothEditorsAndClearAfterApplying() async throws {
        let (changes, state, _, editor, _) = try await fixture()
        XCTAssertTrue(changes.hasPendingChanges)
        state.resetLighting()
        XCTAssertTrue(changes.hasPendingChanges)
        editor.clear()
        XCTAssertFalse(changes.hasPendingChanges)
        state.brightness = 8
        state.draftChanged()
        XCTAssertTrue(changes.hasPendingChanges)
        changes.prepare()
        await changes.apply(try XCTUnwrap(changes.review))
        XCTAssertFalse(changes.hasPendingChanges)
    }

    @MainActor func testReservationKeepsOtherWindowsOutBetweenVerifiedSteps() throws {
        let activity = KeyboardDeviceActivity()
        let owner = try activity.reserve()
        XCTAssertTrue(activity.isBusy)
        XCTAssertThrowsError(try activity.begin())
        try activity.begin(owner: owner)
        activity.finish()
        XCTAssertTrue(activity.isBusy)
        XCTAssertThrowsError(try activity.begin())
        try activity.begin(owner: owner)
        activity.finish()
        activity.release(owner)
        XCTAssertFalse(activity.isBusy)
        XCTAssertThrowsError(try activity.begin(owner: owner))
        try activity.begin()
        activity.finish()
    }

    @MainActor func testOneReviewAppliesBothChangesAndRefreshesVerifiedDetails() async throws {
        let (changes, state, assignments, editor, store) = try await fixture()
        changes.prepare()
        let review = try XCTUnwrap(changes.review)
        XCTAssertNotNil(review.lighting)
        XCTAssertNotNil(review.assignments)
        await changes.apply(review)
        let events = await store.events
        XCTAssertEqual(events, ["assignments", "lighting"])
        XCTAssertFalse(state.hasLightingChangesToApply)
        XCTAssertTrue(assignments.draftMatchesKeyboard(editor.draft))
        XCTAssertNil(changes.error)
        XCTAssertNil(changes.review)
        XCTAssertFalse(changes.isApplying)
    }

    @MainActor func testApplyingDoesNotRecaptureUnrelatedChannels() async throws {
        for lightingOnly in [false, true] {
            let (changes, _, _, editor, store) = try await fixture(failsVerification: true)
            if lightingOnly { editor.clear() }

            changes.prepare()
            await changes.apply(try XCTUnwrap(changes.review))
            let reads = await store.assignmentReads
            XCTAssertEqual(reads, 1)
            XCTAssertNil(changes.error)
            XCTAssertNil(changes.review)
        }
    }

    @MainActor func testFirstFailureDoesNotStartLightingOrDiscardItsDraft() async throws {
        let (changes, state, _, _, store) = try await fixture()
        await store.fail(assignments: true)
        let draft = try state.savedLightingDraft()
        changes.prepare()
        await changes.apply(try XCTUnwrap(changes.review))
        let events = await store.events
        XCTAssertEqual(events, ["assignments"])
        XCTAssertEqual(try state.savedLightingDraft(), draft)
        XCTAssertTrue(changes.error?.contains("Lighting was not applied") == true)
        XCTAssertNil(changes.review)
    }

    @MainActor func testSecondFailureReportsPartialCompletionWithoutRetrying() async throws {
        let (changes, _, _, _, store) = try await fixture()
        await store.fail(assignments: false)
        changes.prepare()
        let review = try XCTUnwrap(changes.review)
        await changes.apply(review)
        await changes.apply(review)
        let events = await store.events
        XCTAssertEqual(events, ["assignments", "lighting"])
        XCTAssertTrue(changes.error?.contains("Assignments applied; lighting did not finish") == true)
        let capture = await store.capture()
        XCTAssertEqual(Array(capture.channels[0][156..<160]), [5, 3, 0, 0])
    }

    @MainActor func testDeferredObservationKeepsAnUnchangedReviewAndClearsStaleReviews() async throws {
        let (changes, state, _, editor, _) = try await fixture()
        changes.prepare()
        let review = try XCTUnwrap(changes.review)
        changes.invalidateReview()
        XCTAssertEqual(changes.review?.id, review.id)
        state.review = nil
        changes.invalidateReview()
        XCTAssertNil(changes.review)
        changes.prepare()
        XCTAssertNotNil(changes.review)
        try editor.set(KeyAction(kind: .factoryDefault), for: 39)
        changes.invalidateReview()
        XCTAssertNil(changes.review)
    }

    @MainActor func testChangedDraftStopsBeforeAnyWriterIsCalled() async throws {
        let (changes, _, _, editor, store) = try await fixture()
        changes.prepare()
        let review = try XCTUnwrap(changes.review)
        try editor.set(KeyAction(kind: .factoryDefault), for: 39)
        await changes.apply(review)
        let events = await store.events
        XCTAssertTrue(events.isEmpty)
        XCTAssertTrue(changes.error?.contains("draft changed") == true)
        XCTAssertNil(changes.review)
    }

    @MainActor func testLightingDoesNotReadOrVerifyOtherChannels() async throws {
        let (changes, _, _, editor, store) = try await fixture()
        editor.clear()
        await store.corruptAfterLighting(channel: 2)
        changes.prepare()
        await changes.apply(try XCTUnwrap(changes.review))
        let reads = await store.assignmentReads
        XCTAssertEqual(reads, 1)
        XCTAssertNil(changes.error)
    }

    @MainActor func testLightingOnlyReviewDoesNotWriteAssignments() async throws {
        let (changes, _, _, editor, store) = try await fixture()
        editor.clear()
        changes.prepare()
        let review = try XCTUnwrap(changes.review)
        XCTAssertNil(review.assignments)
        await changes.apply(review)
        let events = await store.events
        XCTAssertEqual(events, ["lighting"])
        XCTAssertNil(changes.error)
    }

    @MainActor func testAssignmentOnlyReviewDoesNotWriteLighting() async throws {
        let (changes, state, _, _, store) = try await fixture()
        state.resetLighting()
        changes.prepare()
        let review = try XCTUnwrap(changes.review)
        XCTAssertNil(review.lighting)
        await changes.apply(review)
        let events = await store.events
        XCTAssertEqual(events, ["assignments"])
    }
}

private actor ChangesStore {
    var channels = [
        Data(repeating: 0, count: 640), makeTestLightingImage(), Data(repeating: 0, count: 3072),
        Data(repeating: 0, count: 64)
    ]
    private(set) var events: [String] = []
    private var failure: String?
    private var corruptedChannel: Int?

    func corruptAfterLighting(channel: Int = 0) { corruptedChannel = channel }

    func fail(assignments: Bool) { failure = assignments ? "assignments" : "lighting" }

    private(set) var assignmentReads = 0

    func capture() -> AssignmentSnapshot {
        assignmentReads += 1
        return AssignmentSnapshot(location: 0x0114_0000, channels: [channels[0]])
    }

    func lighting() -> DeviceSnapshot { DeviceSnapshot(location: "01140000", lightingData: channels[1]) }

    func write(channel: Int, candidate: Data) throws -> AssignmentSnapshot {
        let name = channel == 0 ? "assignments" : "lighting"
        events.append(name)
        if failure == name { throw WorkflowError.message("Injected write failure") }

        channels[channel] = candidate
        if channel == 1, let corruptedChannel {
            channels[corruptedChannel][17 * 4 % channels[corruptedChannel].count] = 5
        }

        return AssignmentSnapshot(location: 0x0114_0000, channels: [channels[0]])
    }
}

private struct ChangesLightingService: LightingDeviceService {
    let store: ChangesStore
    func capture(output: URL) async throws -> DeviceSnapshot { await store.lighting() }

    func write(snapshot: DeviceSnapshot, candidate: Data, output: URL) async throws -> DeviceSnapshot {
        _ = try await store.write(channel: 1, candidate: candidate)
        return await store.lighting()
    }
}

private struct ChangesAssignmentService: AssignmentDeviceService {
    let store: ChangesStore
    var verificationGate: SessionReadGate? = nil
    var failsVerification = false

    func capture(output: URL) async throws -> AssignmentSnapshot {
        let capture = await store.capture()
        if await store.events.contains("lighting") {
            if let verificationGate { await verificationGate.wait() }

            if failsVerification { throw WorkflowError.message("Injected verification read failure") }
        }

        return capture
    }

    func write(snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL) async throws
        -> AssignmentSnapshot
    { try await store.write(channel: 0, candidate: candidate) }
}
