import XCTest

@testable import KeyboardLights

final class LightingDraftFileTests: XCTestCase {
    func testLinuxDraftImportsPhysicalKeysAndPreservesReservedCaptureSlots() throws {
        let data = try fixture()
        let draft = try LightingDraftFile.decode(data).lightingDraft()
        XCTAssertEqual(draft.style, .perKey)
        XCTAssertEqual(draft.mode, .rolling)
        XCTAssertEqual(draft.direction, .left)
        XCTAssertEqual(draft.solidColor, RGB(red: 18, green: 52, blue: 86))
        let baseline = Array(repeating: LEDSlot(enabled: false, rgb: RGB(red: 1, green: 2, blue: 3)), count: 144)
        let merged = try draft.mergingKeys(into: baseline)
        for index in merged.indices {
            XCTAssertEqual(
                merged[index],
                LegacyKey.physicalIndices.contains(index)
                    ? LEDSlot(enabled: true, rgb: RGB(red: 171, green: 205, blue: 239)) : baseline[index])
        }
    }

    func testMacExportUsesLinuxWireFormatAndRoundTripsAllStyles() throws {
        for style in ["solid", "animation", "perKey"] {
            let draft = try LightingDraftFile.decode(fixture(style: style)).lightingDraft()
            let baseline = Array(repeating: LEDSlot(enabled: false, rgb: RGB(red: 9, green: 8, blue: 7)), count: 144)
            let data = try LightingDraftFile(draft: draft, baseline: baseline).encoded()
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(json["format"] as? String, "c989-lighting-draft-v1")
            let settings = try XCTUnwrap(json["settings"] as? [String: Any])
            XCTAssertEqual(settings["style"] as? String, style)
            XCTAssertEqual(settings["color"] as? String, "#123456")
            XCTAssertEqual((settings["slots"] as? [[String: Any]])?.count, 144)
            XCTAssertEqual(try LightingDraftFile.decode(data).lightingDraft(), draft)
        }
    }

    func testMalformedLinuxDraftsAreRejected() throws {
        for (key, value) in [
            ("style", "unknown" as Any), ("color", "#12GG56"), ("brightness", 17), ("mode", 6), ("speed", 1),
            ("direction", 2), ("generated", 1), ("slots", [])
        ] { XCTAssertThrowsError(try LightingDraftFile.decode(fixture(replacing: key, with: value))) }

        XCTAssertThrowsError(
            try LightingDraftFile.decode(fixture(style: "animation", replacing: "brightness", with: 0)))
        XCTAssertThrowsError(try LightingDraftFile.decode(Data(repeating: 32, count: 128_001)))
    }

    func testSavingLightingRetainsSeparateSolidAndAnimationColorsInEveryStyle() throws {
        for style in ["solid", "animation", "perKey"] {
            var draft = try LightingDraftFile.decode(fixture(style: style)).lightingDraft()
            draft.solidColor = RGB(red: 255, green: 0, blue: 0)
            draft.animationColor = RGB(red: 0, green: 0, blue: 255)
            let baseline = Array(repeating: LEDSlot(enabled: false, rgb: RGB(red: 0, green: 0, blue: 0)), count: 144)
            let data = try LightingDraftFile(draft: draft, baseline: baseline).encoded()
            XCTAssertEqual(try LightingDraftFile.decode(data).lightingDraft(), draft)
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let settings = try XCTUnwrap(json["settings"] as? [String: Any])

            // Linux reads its existing primary color field and ignores the optional editor colors.
            XCTAssertEqual(settings["color"] as? String, style == "animation" ? "#0000FF" : "#FF0000")
        }
    }

    func testLinuxEditsToPrimaryColorTakePrecedenceOverStoredInactiveColors() throws {
        for style in ["solid", "animation"] {
            var json = try XCTUnwrap(JSONSerialization.jsonObject(with: fixture(style: style)) as? [String: Any])
            var settings = try XCTUnwrap(json["settings"] as? [String: Any])
            settings["solidColor"] = "#FF0000"
            settings["animationColor"] = "#0000FF"
            settings["color"] = "#00FF00"
            json["settings"] = settings
            let draft = try LightingDraftFile.decode(JSONSerialization.data(withJSONObject: json)).lightingDraft()
            XCTAssertEqual(style == "solid" ? draft.solidColor : draft.animationColor, RGB(red: 0, green: 255, blue: 0))
        }
    }

    func testNamedLinuxMetadataIsValidatedRatherThanSilentlyDropped() throws {
        let original = try XCTUnwrap(JSONSerialization.jsonObject(with: fixture()) as? [String: Any])
        let identifier = UUID()
        var named = original
        named["name"] = "Linux evening"
        named["id"] = identifier.uuidString
        let profile = try KeyboardProfile.decodeForOpening(JSONSerialization.data(withJSONObject: named))
        XCTAssertEqual(profile.name, "Linux evening")
        XCTAssertEqual(profile.id, identifier)
        for (key, value) in [
            ("name", "" as Any), ("name", "bad\nname"), ("id", "invalid"), ("name", NSNull()), ("id", NSNull())
        ] {
            var invalid = named
            invalid[key] = value
            XCTAssertThrowsError(try KeyboardProfile.decodeForOpening(JSONSerialization.data(withJSONObject: invalid)))
        }

        for key in ["id", "name"] {
            var invalid = named
            invalid.removeValue(forKey: key)
            XCTAssertThrowsError(try KeyboardProfile.decodeForOpening(JSONSerialization.data(withJSONObject: invalid)))
        }
    }

    private func fixture(style: String = "perKey", replacing key: String? = nil, with value: Any = 0) throws -> Data {
        var settings: [String: Any] = [
            "style": style, "color": "#123456", "brightness": 8, "mode": 11, "speed": 5, "generated": false,
            "direction": 1,
            "slots": Array(repeating: ["enabled": true, "color": "#ABCDEF"] as [String: Any], count: 144)
        ]
        if let key { settings[key] = value }

        return try JSONSerialization.data(withJSONObject: ["format": "c989-lighting-draft-v1", "settings": settings])
    }
}
