import XCTest

@testable import KeyboardLights

final class KeyboardProfileFileTests: XCTestCase {
    private func lighting() -> LightingProfileDraft {
        LightingProfileDraft(
            style: .solid, solidColor: RGB(red: 1, green: 2, blue: 3), brightness: 8, mode: .breath,
            animationColor: RGB(red: 4, green: 5, blue: 6), generated: false, speed: 5, direction: .left,
            keys: LegacyKey.physical.map { _ in LEDSlot(enabled: true, rgb: RGB(red: 7, green: 8, blue: 9)) })
    }

    @MainActor func testLegacyMigrationPreservesEverySectionAndDoesNotRestoreDeletedFiles() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let legacyURL = root.appendingPathComponent("lighting-profiles.json")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let keys = KeyAssignmentDraft(assignments: [KeyAssignment(source: 39, action: KeyAction(kind: .disabled))])
        let originals = [
            KeyboardProfile(name: String(repeating: "💡", count: 80), draft: lighting()),
            KeyboardProfile(name: "Keys", keyAssignments: keys),
            KeyboardProfile(name: "Both", draft: lighting(), keyAssignments: keys)
        ]
        try JSONEncoder().encode(originals).write(to: legacyURL)

        let originalBytes = try Data(contentsOf: legacyURL)
        let files = KeyboardProfileFiles(root: root)
        XCTAssertNil(files.error)
        XCTAssertEqual(files.entries.count, 3)
        for original in originals {
            let migrated = try XCTUnwrap(files.entries.first { $0.profile.id == original.id })
            XCTAssertEqual(migrated.profile.draft, original.draft)
            XCTAssertEqual(migrated.profile.keyAssignments, original.keyAssignments)
        }

