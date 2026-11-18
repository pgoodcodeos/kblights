import XCTest

@testable import KeyboardLights

final class LightingCandidateTests: XCTestCase {
    private func validImage() -> Data { makeTestLightingImage() }

    func testBuildChangesOnlySlotAndCompactSelection() throws {
        let baseline = validImage()
        var slots = try LightingCandidateBuilder.slots(from: baseline)
        slots[39] = LEDSlot(enabled: true, rgb: RGB(red: 0, green: 0, blue: 255))
        let candidate = try LightingCandidateBuilder.build(from: baseline, slots: slots, brightness: 16)
        let changed = zip(baseline, candidate).enumerated().compactMap { index, bytes in
            bytes.0 == bytes.1 ? nil : index
        }

        XCTAssertEqual(changed, [0x29C, 0x29F, 0x440, 0x449])
        XCTAssertEqual(Array(candidate[0x29C...0x29F]), [0x80, 0, 0, 0xFF])
        XCTAssertEqual(candidate[0x440], 0x80)
        XCTAssertEqual(candidate[0x449], 16)
    }

    func testRejectsInvalidRecordTrailer() {
        var data = validImage()
        data[62] = 0
        XCTAssertThrowsError(try LightingCandidateBuilder.slots(from: data))
    }

    func testRejectsInvalidSlotControl() {
        var data = validImage()
        data[0x200] = 1
        XCTAssertThrowsError(try LightingCandidateBuilder.slots(from: data))
    }

    func testRejectsBrightnessOutsideFirmwareRange() throws {
        let data = validImage()
        let slots = try LightingCandidateBuilder.slots(from: data)
        XCTAssertThrowsError(try LightingCandidateBuilder.build(from: data, slots: slots, brightness: 17))
    }

    func testPersistentStaticUsesUserDefineAndFillsOnlyPhysicalKeys() throws {
        var baseline = validImage()

        // A reserved slot must survive even when it already contains opaque,
        // structurally valid UserDefine data.
        baseline[0x200] = 0x80
        baseline[0x201] = 0x12
        baseline[0x202] = 0x34
        baseline[0x203] = 0x56

        let color = RGB(red: 0x11, green: 0x22, blue: 0x33)
        let candidate = try LightingCandidateBuilder.buildPersistentStatic(from: baseline, color: color, brightness: 8)

        XCTAssertEqual(LegacyKey.physicalIndices.count, 115)
        XCTAssertEqual(candidate[0x440], 0x80)
        XCTAssertEqual(candidate[0x449], 8)
        for index in 0..<144 {
            let offset = 0x200 + index * 4
            if LegacyKey.physicalIndices.contains(index) {
                XCTAssertEqual(
                    Array(candidate[offset..<(offset + 4)]), [0x80, 0x11, 0x22, 0x33], "physical slot \(index)")
            } else {
                XCTAssertEqual(
                    Array(candidate[offset..<(offset + 4)]), Array(baseline[offset..<(offset + 4)]),
                    "reserved slot \(index)")
            }
        }
    }

    func testCanonicalRecordForEveryEnabledAnimation() throws {
        let baseline = validImage()
        let configurable: Set<BuiltInMode> = [.breath, .volcano, .rolling, .rotating, .flowing, .pulsating, .tilt]

        for mode in BuiltInMode.allCases {
            let settings = BuiltInSettings(
                mode: mode, color: RGB(red: 0, green: 255, blue: 0), usesGeneratedColor: true, brightness: 16,
                speed: 12, direction: .right)
            let candidate = try LightingCandidateBuilder.buildBuiltIn(from: baseline, settings: settings)
            var expected = [UInt8](repeating: 0, count: 16)
            expected[0] = mode.rawValue
            expected[8] = configurable.contains(mode) ? 1 : 0
            expected[9] = 16
            expected[10] = 12
            expected[14] = 0xAA
            expected[15] = 0x55
            XCTAssertEqual(Array(candidate[0x440..<0x450]), expected, mode.name)

            let changed = zip(baseline, candidate).enumerated().compactMap { index, bytes in
                bytes.0 == bytes.1 ? nil : index
            }

            XCTAssertTrue(changed.allSatisfy { (0x440..<0x450).contains($0) }, mode.name)
            XCTAssertEqual(candidate[0x200..<0x440], baseline[0x200..<0x440], mode.name)
        }
    }

    func testSingleColorForEveryConfigurableAnimation() throws {
        let baseline = validImage()
        let modes: [BuiltInMode] = [.breath, .volcano, .rolling, .rotating, .flowing, .pulsating, .tilt]
        for mode in modes {
            let candidate = try LightingCandidateBuilder.buildBuiltIn(
                from: baseline,
                settings: BuiltInSettings(
                    mode: mode, color: RGB(red: 0x12, green: 0x34, blue: 0x56), usesGeneratedColor: false,
                    brightness: 8, speed: 2, direction: .right))
            XCTAssertEqual(Array(candidate[0x441...0x443]), [0x12, 0x34, 0x56], mode.name)
            XCTAssertEqual(candidate[0x448], 0, mode.name)
            XCTAssertEqual(candidate[0x449], 8, mode.name)
            XCTAssertEqual(candidate[0x44A], 2, mode.name)
        }
    }

