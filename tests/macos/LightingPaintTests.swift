import Combine
import XCTest

@testable import KeyboardLights

final class LightingPaintTests: XCTestCase {
    @MainActor private func state(image: Data = makeTestLightingImage()) async -> KeyboardState {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }

        let state = KeyboardState(
            activity: KeyboardDeviceActivity(), service: PaintCaptureService(image: image), root: root,
            checkAccess: { true })
        await state.read()
        return state
    }

    @MainActor func testPointerStartsSelectedAndNeverStagesOrInvalidatesLighting() async throws {
        let state = await state()
        let original = try state.savedLightingDraft()
        XCTAssertTrue(state.isPointerSelected)
        state.paintClickedKey(39)
        XCTAssertEqual(try state.savedLightingDraft(), original)
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        XCTAssertFalse(state.isPointerSelected)
        state.paintClickedKey(39)
        XCTAssertEqual(state.slots[39].rgb.hex, "#123456")
        state.prepareReview()
        let review = try XCTUnwrap(state.review)
        let draft = try state.savedLightingDraft()
        state.selectPointer()
        state.paintClickedKey(56)
        XCTAssertTrue(state.isPointerSelected)
        XCTAssertEqual(try state.savedLightingDraft(), draft)
        XCTAssertEqual(state.review?.id, review.id)
    }

    @MainActor func testAnimationForcesPointerAndReturningToSolidKeepsItSelected() async {
        let state = await state()
        state.selectPaintColor(RGB(red: 255, green: 0, blue: 0))
        XCTAssertFalse(state.isPointerSelected)
        state.selectLightingMode(11)
        XCTAssertTrue(state.isPointerSelected)
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 0))
        XCTAssertTrue(state.isPointerSelected)
        state.selectLightingMode(128)
        XCTAssertTrue(state.isPointerSelected)
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 0))
        XCTAssertFalse(state.isPointerSelected)
        state.paintClickedKey(39)
        XCTAssertFalse(state.slots[39].enabled)
    }

    @MainActor func testPerKeyRevertPreservesOtherPaintBlanksAndBrush() async throws {
        let state = await state()
        state.selectLightingMode(128)
        let baseline = try XCTUnwrap(state.snapshot).lightingData
        state.clearPaint()
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        state.paintSelectedColor(on: 39)
        state.paintSelectedColor(on: 56)
        state.revertKeyLighting(39)
        XCTAssertFalse(state.hasKeyLightingChange(39))
        XCTAssertTrue(state.hasKeyLightingChange(56))
        XCTAssertFalse(state.unpaintedKeys.contains(39))
        XCTAssertTrue(state.unpaintedKeys.contains(57))
        XCTAssertEqual(state.slots[56].rgb.hex, "#123456")
        XCTAssertEqual(state.selectedPaintColor.hex, "#123456")
        XCTAssertEqual(state.snapshot?.lightingData, baseline)
        XCTAssertNil(state.review)
    }

    @MainActor func testKeyStatusDistinguishesAnimationBlanksAndOff() async throws {
        let state = await state()
        state.clearPaint()
        XCTAssertEqual(state.keyLightingDescription(39, onKeyboard: false), "Unpainted")
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 0))
        state.paintSelectedColor(on: 39)
        XCTAssertTrue(state.keyLightingDescription(39, onKeyboard: false).hasPrefix("Off"))
        state.selectLightingMode(11)
        let draft = try state.savedLightingDraft()
        state.revertKeyLighting(39)
        XCTAssertEqual(try state.savedLightingDraft(), draft)
        XCTAssertFalse(state.hasKeyLightingChange(39))
        XCTAssertTrue(
            state.keyLightingDescription(39, onKeyboard: false).hasPrefix(draft.mode.name + " · Full color · speed"))
    }

    @MainActor func testAnimationInspectionExposesSpeedBrightnessAndCustomColors() async throws {
        let state = await state()
        state.selectLightingMode(11)
        let before = state.keyLightingDescription(39, onKeyboard: false)
        state.animationSpeed = 3
        state.draftChanged()
        let after = state.keyLightingDescription(39, onKeyboard: false)
        XCTAssertNotEqual(after, before)
        XCTAssertTrue(after.contains("speed 3"))
        XCTAssertTrue(after.contains("brightness"))
        XCTAssertTrue(after.contains("Full color"))
        state.selectLightingMode(8)
        state.animationUsesGeneratedColor = false
        state.animationColor = RGB(red: 0x12, green: 0x34, blue: 0x56)
        state.draftChanged()
        XCTAssertTrue(state.keyLightingDescription(39, onKeyboard: false).contains("#123456"))
    }

    @MainActor func testBrushSelectionIsLocalAndMatchesPresetsExactly() async throws {
        let state = await state()
        let original = try state.savedLightingDraft()
        for preset in LightingColorPreset.all {
            state.selectPaintColor(preset.rgb)
            XCTAssertEqual(state.paintColorName, preset.name)
            XCTAssertEqual(state.selectedPaintColor.hex, preset.hex)
        }

        XCTAssertEqual(try state.savedLightingDraft(), original)
        XCTAssertFalse(state.hasUnsavedLightingChanges)
        XCTAssertEqual(state.lastUsedColor.hex, "#00FFFF")
    }

    @MainActor func testClearPaintAndFillPreserveExplicitOffAndReservedSlots() async throws {
        let state = await state()
        let baseline = try XCTUnwrap(state.snapshot).lightingData
        state.clearPaint()
        XCTAssertEqual(state.unpaintedKeys, LegacyKey.physicalIndices)
        XCTAssertTrue(state.hasUnsavedLightingChanges)
        XCTAssertFalse(state.hasLightingChangesToApply)
        XCTAssertThrowsError(try state.savedLightingDraft())
        state.prepareReview()
        XCTAssertNil(state.review)
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        state.paintSelectedColor(on: 39)
        XCTAssertEqual(state.lastUsedColor.hex, "#123456")
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 0))
        state.paintSelectedColor(on: 56)
        XCTAssertEqual(state.lastUsedColor.hex, "#123456")
        XCTAssertFalse(state.slots[56].enabled)
        state.selectPaintColor(RGB(red: 0xAB, green: 0xCD, blue: 0xEF))
        state.fillPaint()
        XCTAssertTrue(state.unpaintedKeys.isEmpty)
        XCTAssertEqual(state.slots[39].rgb.hex, "#123456")
        XCTAssertFalse(state.slots[56].enabled)
        XCTAssertEqual(state.slots[57].rgb.hex, "#ABCDEF")
        state.prepareReview()
        let candidate = try XCTUnwrap(state.review).candidate
        for index in 0..<144 where !LegacyKey.physicalIndices.contains(index) {
            let offset = 0x200 + index * 4
            XCTAssertEqual(candidate[offset..<(offset + 4)], baseline[offset..<(offset + 4)])
        }

        state.fillPaint(overwriting: true)
        XCTAssertNil(state.review)
        XCTAssertTrue(
            LegacyKey.physical.allSatisfy {
                state.slots[$0.index].enabled && state.slots[$0.index].rgb.hex == "#ABCDEF"
            })
    }

    @MainActor func testBulkPaintPublishesOneCompleteLayout() async {
        let state = await state()
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        var publications = 0
        let subscription = state.$slots.dropFirst().sink { _ in publications += 1 }

        defer { subscription.cancel() }

        state.fillPaint(overwriting: true)
        XCTAssertEqual(publications, 1)
        XCTAssertTrue(LegacyKey.physical.allSatisfy { state.slots[$0.index].rgb.hex == "#123456" })
    }

    @MainActor func testResetAndProfileReplacementClearBlankMarkers() async throws {
        let state = await state()
        let original = try state.savedLightingDraft()
        state.clearPaint()
        state.resetLighting()
        XCTAssertFalse(state.hasUnpaintedKeys)
        XCTAssertEqual(try state.savedLightingDraft(), original)
        state.clearPaint()
        try state.loadLightingProfile(KeyboardProfile(name: "Existing", draft: original))
        XCTAssertTrue(state.unpaintedKeys.isEmpty)
        XCTAssertEqual(try state.savedLightingDraft(), original)
    }

    @MainActor func testAnimationModeSelectionUsesGeneratedColorsAndSpeedKeepsStaticDraft() async throws {
        let state = await state()
        state.clearPaint()
        state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))
        XCTAssertEqual(state.editorStyle, .animation)
        XCTAssertTrue(state.animationUsesGeneratedColor)
        XCTAssertEqual(state.brightness, 16)
        XCTAssertFalse(state.hasUnpaintedKeys)
        state.animationSpeed = 7
        state.draftChanged()
        state.prepareReview()
        XCTAssertNotNil(state.review)
        state.selectLightingMode(128)
        XCTAssertTrue(state.hasUnpaintedKeys)
        XCTAssertNil(state.review)
    }

    @MainActor func testIncompleteDraftCannotApplyEvenWithARestoredReview() async throws {
        let state = await state()
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        state.fillPaint(overwriting: true)
        state.prepareReview()
        let review = try XCTUnwrap(state.review)
        let snapshot = state.snapshot
        state.clearPaint()
        state.review = review
        await state.applyReviewedWrite(review)
        XCTAssertEqual(state.snapshot, snapshot)
        XCTAssertTrue(state.hasUnpaintedKeys)
        XCTAssertNil(state.review)
        XCTAssertTrue(state.error?.contains("blank key") == true)
    }

    @MainActor func testModeRoundTripPreservesStaticBrightnessAndLegacyColor() async throws {
        let state = await state()
        XCTAssertEqual(state.editorStyle, .solid)
        let color = state.solidColor
        state.brightness = 8
        state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))
        XCTAssertEqual(state.brightness, 16)
        state.selectLightingMode(128)
        XCTAssertEqual(state.brightness, 8)
        XCTAssertTrue(
            LegacyKey.physical.allSatisfy { state.slots[$0.index].enabled && state.slots[$0.index].rgb == color })
    }

    @MainActor func testInactiveAndEmptyFillPreserveModeAndReview() async throws {
        let state = await state()
        state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))
        state.prepareReview()
        let review = try XCTUnwrap(state.review)
        let original = try state.savedLightingDraft()
        state.fillPaint()
        state.clearPaint()
        XCTAssertEqual(try state.savedLightingDraft(), original)
        XCTAssertEqual(state.review?.id, review.id)
        state.selectLightingMode(128)
        state.fillPaint(overwriting: true)
        state.prepareReview()
        let staticReview = try XCTUnwrap(state.review)
        let staticDraft = try state.savedLightingDraft()
        state.fillPaint()
        XCTAssertEqual(try state.savedLightingDraft(), staticDraft)
        XCTAssertEqual(state.review?.id, staticReview.id)
    }

    @MainActor func testHiddenBlankDraftRemainsUnsavedWhenAnimationMatchesBaseline() async throws {
        let image = try LightingCandidateBuilder.buildBuiltIn(
            from: makeTestLightingImage(),
            settings: BuiltInSettings(
                mode: .rolling, color: RGB(red: 0, green: 255, blue: 0), usesGeneratedColor: true, brightness: 16,
                speed: 12, direction: .right))
        let state = await state(image: image)
        state.selectLightingMode(128)
        state.clearPaint()
        state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))

        // The blank draft remains protected even when the active animation matches the captured bytes.
        XCTAssertFalse(state.hasLightingChangesToApply)
        XCTAssertTrue(state.hasUnsavedLightingChanges)
        let snapshot = state.snapshot
        await state.read()
        XCTAssertEqual(state.snapshot, snapshot)
        state.selectLightingMode(128)
        XCTAssertEqual(state.unpaintedKeys, LegacyKey.physicalIndices)
    }

    @MainActor func testInactivePaintAndBrightnessEditsRemainProtected() async throws {
        let image = try LightingCandidateBuilder.buildBuiltIn(
            from: makeTestLightingImage(),
            settings: BuiltInSettings(
                mode: .rolling, color: RGB(red: 0, green: 255, blue: 0), usesGeneratedColor: true, brightness: 16,
                speed: 12, direction: .right))
        for paintsKey in [false, true] {
            let state = await state(image: image)
            state.selectLightingMode(128)
            if paintsKey {
                state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
                state.paintSelectedColor(on: 39)
            } else {
                state.brightness = 8
                state.draftChanged()
            }

            state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))
            XCTAssertFalse(state.hasLightingChangesToApply)
            XCTAssertTrue(state.hasUnsavedLightingChanges)
            let snapshot = state.snapshot
            await state.read()
            XCTAssertEqual(state.snapshot, snapshot)
            state.selectLightingMode(128)
            if paintsKey {
                XCTAssertEqual(state.slots[39].rgb.hex, "#123456")
            } else {
                XCTAssertEqual(state.brightness, 8)
            }

            state.resetLighting()
            XCTAssertFalse(state.hasUnsavedLightingChanges)
        }
    }

    @MainActor func testBucketUsesPhysicalEdgesAndKeepsDisconnectedRegionsAndReservedSlots() async throws {
        let state = await state()
        let reserved = state.slots.enumerated().filter { !LegacyKey.physicalIndices.contains($0.offset) }

        state.selectPaintColor(RGB(red: 255, green: 0, blue: 0))
        state.paintSelectedColor(on: 39)
        state.paintSelectedColor(on: 40)
        state.selectBucketColor(RGB(red: 0xAB, green: 0xCD, blue: 0xEF))
        state.paintClickedKey(39)
        XCTAssertEqual(state.slots[39].rgb.hex, "#ABCDEF")
        XCTAssertEqual(state.slots[40].rgb.hex, "#ABCDEF")
        state.selectPaintColor(RGB(red: 0, green: 255, blue: 255))
        state.fillPaint(overwriting: true)
        state.selectPaintColor(RGB(red: 255, green: 0, blue: 0))
        state.paintSelectedColor(on: 39)
        state.paintSelectedColor(on: 58)
        state.selectPaintTool(.bucket)
        state.paintClickedKey(39)
        XCTAssertEqual(state.slots[58].rgb.hex, "#FF0000")
        for (index, slot) in reserved { XCTAssertEqual(state.slots[index], slot) }

        XCTAssertEqual(state.keyPaintColor.hex, "#FF0000")
        XCTAssertEqual(state.lastUsedColor.hex, "#FF0000")
        XCTAssertEqual(state.lastBucketColor.hex, "#ABCDEF")
        XCTAssertEqual(LegacyKey.neighbors[39]?.contains(40), true)
        XCTAssertEqual(LegacyKey.neighbors[39]?.contains(58), false)
    }

    @MainActor func testBucketSeparatesBlankOffAndPaintedRegionsAndInvalidatesReview() async throws {
        let state = await state()
        state.clearPaint()
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        state.paintSelectedColor(on: 39)
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 0))
        state.paintSelectedColor(on: 56)
        state.selectBucketColor(RGB(red: 0xAB, green: 0xCD, blue: 0xEF))
        state.paintClickedKey(40)
        XCTAssertEqual(state.slots[40].rgb.hex, "#ABCDEF")
        XCTAssertFalse(state.unpaintedKeys.contains(40))
        XCTAssertTrue(state.unpaintedKeys.contains(109))
        XCTAssertFalse(state.slots[56].enabled)
        XCTAssertEqual(state.slots[39].rgb.hex, "#123456")
        XCTAssertThrowsError(try state.savedLightingDraft())
        state.paintClickedKey(56)
        XCTAssertEqual(state.slots[56].rgb.hex, "#ABCDEF")
        state.fillPaint()
        state.prepareReview()
        XCTAssertNotNil(state.review)
        state.selectBucketColor(RGB(red: 0, green: 0, blue: 0))
        state.paintClickedKey(40)
        XCTAssertNil(state.review)
        XCTAssertFalse(state.slots[40].enabled)
        XCTAssertEqual(state.lastBucketColor.hex, "#ABCDEF")
    }

    @MainActor func testBucketTreatsOffColorsEquallyAndAnimationForcesPointer() async throws {
        let state = await state()
        state.selectPaintColor(RGB(red: 255, green: 0, blue: 0))
        state.paintSelectedColor(on: 39)
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 255))
        state.paintSelectedColor(on: 40)
        state.selectPaintColor(RGB(red: 0, green: 0, blue: 0))
        state.paintSelectedColor(on: 39)
        state.paintSelectedColor(on: 40)
        state.selectBucketColor(RGB(red: 0xAB, green: 0xCD, blue: 0xEF))
        state.paintClickedKey(39)
        XCTAssertEqual(state.slots[40].rgb.hex, "#ABCDEF")
        state.selectLightingMode(11)
        let animation = try state.savedLightingDraft()
        state.floodFill(on: 39)
        XCTAssertEqual(try state.savedLightingDraft(), animation)
        XCTAssertTrue(state.isPointerSelected)
        state.selectLightingMode(128)
        XCTAssertTrue(state.isPointerSelected)
        XCTAssertEqual(state.bucketColor.hex, "#ABCDEF")
    }

    @MainActor func testToolHistoriesSurviveReopeningAndFillUsesActiveTool() async {
        let suite = "KeyboardLightsPaintTests-" + UUID().uuidString
        let preferences = UserDefaults(suiteName: suite)!
        defer { preferences.removePersistentDomain(forName: suite) }

        let state = KeyboardState(
            activity: KeyboardDeviceActivity(), service: PaintCaptureService(image: makeTestLightingImage()),
            checkAccess: { true }, preferences: preferences)
        await state.read()
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        state.paintClickedKey(39)
        state.clearPaint()
        state.selectBucketColor(RGB(red: 0xAB, green: 0xCD, blue: 0xEF))
        state.fillPaint()
        XCTAssertTrue(LegacyKey.physical.allSatisfy { state.slots[$0.index].rgb.hex == "#ABCDEF" })
        let reopened = KeyboardState(preferences: preferences)
        XCTAssertEqual(reopened.keyPaintColor.hex, "#123456")
        XCTAssertEqual(reopened.bucketColor.hex, "#ABCDEF")
        XCTAssertEqual(reopened.lastBucketColor.hex, "#ABCDEF")
        XCTAssertTrue(reopened.isPointerSelected)
    }

    func testHexColorsRejectSignsAndNonHexCharacters() {
        for value in ["#+12345", "#-12345", "#12345G", "#12345", "#1234567"] { XCTAssertNil(RGB(hex: value), value) }

        XCTAssertEqual(RGB(hex: "#aBcDeF")?.hex, "#ABCDEF")
    }

}

private actor PaintCaptureService: LightingDeviceService {
    let image: Data
    init(image: Data) { self.image = image }

    func capture(output: URL) async throws -> DeviceSnapshot {
        DeviceSnapshot(location: "01140000", lightingData: image)
    }

    func write(snapshot: DeviceSnapshot, candidate: Data, output: URL) async throws -> DeviceSnapshot {
        throw WorkflowError.message("Paint tests must never enter the writer")
    }
}
