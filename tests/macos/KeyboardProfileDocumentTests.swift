import XCTest

@testable import KeyboardLights

final class KeyboardProfileDocumentTests: XCTestCase {
    private func profile() -> KeyboardProfile {
        KeyboardProfile(
            name: "Combined",
            draft: LightingProfileDraft(
                style: .solid, solidColor: RGB(red: 255, green: 0, blue: 0), brightness: 8, mode: .breath,
                animationColor: RGB(red: 0, green: 0, blue: 255), generated: false, speed: 5, direction: .left,
                keys: LegacyKey.physical.map { _ in LEDSlot(enabled: true, rgb: RGB(red: 1, green: 2, blue: 3)) }),
            keyAssignments: KeyAssignmentDraft())
    }

    func testStaleWindowCannotOverwriteAnotherWritersChangesOrRecreateDeletedFile() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profile.json")
        try profile().encoded().write(to: url)
        let first = try KeyboardProfileDocument.open(url)
        let second = try KeyboardProfileDocument.open(url)
        let keys = KeyAssignmentDraft(assignments: [KeyAssignment(source: 39, action: KeyAction(kind: .disabled))])
        let updated = try KeyboardProfileDocument.contents(
            updating: .assignments(keys), original: first, newCopy: false)
        let saved = try KeyboardProfileDocument.write(updated, to: url, replacing: first.data)
        XCTAssertEqual(saved.profile.draft, first.profile.draft)
        XCTAssertEqual(saved.profile.id, first.profile.id)
        XCTAssertThrowsError(try KeyboardProfileDocument.write(second.data, to: url, replacing: second.data))
        XCTAssertEqual(try Data(contentsOf: url), updated)
        try FileManager.default.removeItem(at: url)
        XCTAssertThrowsError(try KeyboardProfileDocument.write(updated, to: url, replacing: saved.data))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    func testChangedFileNeverEntersDeletionAction() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profile.json")
        try profile().encoded().write(to: url)
        let document = try KeyboardProfileDocument.open(url)
        var replacement = profile()
        replacement.name = "Someone else's changes"
        let changed = try replacement.encoded()
        try changed.write(to: url)
        var enteredDeletion = false
        XCTAssertThrowsError(
            try KeyboardProfileDocument.withUnchangedFile(at: url, expected: document.data, options: .forDeleting) {
                _ in enteredDeletion = true
            })
        XCTAssertFalse(enteredDeletion)
        XCTAssertEqual(try Data(contentsOf: url), changed)
        try KeyboardProfileDocument.withUnchangedFile(at: url, expected: changed, options: .forDeleting) { _ in
            enteredDeletion = true
        }

