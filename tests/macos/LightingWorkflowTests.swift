import C989Native
import XCTest

@testable import KeyboardLights

private actor MockLightingService: LightingDeviceService {
    let initial: Data
    let writeError: (any Error)?
    let wrongResult: Bool
    var captures = 0
    var writes: [Data] = []
    var captureFails = false

    func failFutureCaptures() { captureFails = true }

    init(initial: Data = makeTestLightingImage(), writeError: (any Error)? = nil, wrongResult: Bool = false) {
        self.initial = initial
        self.writeError = writeError
        self.wrongResult = wrongResult
    }

    func capture(output: URL) async throws -> DeviceSnapshot {
        captures += 1
        if captureFails { throw WorkflowError.message("injected capture failure") }

        return DeviceSnapshot(location: "01140000", lightingData: initial)
    }

    func write(snapshot: DeviceSnapshot, candidate: Data, output: URL) async throws -> DeviceSnapshot {
        writes.append(candidate)
        if let writeError { throw writeError }

        return DeviceSnapshot(location: snapshot.location, lightingData: wrongResult ? initial : candidate)
    }
}

final class LightingWorkflowTests: XCTestCase {
    @MainActor func testInactiveControlsCannotOfferAnEmptyLightingUpdate() async throws {
        let image = try LightingCandidateBuilder.buildPersistentStatic(
            from: makeTestLightingImage(), color: RGB(red: 20, green: 100, blue: 200), brightness: 16)
        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: MockLightingService(initial: image),
            root: FileManager.default.temporaryDirectory, checkAccess: { true })
        await workflow.read()
        workflow.animationSpeed = 3
        XCTAssertTrue(workflow.hasUnsavedLightingChanges)
        XCTAssertFalse(workflow.hasLightingChangesToApply)
    }

    @MainActor func testResetAndPaintUseOneKeyboardAndPreserveVerifiedState() async throws {
        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: MockLightingService(),
            root: FileManager.default.temporaryDirectory, checkAccess: { true })
        await workflow.read()
        let original = try workflow.savedLightingDraft()
        let current = workflow.currentKeyboardSlots
        workflow.editorStyle = .perKey
        let color = RGB(red: 21, green: 102, blue: 213)
        workflow.paintKey(90, color: color)
        XCTAssertEqual(workflow.slots[90], LEDSlot(enabled: true, rgb: color))
        XCTAssertEqual(workflow.currentKeyboardSlots, current)
        workflow.paintKey(90, color: color, enabled: false)
        XCTAssertFalse(workflow.slots[90].enabled)
        workflow.resetLighting()
        XCTAssertEqual(try workflow.savedLightingDraft(), original)
        XCTAssertFalse(workflow.hasLightingChangesToApply)
        XCTAssertNil(workflow.review)
    }

    @MainActor func testBrightnessOffAndGeneratedAnimationIgnoreHiddenColorSettings() async {
        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: MockLightingService(),
            root: FileManager.default.temporaryDirectory, checkAccess: { true })
        await workflow.read()
        workflow.editorStyle = .solid
        workflow.brightness = 1
        XCTAssertEqual(workflow.brightnessPercent, 0)
        XCTAssertEqual(workflow.previewSlots[90].rgb, RGB(red: 0, green: 0, blue: 0))
        workflow.editorStyle = .animation
        workflow.animationMode = .colourful
        workflow.brightness = 16
        workflow.animationUsesGeneratedColor = false
        workflow.animationColor = RGB(red: 0, green: 0, blue: 0)
        XCTAssertNotEqual(workflow.previewSlots[90].rgb, RGB(red: 0, green: 0, blue: 0))
    }

    @MainActor func testKeyboardPreviewFollowsColorBrightnessAndVerifiedUpdate() async throws {
        let service = MockLightingService()
        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: service, root: FileManager.default.temporaryDirectory,
            checkAccess: { true })
        await workflow.read()
        workflow.editorStyle = .solid
        workflow.solidColor = RGB(red: 200, green: 100, blue: 50)
        workflow.brightness = 8
        XCTAssertEqual(workflow.previewSlots[90].rgb, RGB(red: 100, green: 50, blue: 25))
        workflow.prepareReview()
        await workflow.applyReviewedWrite(try XCTUnwrap(workflow.review))
        XCTAssertFalse(workflow.hasUnsavedLightingChanges)
        XCTAssertEqual(workflow.slots[90].rgb, RGB(red: 200, green: 100, blue: 50))
        XCTAssertEqual(workflow.previewSlots[90].rgb, RGB(red: 100, green: 50, blue: 25))
        XCTAssertEqual(workflow.status, "Keyboard lighting updated.")
    }

    @MainActor func testStoppedWriteRetainsPortableLightingDraftForSaving() async throws {
        for code in [C989_STOPPED, C989_UPLOAD_UNCERTAIN, C989_ACTIVATION_UNCERTAIN, C989_EVIDENCE_INCOMPLETE] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let workflow = KeyboardState(
                activity: KeyboardDeviceActivity(),
                service: MockLightingService(writeError: NativeDeviceError(code: Int32(code), message: "injected")),
                root: root, checkAccess: { true })
            XCTAssertFalse(workflow.hasLightingDraft)
            XCTAssertThrowsError(try workflow.savedLightingDraft())
            await workflow.read()
            workflow.solidColor = RGB(red: 11, green: 22, blue: 33)
            let draft = try workflow.savedLightingDraft()
            workflow.prepareReview()
            await workflow.applyReviewedWrite(try XCTUnwrap(workflow.review))
            XCTAssertFalse(workflow.hasSnapshot)
            XCTAssertTrue(workflow.hasLightingDraft)
            XCTAssertEqual(try workflow.savedLightingDraft(), draft)
            let profile = KeyboardProfile(name: "Recovery", draft: try workflow.savedLightingDraft())
            XCTAssertEqual(try KeyboardProfile.decode(profile.encoded()).draft, draft)
        }
    }

    @MainActor func testRefreshRequiresDiscardDecisionAndPreservesDraftOnCancel() async throws {
        let service = MockLightingService()
        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: service, root: FileManager.default.temporaryDirectory,
            checkAccess: { true })
        await workflow.read()
        XCTAssertFalse(workflow.hasUnsavedLightingChanges)
        let original = workflow.brightness
        workflow.brightness = original == 16 ? 15 : 16
        workflow.prepareReview()
        let review = try XCTUnwrap(workflow.review)

        await workflow.read()
        XCTAssertTrue(workflow.confirmsLightingDiscard)
        XCTAssertTrue(workflow.hasUnsavedLightingChanges)
        XCTAssertEqual(workflow.review?.id, review.id)
        let captures = await service.captures
        XCTAssertEqual(captures, 1)

        // Cancelling the alert leaves the editor and its review intact.
        workflow.confirmsLightingDiscard = false
        XCTAssertNotEqual(workflow.brightness, original)
        workflow.brightness = original
        XCTAssertFalse(workflow.hasUnsavedLightingChanges)

        workflow.paintKey(LegacyKey.exposed[0].index, color: RGB(red: 1, green: 2, blue: 3))
        XCTAssertTrue(workflow.hasUnsavedLightingChanges)
        await workflow.read(discardingEdits: true)
        XCTAssertFalse(workflow.hasUnsavedLightingChanges)
        XCTAssertFalse(workflow.confirmsLightingDiscard)
        XCTAssertNil(workflow.review)
        let finalCaptures = await service.captures
        XCTAssertEqual(finalCaptures, 2)
    }

    @MainActor func testFailedConfirmedRefreshKeepsStagedEditsAndBaseline() async throws {
        let service = MockLightingService()
        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: service, root: FileManager.default.temporaryDirectory,
            checkAccess: { true })
        await workflow.read()
        let baseline = try XCTUnwrap(workflow.snapshot)
        workflow.solidColor = RGB(red: 12, green: 34, blue: 56)
        await service.failFutureCaptures()
        await workflow.read(discardingEdits: true)
        XCTAssertEqual(workflow.snapshot?.lightingData, baseline.lightingData)
        XCTAssertEqual(workflow.solidColor, RGB(red: 12, green: 34, blue: 56))
        XCTAssertTrue(workflow.hasUnsavedLightingChanges)
        XCTAssertNotNil(workflow.error)
        XCTAssertFalse(workflow.isBusy)
    }

    @MainActor func testReviewAndWriteUseExactMemoryWithoutFileHandoff() async throws {
        let service = MockLightingService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: service, root: root, checkAccess: { true })
        await workflow.read()
        workflow.solidColor = RGB(red: 255, green: 0, blue: 255)
        workflow.prepareReview()
        let review = try XCTUnwrap(workflow.review)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))

        // Even unrelated evidence on disk cannot change the approved bytes.
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try Data(repeating: 255, count: 1152).write(to: root.appendingPathComponent("candidate-channel-1.bin"))
        await workflow.applyReviewedWrite(review)
        let writes = await service.writes
        XCTAssertEqual(writes, [review.candidate])
        XCTAssertEqual(workflow.snapshot?.lightingData, review.candidate)
        XCTAssertNil(workflow.error)
        XCTAssertNil(workflow.review)
        XCTAssertFalse(workflow.hasUnsavedLightingChanges)
        await workflow.applyReviewedWrite(review)
        let finalWrites = await service.writes
        XCTAssertEqual(finalWrites.count, 1)
    }

    @MainActor func testStaleReviewNeverCallsDevice() async throws {
        let service = MockLightingService()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: service, root: root, checkAccess: { true })
        await workflow.read()
        workflow.prepareReview()
        let review = try XCTUnwrap(workflow.review)
        workflow.draftChanged()
        await workflow.applyReviewedWrite(review)
        let writes = await service.writes
        XCTAssertTrue(writes.isEmpty)
    }

    @MainActor func testNativeWriteFailuresBlockOnlyAfterUploadOrVerifiedEvidenceFailure() async throws {
        for code in [C989_STOPPED, C989_UPLOAD_UNCERTAIN, C989_ACTIVATION_UNCERTAIN, C989_EVIDENCE_INCOMPLETE] {
            let service = MockLightingService(writeError: NativeDeviceError(code: Int32(code), message: "injected"))
            let activity = KeyboardDeviceActivity()
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let workflow = KeyboardState(activity: activity, service: service, root: root, checkAccess: { true })
            await workflow.read()
            workflow.prepareReview()
            await workflow.applyReviewedWrite(try XCTUnwrap(workflow.review))
            XCTAssertEqual(activity.requiresInspection, code != C989_STOPPED)
            XCTAssertFalse(workflow.hasSnapshot)
            XCTAssertFalse(activity.isBusy)
            if activity.requiresInspection {
                await workflow.read()
                workflow.prepareReview()
                XCTAssertNil(workflow.review)
            }
        }
    }

    @MainActor func testIncorrectDeviceResultCannotBecomeBaseline() async throws {
        let service = MockLightingService(wrongResult: true)
        let activity = KeyboardDeviceActivity()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = KeyboardState(activity: activity, service: service, root: root, checkAccess: { true })
        await workflow.read()
        workflow.prepareReview()
        await workflow.applyReviewedWrite(try XCTUnwrap(workflow.review))
        XCTAssertFalse(workflow.hasSnapshot)
        XCTAssertTrue(activity.requiresInspection)
    }

    @MainActor func testSingleColorBlackSurvivesReadAndBrightnessEdit() async throws {
        let initial = try LightingCandidateBuilder.buildBuiltIn(
            from: makeTestLightingImage(),
            settings: BuiltInSettings(
                mode: .breath, color: RGB(red: 0, green: 0, blue: 0), usesGeneratedColor: false, brightness: 8,
                speed: 12, direction: .right))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let workflow = KeyboardState(
            activity: KeyboardDeviceActivity(), service: MockLightingService(initial: initial), root: root,
            checkAccess: { true })
        await workflow.read()
        XCTAssertEqual(workflow.animationColor, RGB(red: 0, green: 0, blue: 0))
        XCTAssertFalse(workflow.animationUsesGeneratedColor)
        workflow.brightness = 7
        workflow.prepareReview()
        let review = try XCTUnwrap(workflow.review)
        XCTAssertEqual(Array(review.candidate[0x441..<0x444]), [0, 0, 0])
    }

    func testInvalidNativeCallsStopBeforeEvidenceOrHardwareAccess() {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        XCTAssertThrowsError(
            try NativeKeyboardBackend().writeLighting(baseline: Data(), location: 0, candidate: Data(), output: root))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }
}