    func testGeneratedPaletteModesCanonicalizeIgnoredColorFields() throws {
        let baseline = validImage()
        for mode in [BuiltInMode.colourful, .spectrum] {
            let candidate = try LightingCandidateBuilder.buildBuiltIn(
                from: baseline,
                settings: BuiltInSettings(
                    mode: mode, color: RGB(red: 0xFF, green: 0, blue: 0), usesGeneratedColor: true, brightness: 16,
                    speed: 12, direction: .left))
            XCTAssertEqual(Array(candidate[0x441...0x443]), [0, 0, 0], mode.name)
            XCTAssertEqual(candidate[0x448], 0, mode.name)
            XCTAssertEqual(candidate[0x44B], 0, mode.name)
        }
    }

    func testOnlyRollingExposesReliableDirection() throws {
        let baseline = validImage()
        for mode in BuiltInMode.allCases {
            let candidate = try LightingCandidateBuilder.buildBuiltIn(
                from: baseline,
                settings: BuiltInSettings(
                    mode: mode, color: RGB(red: 0, green: 255, blue: 0), usesGeneratedColor: true, brightness: 16,
                    speed: 12, direction: .left))
            XCTAssertEqual(candidate[0x44B], mode == .rolling ? 1 : 0, mode.name)
            XCTAssertEqual(mode.supportsDirection, mode == .rolling, mode.name)
        }
    }

    func testRejectsStoppedOrOutOfRangeAnimationControls() {
        let baseline = validImage()
        func settings(brightness: Int = 16, speed: Int = 12) -> BuiltInSettings {
            BuiltInSettings(
                mode: .breath, color: RGB(red: 0, green: 255, blue: 0), usesGeneratedColor: true,
                brightness: brightness, speed: speed, direction: .right)
        }

        XCTAssertThrowsError(
            try LightingCandidateBuilder.buildBuiltIn(from: baseline, settings: settings(brightness: 0)))
        XCTAssertThrowsError(try LightingCandidateBuilder.buildBuiltIn(from: baseline, settings: settings(speed: 1)))
        XCTAssertThrowsError(try LightingCandidateBuilder.buildBuiltIn(from: baseline, settings: settings(speed: 17)))
    }

    func testLegacyStaticPaletteMigratesToPersistentColors() {
        let expected: [RGB] = [
            RGB(red: 0xFF, green: 0xFF, blue: 0xFF), RGB(red: 0, green: 0xFF, blue: 0xFF),
            RGB(red: 0xFF, green: 0, blue: 0), RGB(red: 0xFF, green: 0xFF, blue: 0), RGB(red: 0, green: 0, blue: 0xFF),
            RGB(red: 0xFF, green: 0, blue: 0xFF)
        ]
        for tag in UInt8(1)...UInt8(6) {
            XCTAssertEqual(LightingCandidateBuilder.persistentStaticColor(forLegacyTag: tag), expected[Int(tag - 1)])
        }

        XCTAssertNil(LightingCandidateBuilder.persistentStaticColor(forLegacyTag: 7))
    }

    func testNativeLightingPolicyAcceptsEveryEditorStyleAndRejectsReservedChanges() throws {
        let baseline = validImage()
        let solid = try LightingCandidateBuilder.buildPersistentStatic(
            from: baseline, color: RGB(red: 255, green: 0, blue: 255), brightness: 16)
        try NativeLightingPolicy.validate(baseline: baseline, candidate: solid)
        for mode in BuiltInMode.allCases {
            let candidate = try LightingCandidateBuilder.buildBuiltIn(
                from: baseline,
                settings: BuiltInSettings(
                    mode: mode, color: RGB(red: 0, green: 0, blue: 0), usesGeneratedColor: false, brightness: 8,
                    speed: 12, direction: .right))
            try NativeLightingPolicy.validate(baseline: baseline, candidate: candidate)
        }

        let perKey = try LightingCandidateBuilder.build(
            from: baseline, slots: LightingCandidateBuilder.slots(from: solid), brightness: 4)
        try NativeLightingPolicy.validate(baseline: baseline, candidate: perKey)
        XCTAssertThrowsError(try NativeLightingPolicy.validate(baseline: baseline, candidate: baseline))
        XCTAssertThrowsError(try NativeLightingPolicy.validate(baseline: Data(), candidate: solid))
        for offset in [0, 62, 0x450, 0x47F] {
            var corrupt = solid
            corrupt[offset] ^= 1
            XCTAssertThrowsError(try NativeLightingPolicy.validate(baseline: baseline, candidate: corrupt))
        }
    }
}