        XCTAssertTrue(enteredDeletion)
    }

    func testSaveAsRetainsUnsavedEditsAndOtherSectionWhenSourceIsMissingOrCorrupt() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let source = root.appendingPathComponent("source.json")
        try profile().encoded().write(to: source)
        let document = try KeyboardProfileDocument.open(source)
        let keys = KeyAssignmentDraft(assignments: [KeyAssignment(source: 39, action: KeyAction(kind: .disabled))])
        for missing in [true, false] {
            if missing {
                try FileManager.default.removeItem(at: source)
            } else {
                try Data("broken source".utf8).write(to: source)
            }

            let data = try KeyboardProfileDocument.contents(
                updating: .assignments(keys), original: document, newCopy: true)
            let target = root.appendingPathComponent("copy-\(missing).json")
            let saved = try KeyboardProfileDocument.write(data, to: target, replacing: nil)
            XCTAssertEqual(saved.profile.keyAssignments, keys)
            XCTAssertEqual(saved.profile.draft, document.profile.draft)
            XCTAssertNotEqual(saved.profile.id, document.profile.id)
        }
    }

    func testSavingLightingPreservesAssignmentsAndAllColors() throws {
        let original = profile()
        let document = KeyboardProfileDocument(
            url: URL(fileURLWithPath: "/unused"), data: try original.encoded(), profile: original)
        var draft = try XCTUnwrap(original.draft)
        draft.brightness = 4
        let baseline = Array(repeating: LEDSlot(enabled: false, rgb: RGB(red: 0, green: 0, blue: 0)), count: 144)
        let data = try KeyboardProfileDocument.contents(
            updating: .lighting(draft, baseline: baseline), original: document, newCopy: false)
        let saved = try KeyboardProfile.decodeForOpening(data)
        XCTAssertEqual(saved.draft, draft)
        XCTAssertEqual(saved.keyAssignments, original.keyAssignments)
    }

    func testNewTargetAppearingBeforeSaveIsNotOverwrittenAndInvalidDataCannotBeWritten() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("profile.json")
        let existing = try profile().encoded()
        try existing.write(to: url)
        XCTAssertThrowsError(try KeyboardProfileDocument.write(existing, to: url, replacing: nil))
        XCTAssertThrowsError(try KeyboardProfileDocument.write(Data("broken".utf8), to: url, replacing: existing))
        XCTAssertEqual(try Data(contentsOf: url), existing)
    }

    func testOtherSectionSaveCannotAcknowledgeLightingChangesThatWereNeverLoaded() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let url = root.appendingPathComponent("combined.json")
        try profile().encoded().write(to: url)
        let oldLighting = try KeyboardProfileDocument.open(url)
        var external = oldLighting.profile
        external.draft?.brightness = 3
        try external.encoded().write(to: url)
        let newerAssignments = try KeyboardProfileDocument.open(url)
        let keys = KeyAssignmentDraft(assignments: [KeyAssignment(source: 39, action: KeyAction(kind: .disabled))])
        let data = try KeyboardProfileDocument.contents(
            updating: .assignments(keys), original: newerAssignments, newCopy: false)
        let saved = try KeyboardProfileDocument.write(data, to: url, replacing: newerAssignments.data)
        let lightingRevision = oldLighting.advancingRevision(after: saved, for: .lighting)
        XCTAssertEqual(lightingRevision.data, oldLighting.data)
        XCTAssertThrowsError(
            try KeyboardProfileDocument.write(oldLighting.data, to: url, replacing: lightingRevision.data))
        XCTAssertEqual(try KeyboardProfileDocument.open(url).profile.draft?.brightness, 3)
    }

    func testOtherSectionSaveAdvancesRevisionOnlyWhenOurSavedSectionIsUnchanged() throws {
        let original = profile()
        let url = URL(fileURLWithPath: "/unused/combined.json")
        let document = KeyboardProfileDocument(url: url, data: try original.encoded(), profile: original)
        let keys = KeyAssignmentDraft(assignments: [KeyAssignment(source: 39, action: KeyAction(kind: .disabled))])
        let data = try KeyboardProfileDocument.contents(
            updating: .assignments(keys), original: document, newCopy: false)
        let saved = KeyboardProfileDocument(url: url, data: data, profile: try KeyboardProfile.decodeForOpening(data))
        XCTAssertEqual(document.advancingRevision(after: saved, for: .lighting).data, saved.data)
        XCTAssertEqual(document.advancingRevision(after: saved, for: .assignments).data, document.data)
        var changed = original
        changed.draft?.brightness = 2
        let lightingSave = KeyboardProfileDocument(url: url, data: try changed.encoded(), profile: changed)
        XCTAssertEqual(document.advancingRevision(after: lightingSave, for: .assignments).data, lightingSave.data)
        XCTAssertEqual(document.advancingRevision(after: lightingSave, for: .lighting).data, document.data)
    }

    func testLightingSavePreservesNamedProfileMetadataAndSaveAsChangesItsIdentity() throws {
        var original = profile()
        original.keyAssignments = nil
        original.name = "Night work"
        let document = KeyboardProfileDocument(
            url: URL(fileURLWithPath: "/unused/lighting.json"), data: try original.encoded(), profile: original)
        let draft = try XCTUnwrap(original.draft)
        let baseline = Array(repeating: LEDSlot(enabled: false, rgb: RGB(red: 0, green: 0, blue: 0)), count: 144)
        for newCopy in [false, true] {
            let data = try KeyboardProfileDocument.contents(
                updating: .lighting(draft, baseline: baseline), original: document, newCopy: newCopy)
            let file = try LightingDraftFile.decode(data)
            XCTAssertEqual(file.name, original.name)
            if newCopy { XCTAssertNotEqual(file.id, original.id) } else { XCTAssertEqual(file.id, original.id) }

            let reopened = try KeyboardProfile.decodeForOpening(data)
            XCTAssertEqual(reopened.name, original.name)
            XCTAssertEqual(reopened.id, file.id)
            XCTAssertEqual(reopened.draft, draft)
        }
    }

}
