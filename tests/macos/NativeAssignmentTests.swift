import C989Native
import XCTest

@testable import KeyboardLights

final class NativeAssignmentTests: XCTestCase {
    private func image(_ marker: Bool = false) -> Data {
        var data = Data(repeating: 0, count: 640)
        if marker {
            data[638] = 0xAA
            data[639] = 0x55
        }

        return data
    }

    func testEveryNativeOutputMatchesEditorAndPreservesReservedBytes() throws {
        var baseline = image(true)
        baseline[0] = 0x71  // Opaque reserved matrix slot.
        baseline[600] = 0x73  // Opaque non-key metadata.
        for kind in KeyActionKind.allCases {
            let actions =
                kind.targets.isEmpty
                ? [KeyAction(kind: kind)]
                : kind.targets.map { KeyAction(kind: kind, target: $0.id, modifiers: kind == .keyboard ? 0xA5 : 0) }

            for action in actions {
                var before = baseline
                before[360] = 2
                before[362] = 5
                let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: action)])
                let result = try NativeAssignmentPolicy.candidate(baseline: before, draft: draft)
                XCTAssertEqual(result, try draft.candidate(from: before))
                XCTAssertEqual(result[0], 0x71)
                XCTAssertEqual(result[600], 0x73)
            }
        }
    }

    func testEveryPhysicalSourceAcceptedAndInvalidRequestsRejected() throws {
        for source in LegacyKey.physical {
            let draft = KeyAssignmentDraft(assignments: [
                KeyAssignment(source: source.index, action: KeyAction(kind: .disabled))
            ])
            XCTAssertEqual(
                try NativeAssignmentPolicy.candidate(baseline: image(), draft: draft),
                try draft.candidate(from: image()))
        }

        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        XCTAssertThrowsError(try NativeAssignmentPolicy.candidate(baseline: Data(), draft: draft))
        XCTAssertThrowsError(try NativeAssignmentPolicy.candidate(baseline: image(), draft: KeyAssignmentDraft()))
        var inactive = image()
        inactive[4] = 2
        XCTAssertThrowsError(try NativeAssignmentPolicy.candidate(baseline: inactive, draft: draft))
        XCTAssertThrowsError(try draft.candidate(from: inactive))
        let already = try draft.candidate(from: image())
        XCTAssertThrowsError(try NativeAssignmentPolicy.candidate(baseline: already, draft: draft))
    }

    func testCPolicyIndependentlyRejectsRawUnsafePatches() {
        func prepare(_ records: [C989AssignmentPatch]) -> Int32 {
            let baseline = image()
            var output = [UInt8](repeating: 0, count: 640)
            var error = [CChar](repeating: 0, count: 256)
            return baseline.withUnsafeBytes { bytes in
                records.withUnsafeBufferPointer { patches in
                    c989_assignments_prepare(
                        bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count, patches.baseAddress, patches.count,
                        &output, output.count, &error, error.count)
                }
            }
        }

        let valid = C989AssignmentPatch(source: 90, record: (2, 8, 6, 0))
        XCTAssertEqual(prepare([valid]), 0)
        for record in [(UInt8(4), UInt8(1), UInt8(0), UInt8(0)), (2, 0, 255, 0), (2, 0, 4, 1), (1, 1, 3, 0)] {
            XCTAssertNotEqual(prepare([C989AssignmentPatch(source: 90, record: record)]), 0)
        }

        XCTAssertNotEqual(prepare([C989AssignmentPatch(source: 0, record: (2, 0, 4, 0))]), 0)
        XCTAssertNotEqual(prepare([valid, valid]), 0)
    }

    func testMalformedNativeWriteIsRejectedBeforeEvidenceOrDeviceAccess() throws {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        XCTAssertThrowsError(
            try NativeKeyboardBackend().writeAssignments(
                snapshot: AssignmentSnapshot(location: 0, channels: []), draft: KeyAssignmentDraft(), candidate: Data(),
                output: path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }

    func testLockReadingFreshnessDoesNotChangeStateEquality() {
        let first = LockIndicators(rawValue: 5)
        let second = LockIndicators(rawValue: 5)
        XCTAssertEqual(first, second)
        XCTAssertEqual(LockIndicators.latest(first, second)?.sampledAt, second.sampledAt)
        XCTAssertEqual(LockIndicators.latest(second, first)?.sampledAt, second.sampledAt)
        XCTAssertEqual(LockIndicators.latest(first, nil)?.sampledAt, first.sampledAt)
        XCTAssertEqual(LockIndicators.latest(nil, second)?.sampledAt, second.sampledAt)
        XCTAssertNil(LockIndicators.latest(nil, nil))
    }

    func testSnapshotOwnsNormalizedChannelsAndRejectsIncompleteState() throws {
        var channels = [Data(repeating: 1, count: 640)]
        let original = channels
        let snapshot = try AssignmentSnapshot(location: 0x0114_0000, channels: channels).validated()
        channels[0][0] = 255
        XCTAssertEqual(snapshot.channels, original)

        let slices = original.map { (Data([255]) + $0).dropFirst() }

        let normalized = try AssignmentSnapshot(location: 1, channels: slices).validated()
        XCTAssertEqual(normalized.channels, original)
        XCTAssertTrue(normalized.channels.allSatisfy { $0.startIndex == 0 })
        XCTAssertThrowsError(try AssignmentSnapshot(location: 0, channels: original).validated())
        XCTAssertThrowsError(try AssignmentSnapshot(location: 1, channels: Array(original.dropLast())).validated())
        channels[0] = Data(channels[0].dropLast())
        XCTAssertThrowsError(try AssignmentSnapshot(location: 1, channels: channels).validated())
        channels = original + [makeTestLightingImage()]
        XCTAssertThrowsError(try AssignmentSnapshot(location: 1, channels: channels).validated())
    }

}

private actor MockAssignmentService: AssignmentDeviceService {
    let initial: AssignmentSnapshot
    let writeError: (any Error)?
    var writes = 0
    let captureOverride: AssignmentSnapshot?
    let resultOverride: AssignmentSnapshot?
    init(writeError: (any Error)? = nil, capture: AssignmentSnapshot? = nil, result: AssignmentSnapshot? = nil) {
        captureOverride = capture
        resultOverride = result
        self.writeError = writeError
        initial = AssignmentSnapshot(location: 0x0114_0000, channels: [Data(repeating: 0, count: 640)])
    }

    func capture(output: URL) async throws -> AssignmentSnapshot { captureOverride ?? initial }

    func write(snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL) async throws
        -> AssignmentSnapshot
    {
        writes += 1
        if let writeError { throw writeError }

        if let resultOverride { return resultOverride }

        var channels = snapshot.channels
        channels[0] = candidate
        return AssignmentSnapshot(location: snapshot.location, channels: channels)
    }
}

final class AssignmentWorkflowTests: XCTestCase {
    @MainActor func testLightingChangesDoNotInvalidateAssignmentChannel() async throws {
        let workflow = AssignmentWorkflow(
            service: MockAssignmentService(), root: FileManager.default.temporaryDirectory,
            activity: KeyboardDeviceActivity(), checkAccess: { true })
        await workflow.readIfNeeded()
        let original = try XCTUnwrap(workflow.snapshot)
        workflow.prepare(
            KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))]))
        let reviewID = try XCTUnwrap(workflow.review).id
        await workflow.readIfNeeded()
        XCTAssertEqual(workflow.review?.id, reviewID)
        workflow.invalidateIfKeyboardChanged(
            DeviceSnapshot(location: "01140000", lightingData: makeTestLightingImage()))
        XCTAssertNotNil(workflow.snapshot)
        var changed = makeTestLightingImage()
        changed[0x449] = changed[0x449] == 16 ? 15 : 16
        workflow.invalidateIfKeyboardChanged(DeviceSnapshot(location: "01140000", lightingData: changed))
        XCTAssertNotNil(workflow.snapshot)
        XCTAssertEqual(workflow.review?.id, reviewID)
        await workflow.readIfNeeded()
        XCTAssertNotNil(workflow.snapshot)
    }

    @MainActor func testReconnectAndLostLightingStateInvalidateAssignmentReview() async throws {
        let workflow = AssignmentWorkflow(
            service: MockAssignmentService(), root: FileManager.default.temporaryDirectory,
            activity: KeyboardDeviceActivity(), checkAccess: { true })
        await workflow.read()
        let original = try XCTUnwrap(workflow.snapshot)
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        workflow.prepare(draft)
        XCTAssertNotNil(workflow.review)
        workflow.invalidateIfKeyboardChanged(
            DeviceSnapshot(location: "01150000", lightingData: makeTestLightingImage()))
        XCTAssertNil(workflow.snapshot)
        XCTAssertNil(workflow.review)
        await workflow.readIfNeeded()
        XCTAssertNotNil(workflow.snapshot)
        workflow.prepare(draft)
        workflow.invalidateIfKeyboardChanged(nil)
        XCTAssertNil(workflow.snapshot)
        XCTAssertNil(workflow.review)
    }

    @MainActor func testBackgroundAssignmentReadWaitsForOtherWorkflowWithoutShowingAnError() async throws {
        let activity = KeyboardDeviceActivity()
        let workflow = AssignmentWorkflow(
            service: MockAssignmentService(), root: FileManager.default.temporaryDirectory, activity: activity,
            checkAccess: { true })
        try activity.begin()
        await workflow.readIfNeeded()
        XCTAssertNil(workflow.snapshot)
        XCTAssertNil(workflow.error)
        activity.finish()
        await workflow.readIfNeeded()
        XCTAssertNotNil(workflow.snapshot)
    }

    @MainActor func testMalformedCapturesNeverBecomeBaselines() async {
        let valid = AssignmentSnapshot(location: 1, channels: [Data(repeating: 0, count: 640)])
        var invalidLighting = valid.channels
        invalidLighting[0] = Data(repeating: 0, count: 639)
        for capture in [
            AssignmentSnapshot(location: 0, channels: valid.channels), AssignmentSnapshot(location: 1, channels: []),
            AssignmentSnapshot(location: 1, channels: Array(valid.channels.dropLast())),
            AssignmentSnapshot(location: 1, channels: invalidLighting)
        ] {
            let activity = KeyboardDeviceActivity()
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let workflow = AssignmentWorkflow(
                service: MockAssignmentService(capture: capture), root: root, activity: activity, checkAccess: { true })
            await workflow.read()
            XCTAssertNil(workflow.snapshot)
            XCTAssertNotNil(workflow.error)
            XCTAssertFalse(activity.isBusy)
        }
    }

    @MainActor func testIncorrectWriteResultsBlockFurtherWrites() async throws {
        let channels = [Data(repeating: 0, count: 640)]
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        var expected = channels
        expected[0] = try draft.candidate(from: channels[0])
        var corruptedOtherChannel = expected
        corruptedOtherChannel[0][0] ^= 1
        for result in [
            AssignmentSnapshot(location: 0x0114_0000, channels: channels),
            AssignmentSnapshot(location: 99, channels: expected),
            AssignmentSnapshot(location: 0x0114_0000, channels: corruptedOtherChannel),
            AssignmentSnapshot(location: 0x0114_0000, channels: [])
        ] {
            let activity = KeyboardDeviceActivity()
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let workflow = AssignmentWorkflow(
                service: MockAssignmentService(result: result), root: root, activity: activity, checkAccess: { true })
            await workflow.read()
            workflow.prepare(draft)
            await workflow.apply(try XCTUnwrap(workflow.review), currentDraft: draft)
            XCTAssertNil(workflow.snapshot)
            XCTAssertTrue(activity.requiresInspection)
            XCTAssertFalse(activity.isBusy)
        }
    }

    @MainActor func testPermissionRevocationReplacesSuccessfulReadStatus() async {
        @MainActor final class Permission { var granted = true }

        let permission = Permission()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(
            service: MockAssignmentService(), root: root, activity: KeyboardDeviceActivity(),
            checkAccess: { permission.granted })
        await workflow.read()
        XCTAssertNotNil(workflow.snapshot)
        permission.granted = false
        await workflow.read()
        XCTAssertNil(workflow.snapshot)
        XCTAssertTrue(workflow.needsInputMonitoringAccess)
        XCTAssertEqual(workflow.status, "Read stopped; Input Monitoring access is required.")
        permission.granted = true
        await workflow.read()
        XCTAssertFalse(workflow.needsInputMonitoringAccess)
        XCTAssertNotNil(workflow.snapshot)
    }

    @MainActor func testStaleReviewNeverCallsWriter() async throws {
        let service = MockAssignmentService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(
            service: service, root: root, activity: KeyboardDeviceActivity(), checkAccess: { true })
        await workflow.read()
        let draft = KeyAssignmentDraft(assignments: [
            KeyAssignment(source: 90, action: KeyAction(kind: .keyboard, target: 4))
        ])
        workflow.prepare(draft)
        let review = try XCTUnwrap(workflow.review)
        await workflow.apply(review, currentDraft: KeyAssignmentDraft())
        let writes = await service.writes
        XCTAssertEqual(writes, 0)
        XCTAssertNil(workflow.review)
    }

    @MainActor func testVerifiedCandidateBecomesBaselineForNextEdit() async throws {
        let service = MockAssignmentService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(
            service: service, root: root, activity: KeyboardDeviceActivity(), checkAccess: { true })
        await workflow.read()
        let draft = KeyAssignmentDraft(assignments: [
            KeyAssignment(source: 90, action: KeyAction(kind: .keyboard, target: 4))
        ])
        workflow.prepare(draft)
        let review = try XCTUnwrap(workflow.review)
        await workflow.apply(review, currentDraft: draft)
        XCTAssertEqual(workflow.snapshot?.channels[0], review.candidate)
        XCTAssertTrue(workflow.draftMatchesKeyboard(draft))
        XCTAssertFalse(workflow.draftMatchesKeyboard(KeyAssignmentDraft()))
        XCTAssertNil(workflow.error)
        XCTAssertFalse(workflow.isBusy)
        workflow.prepare(draft)
        XCTAssertNil(workflow.review)  // Already matches: no repeated write.
    }

    @MainActor func testUncertainWriteDiscardsBaselineAndCannotBeRearmedByReading() async throws {
        let service = MockAssignmentService(writeError: NativeDeviceError(code: 3, message: "ambiguous"))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(
            service: service, root: root, activity: KeyboardDeviceActivity(), checkAccess: { true })
        await workflow.read()
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        workflow.prepare(draft)
        await workflow.apply(try XCTUnwrap(workflow.review), currentDraft: draft)
        XCTAssertNil(workflow.snapshot)
        XCTAssertTrue(workflow.requiresInspection)
        let failedEvidence = workflow.lastEvidence
        await workflow.read()
        XCTAssertEqual(workflow.lastEvidence, failedEvidence)
        workflow.prepare(draft)
        XCTAssertNil(workflow.review)
        XCTAssertTrue(workflow.requiresInspection)
        let writes = await service.writes
        XCTAssertEqual(writes, 1)
    }

    @MainActor func testPermissionAndSharedActivityBlockCapture() async {
        let service = MockAssignmentService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let activity = KeyboardDeviceActivity()
        let workflow = AssignmentWorkflow(service: service, root: root, activity: activity, checkAccess: { false })
        await workflow.read()
        XCTAssertNil(workflow.snapshot)
        XCTAssertNotNil(workflow.error)
        XCTAssertFalse(activity.isBusy)
        try? activity.begin()
        await workflow.read()
        XCTAssertTrue(activity.isBusy)
        XCTAssertNil(workflow.snapshot)
        activity.finish()
    }

    @MainActor func testUncertaintyBlocksOtherWindowsAndLightingEvenWithExistingReview() async throws {
        let activity = KeyboardDeviceActivity()
        let service = MockAssignmentService(
            writeError: NativeDeviceError(code: Int32(C989_UPLOAD_UNCERTAIN), message: "incomplete"))
        let otherService = MockAssignmentService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let first = AssignmentWorkflow(service: service, root: root, activity: activity, checkAccess: { true })
        let other = AssignmentWorkflow(service: otherService, root: root, activity: activity, checkAccess: { true })
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        await first.read()
        await other.read()
        first.prepare(draft)
        other.prepare(draft)
        let existingReview = try XCTUnwrap(other.review)
        await first.apply(try XCTUnwrap(first.review), currentDraft: draft)
        XCTAssertNil(first.lastEvidence)
        XCTAssertTrue(other.requiresInspection)
        XCTAssertNil(other.lastEvidence)
        await other.apply(existingReview, currentDraft: draft)
        let writes = await otherService.writes
        XCTAssertEqual(writes, 0)
        await other.read()
        other.prepare(draft)
        XCTAssertNil(other.review)
        XCTAssertNil(other.lastEvidence)

        let lighting = KeyboardState(activity: activity)
        lighting.prepareReview()
        XCTAssertTrue(lighting.error?.contains("Further writes are blocked") == true)
        XCTAssertFalse(lighting.isBusy)
        XCTAssertFalse(activity.isBusy)
        activity.blockWrites(evidence: root.appendingPathComponent("later"))
        XCTAssertNil(activity.inspectionEvidence)
    }

    @MainActor func testPreUploadStopDoesNotPoisonOtherWindows() async throws {
        let activity = KeyboardDeviceActivity()
        let service = MockAssignmentService(
            writeError: NativeDeviceError(code: Int32(C989_STOPPED), message: "preflight mismatch"))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(service: service, root: root, activity: activity, checkAccess: { true })
        await workflow.read()
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        workflow.prepare(draft)
        await workflow.apply(try XCTUnwrap(workflow.review), currentDraft: draft)
        XCTAssertFalse(activity.requiresInspection)
        XCTAssertFalse(activity.isBusy)
        XCTAssertNil(workflow.snapshot)
        XCTAssertNoThrow(try activity.checkWriteAllowed())
    }

    @MainActor func testUnclassifiedHandoffFailureBlocksAllWriters() async throws {
        let activity = KeyboardDeviceActivity()
        let service = MockAssignmentService(writeError: CocoaError(.fileReadCorruptFile))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(service: service, root: root, activity: activity, checkAccess: { true })
        await workflow.read()
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        workflow.prepare(draft)
        await workflow.apply(try XCTUnwrap(workflow.review), currentDraft: draft)
        XCTAssertTrue(activity.requiresInspection)
        XCTAssertFalse(activity.isBusy)
        XCTAssertThrowsError(try activity.checkWriteAllowed())
    }

    @MainActor func testLightingCannotReleaseAnotherOperationsActivity() throws {
        let activity = KeyboardDeviceActivity()
        try activity.begin()
        let lighting = KeyboardState(activity: activity)
        lighting.refresh()
        lighting.prepareReview()
        XCTAssertTrue(activity.isBusy)
        XCTAssertFalse(lighting.isBusy)
        XCTAssertThrowsError(try activity.begin())
        activity.finish()
    }

    @MainActor func testDisabledLoggingDoesNotRequireAnEvidenceDirectory() async throws {
        let activity = KeyboardDeviceActivity()
        let service = MockAssignmentService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = AssignmentWorkflow(service: service, root: root, activity: activity, checkAccess: { true })
        await workflow.read()
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 90, action: KeyAction(kind: .disabled))])
        workflow.prepare(draft)
        let review = try XCTUnwrap(workflow.review)

        // Even an unusable diagnostic destination must not stop an in-memory operation.
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
        try Data().write(to: root)
        await workflow.apply(review, currentDraft: draft)
        let writes = await service.writes
        XCTAssertEqual(writes, 1)
        XCTAssertNil(workflow.error)
        XCTAssertFalse(activity.requiresInspection)
        XCTAssertFalse(activity.isBusy)
    }
}
