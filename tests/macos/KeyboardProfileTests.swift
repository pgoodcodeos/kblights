import Darwin
import XCTest

@testable import KeyboardLights

final class KeyboardProfileTests: XCTestCase {
    private func profile() -> KeyboardProfile {
        KeyboardProfile(
            name: "Evening",
            draft: LightingProfileDraft(
                style: .perKey, solidColor: RGB(red: 1, green: 2, blue: 3), brightness: 8, mode: .rolling,
                animationColor: RGB(red: 4, green: 5, blue: 6), generated: false, speed: 5, direction: .left,
                keys: LegacyKey.physical.map { _ in LEDSlot(enabled: true, rgb: RGB(red: 7, green: 8, blue: 9)) }))
    }

    func testPortableProfileRoundTripAndInvalidInputs() throws {
        let original = profile()
        let restored = try KeyboardProfile.decode(original.encoded())
        XCTAssertEqual(restored.draft, original.draft)
        XCTAssertEqual(restored.name, original.name)
        XCTAssertEqual(restored.id, original.id)
        var invalid = original
        invalid.format = "windows-prf"
        XCTAssertThrowsError(try invalid.encoded())
        invalid = original
        invalid.draft?.keys.removeLast()
        XCTAssertThrowsError(try invalid.encoded())
        invalid = original
        invalid.draft?.speed = 1
        XCTAssertThrowsError(try invalid.encoded())
        invalid = original
        invalid.draft?.style = .animation
        invalid.draft?.brightness = 0
        XCTAssertThrowsError(try invalid.encoded())
        invalid = original
        invalid.name = " \n "
        XCTAssertThrowsError(try invalid.encoded())
        XCTAssertThrowsError(try KeyboardProfile.decode(Data(repeating: 32, count: 128_001)))
    }

    func testLoadingPreservesReservedSlotsFromCurrentCapture() throws {
        let baseline = (0..<144).map { LEDSlot(enabled: false, rgb: RGB(red: UInt8($0), green: 0, blue: 0)) }

        let draft = try XCTUnwrap(profile().draft)
        let merged = try draft.mergingKeys(into: baseline)
        for index in 0..<144 {
            if let keyIndex = LegacyKey.physical.firstIndex(where: { $0.index == index }) {
                XCTAssertEqual(merged[index], draft.keys[keyIndex])
            } else {
                XCTAssertEqual(merged[index], baseline[index])
            }
        }

        XCTAssertThrowsError(try draft.mergingKeys(into: []))
    }