        let deleted = try XCTUnwrap(files.entries.first).url
        try FileManager.default.removeItem(at: deleted)
        files.refresh()
        XCTAssertEqual(files.entries.count, 2)
        XCTAssertEqual(KeyboardProfileFiles(root: root).entries.count, 2)
        XCTAssertFalse(FileManager.default.fileExists(atPath: deleted.path))
        XCTAssertEqual(try Data(contentsOf: legacyURL), originalBytes)
    }

    @MainActor func testLocalFilesFollowFinderRenameAndDeletionAndDiscoverNewProfiles() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let files = KeyboardProfileFiles(root: root)
        try FileManager.default.createDirectory(at: files.directory, withIntermediateDirectories: true)
        let url = files.directory.appendingPathComponent("Evening.json")
        try KeyboardProfile(name: "Evening", draft: lighting()).encoded().write(to: url)
        files.refresh()
        XCTAssertEqual(files.entries.map(\.name), ["Evening"])
        let renamed = files.directory.appendingPathComponent("Morning.json")
        try FileManager.default.moveItem(at: url, to: renamed)
        files.refresh()
        XCTAssertEqual(files.entries.map(\.name), ["Morning"])
        try FileManager.default.removeItem(at: renamed)
        files.refresh()
        XCTAssertTrue(files.entries.isEmpty)
    }

    @MainActor func testOpenedExternalFilesAreRememberedAndRereadWithoutDuplicatingEntries() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let filesRoot = root.appendingPathComponent("App")
        let url = root.appendingPathComponent("External.json")
        var original = KeyboardProfile(name: "External", keyAssignments: KeyAssignmentDraft())
        try original.encoded().write(to: url)
        let files = KeyboardProfileFiles(root: filesRoot)
        try files.remember(url)
        try files.remember(url)
        XCTAssertEqual(files.entries.count, 1)
        original.keyAssignments = KeyAssignmentDraft(assignments: [
            KeyAssignment(source: 39, action: KeyAction(kind: .disabled))
        ])
        try original.encoded().write(to: url)
        let reloaded = KeyboardProfileFiles(root: filesRoot)
        XCTAssertEqual(reloaded.entries.first?.profile.keyAssignments, original.keyAssignments)
        try FileManager.default.removeItem(at: url)
        reloaded.refresh()
        XCTAssertTrue(reloaded.entries.isEmpty)
        try reloaded.forget(url)
        XCTAssertTrue(KeyboardProfileFiles(root: filesRoot).entries.isEmpty)
    }

    @MainActor func testCorruptFilesAreReportedWithoutHidingValidFilesOrOverwritingSource() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let files = KeyboardProfileFiles(root: root)
        try FileManager.default.createDirectory(at: files.directory, withIntermediateDirectories: true)
        let good = files.directory.appendingPathComponent("Good.json")
        let bad = files.directory.appendingPathComponent("Bad.json")
        try KeyboardProfile(name: "Good", draft: lighting()).encoded().write(to: good)
        let corrupt = Data("broken".utf8)
        try corrupt.write(to: bad)
        files.refresh()
        XCTAssertEqual(files.entries.map(\.name), ["Good"])
        XCTAssertTrue(files.error?.contains("Bad.json") == true)
        XCTAssertEqual(try Data(contentsOf: bad), corrupt)
    }

    @MainActor func testCorruptIndexDoesNotHideLocalProfilesOrGetOverwritten() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let files = KeyboardProfileFiles(root: root)
        try FileManager.default.createDirectory(at: files.directory, withIntermediateDirectories: true)
        let local = files.directory.appendingPathComponent("Local.json")
        try KeyboardProfile(name: "Local", draft: lighting()).encoded().write(to: local)
        let index = root.appendingPathComponent("profile-files.json")
        let corrupt = Data("broken index".utf8)
        try corrupt.write(to: index)
        files.refresh()
        XCTAssertEqual(files.entries.map(\.name), ["Local"])
        XCTAssertNotNil(files.error)
        try files.remember(local)
        XCTAssertEqual(try Data(contentsOf: index), corrupt)
        XCTAssertThrowsError(try files.remember(root.appendingPathComponent("External.json")))
        XCTAssertEqual(try Data(contentsOf: index), corrupt)
        try FileManager.default.removeItem(at: local)
        files.refresh()
        XCTAssertTrue(files.entries.isEmpty)
    }

    @MainActor func testMigrationRecoversLegacyProfileWithoutOverwritingConflictingFile() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let original = KeyboardProfile(name: "Lights", draft: lighting())
        try JSONEncoder().encode([original]).write(to: root.appendingPathComponent("lighting-profiles.json"))
        let directory = root.appendingPathComponent("Profiles")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let collision = directory.appendingPathComponent("Lights-\(original.id.uuidString).json")
        var edited = original
        edited.draft?.brightness = 3
        let editedData = try edited.encoded()
        try editedData.write(to: collision)
        let files = KeyboardProfileFiles(root: root)
        XCTAssertNil(files.error)
        XCTAssertEqual(files.entries.count, 2)
        XCTAssertEqual(try Data(contentsOf: collision), editedData)
        XCTAssertTrue(files.entries.contains { $0.profile.draft == original.draft })
        XCTAssertTrue(files.entries.contains { $0.profile.draft == edited.draft })
        XCTAssertEqual(KeyboardProfileFiles(root: root).entries.count, 2)
    }

    @MainActor func testUnchangedRefreshAvoidsPublishingAndStillDetectsSameSizeEdits() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let files = KeyboardProfileFiles(root: root)
        try FileManager.default.createDirectory(at: files.directory, withIntermediateDirectories: true)
        let url = files.directory.appendingPathComponent("Lights.json")
        var profile = KeyboardProfile(name: "Lights", draft: lighting())
        let initial = try profile.encoded()
        try initial.write(to: url)
        files.refresh()
        var updates = 0
        let subscription = files.$entries.dropFirst().sink { _ in updates += 1 }

        defer { subscription.cancel() }

        files.refresh()
        XCTAssertEqual(updates, 0)
        profile.draft?.brightness = 3
        let changed = try profile.encoded()
        XCTAssertEqual(changed.count, initial.count)
        try changed.write(to: url)
        files.refresh()
        XCTAssertEqual(updates, 1)
        XCTAssertEqual(files.entries.first?.profile.draft?.brightness, 3)
    }

    @MainActor func testRememberingKnownExternalFileDoesNotRewriteTheIndex() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let appRoot = root.appendingPathComponent("App")
        let files = KeyboardProfileFiles(root: appRoot)
        let external = root.appendingPathComponent("External.json")
        try KeyboardProfile(name: "External", draft: lighting()).encoded().write(to: external)
        try files.remember(external)
        let index = appRoot.appendingPathComponent("profile-files.json")
        let timestamp = Date(timeIntervalSince1970: 1_000)
        try FileManager.default.setAttributes([.modificationDate: timestamp], ofItemAtPath: index.path)
        try files.remember(external)
        let attributes = try FileManager.default.attributesOfItem(atPath: index.path)
        XCTAssertEqual(attributes[.modificationDate] as? Date, timestamp)
    }
}