    @MainActor func testCorruptLegacyLibraryIsPreservedAndLightingLoadRequiresCapture() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profiles.json")
        let corrupt = Data("broken".utf8)
        try corrupt.write(to: url)
        XCTAssertThrowsError(try LegacyProfileLibrary.read(from: url))
        XCTAssertEqual(try Data(contentsOf: url), corrupt)
        let state = KeyboardState()
        XCTAssertThrowsError(try state.loadLightingProfile(profile()))
        XCTAssertThrowsError(try state.savedLightingDraft())
        XCTAssertNil(state.review)
    }

    func testBoundedFileReadsAndExportCanBeImported() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profile.json")
        let original = profile()
        try original.encoded().write(to: url)
        XCTAssertEqual(try KeyboardProfile.read(from: url).draft, original.draft)
        try Data(repeating: 32, count: 128_001).write(to: url)
        XCTAssertThrowsError(try KeyboardProfile.read(from: url))
        XCTAssertThrowsError(try KeyboardProfile.read(from: root))
        try Data(repeating: 32, count: 10).write(to: url)
        XCTAssertEqual(try ProfileFileReader.read(url, limit: 10).count, 10)
        XCTAssertThrowsError(try ProfileFileReader.read(url, limit: 9))
    }

    func testMalformedProfilePayloadsAreRejected() throws {
        let original = try XCTUnwrap(JSONSerialization.jsonObject(with: profile().encoded()) as? [String: Any])
        for (field, value) in [("format", "c989-profile-v99"), ("id", "bad-uuid"), ("name", "two\nlines")] {
            var modified = original
            modified[field] = value
            XCTAssertThrowsError(try KeyboardProfile.decode(JSONSerialization.data(withJSONObject: modified)))
        }

        for field in ["format", "id", "draft"] {
            var modified = original
            modified.removeValue(forKey: field)
            XCTAssertThrowsError(try KeyboardProfile.decode(JSONSerialization.data(withJSONObject: modified)))
        }

        for (field, value) in [
            ("style", "unrecognized" as Any), ("mode", 255), ("brightness", 17), ("direction", 3),
            ("solidColor", ["red": -1, "green": 0, "blue": 0])
        ] {
            var modified = original
            var draft = try XCTUnwrap(modified["draft"] as? [String: Any])
            draft[field] = value
            modified["draft"] = draft
            XCTAssertThrowsError(try KeyboardProfile.decode(JSONSerialization.data(withJSONObject: modified)))
        }

        var oversizedName = profile()

        // Character count alone does not bound a name's encoded size.
        oversizedName.name = "a" + String(repeating: "\u{0301}", count: 1000)
        XCTAssertThrowsError(try oversizedName.encoded())
    }

    @MainActor func testDuplicateIdentifiersAndFutureLegacyLibraryFormatsAreRejected() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profiles.json")
        let original = profile()
        var future = original
        future.format = "c989-profile-v99"
        for entries in [[original, original], [future]] {
            let bytes = try JSONEncoder().encode(entries)
            try bytes.write(to: url)
            XCTAssertThrowsError(try LegacyProfileLibrary.read(from: url))
            XCTAssertEqual(try Data(contentsOf: url), bytes)
        }
    }

    @MainActor func testLegacyLightingProfileMigratesWithoutLosingData() throws {
        let original = profile()
        var legacy = try XCTUnwrap(JSONSerialization.jsonObject(with: original.encoded()) as? [String: Any])
        legacy["format"] = "c989-lighting-profile-v1"
        let bytes = try JSONSerialization.data(withJSONObject: legacy)
        let migrated = try KeyboardProfile.decode(bytes)
        XCTAssertEqual(migrated.format, "c989-profile-v2")
        XCTAssertEqual(migrated.id, original.id)
        XCTAssertEqual(migrated.draft, original.draft)
        XCTAssertNil(migrated.keyAssignments)
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profiles.json")
        try JSONSerialization.data(withJSONObject: [legacy]).write(to: url)
        let restored = try LegacyProfileLibrary.read(from: url)
        XCTAssertEqual(restored.first?.draft, original.draft)
        XCTAssertEqual(restored.first?.id, original.id)
    }

    func testMalformedAssignmentProfilesAndLegacyFormatSmugglingAreRejected() throws {
        let assignment = KeyAssignment(source: 39, action: KeyAction(kind: .disabled))
        var combined = profile()
        combined.keyAssignments = KeyAssignmentDraft(assignments: [assignment])
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: combined.encoded()) as? [String: Any])
        json["format"] = "c989-lighting-profile-v1"
        XCTAssertThrowsError(try KeyboardProfile.decode(JSONSerialization.data(withJSONObject: json)))
        combined.keyAssignments = KeyAssignmentDraft(assignments: [assignment, assignment])
        XCTAssertThrowsError(try combined.encoded())
        json["format"] = "c989-profile-v2"
        json["keyAssignments"] = ["assignments": [["source": 0, "action": ["kind": "disabled", "modifiers": 0]]]]
        XCTAssertThrowsError(try KeyboardProfile.decode(JSONSerialization.data(withJSONObject: json)))
    }

    @MainActor func testOpeningLinuxLightingDraftCanSaveAsPortableProfile() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let draft = try XCTUnwrap(profile().draft)
        let baseline = Array(repeating: LEDSlot(enabled: false, rgb: RGB(red: 0, green: 0, blue: 0)), count: 144)
        let file = root.appendingPathComponent("linux-lighting.json")
        try LightingDraftFile(draft: draft, baseline: baseline).encoded().write(to: file)
        let opened = try KeyboardProfile.readForOpening(from: file)
        XCTAssertEqual(opened.draft?.keys, draft.keys)
        XCTAssertEqual(opened.draft?.style, draft.style)
        XCTAssertNil(opened.keyAssignments)
        let saved = try KeyboardProfileDocument.write(
            opened.encoded(), to: root.appendingPathComponent("portable-profile.json"), replacing: nil)
        XCTAssertEqual(saved.profile.draft, opened.draft)
        XCTAssertThrowsError(try KeyboardProfile.decodeForOpening(Data(repeating: 32, count: 128_001)))
    }

    func testProfileReaderRejectsNonregularFilesAndSymbolicLinksWithoutBlocking() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let fifo = root.appendingPathComponent("pipe.json")
        XCTAssertEqual(mkfifo(fifo.path, mode_t(S_IRUSR | S_IWUSR)), 0)
        XCTAssertThrowsError(try ProfileFileReader.read(fifo, limit: 128_000))
        XCTAssertThrowsError(try ProfileFileReader.read(root, limit: 128_000))
        let file = root.appendingPathComponent("profile.json")
        try profile().encoded().write(to: file)
        let link = root.appendingPathComponent("link.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
        XCTAssertThrowsError(try ProfileFileReader.read(link, limit: 128_000))
        XCTAssertEqual(try KeyboardProfile.read(from: file).name, "Evening")
    }

}
