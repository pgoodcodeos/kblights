import XCTest

@testable import KeyboardLights

final class KeyboardProfileWorkflowTests: XCTestCase {
    private func profile() -> KeyboardProfile {
        KeyboardProfile(
            name: "Shared setup",
            draft: LightingProfileDraft(
                style: .solid, solidColor: RGB(red: 255, green: 0, blue: 0), brightness: 8, mode: .breath,
                animationColor: RGB(red: 0, green: 0, blue: 255), generated: false, speed: 5, direction: .left,
                keys: LegacyKey.physical.map { _ in LEDSlot(enabled: true, rgb: RGB(red: 1, green: 2, blue: 3)) }),
            keyAssignments: KeyAssignmentDraft())
    }

    @MainActor func testCancelAndConfirmLoadKeepTheDocumentAndDraftTogether() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        let files = KeyboardProfileFiles(root: root)
        let editor = KeyAssignmentEditor()
        let workflow = KeyboardProfileWorkflow(files: files, state: rig.session.state, editor: editor)
        let url = root.appendingPathComponent("Assignments.json")
        let replacement = KeyAssignmentDraft(assignments: [
            KeyAssignment(source: 39, action: KeyAction(kind: .disabled))
        ])
        try KeyboardProfile(name: "Assignments", keyAssignments: replacement).encoded().write(to: url)
        try editor.set(KeyAction(kind: .factoryDefault), for: 90)
        let staged = editor.draft
        workflow.open(url, section: .assignments)
        XCTAssertNotNil(workflow.pendingLoad)
        XCTAssertEqual(editor.draft, staged)
        XCTAssertNil(workflow.document(for: .assignments))
        workflow.cancelLoad()
        XCTAssertNil(workflow.pendingLoad)
        XCTAssertEqual(editor.draft, staged)
        XCTAssertTrue(files.entries.isEmpty)
        workflow.open(url, section: .assignments)
        workflow.confirmLoad()
        XCTAssertNil(workflow.pendingLoad)
        XCTAssertEqual(editor.draft, replacement)
        XCTAssertEqual(workflow.document(for: .assignments)?.url, url.standardizedFileURL)
        XCTAssertEqual(files.entries.map(\.url), [url.standardizedFileURL])
        XCTAssertNil(workflow.error)
    }

    @MainActor func testNewProfilesConfirmDiscardAndDetachOnlyTheirOwnDocument() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let files = KeyboardProfileFiles(root: root)
        let editor = KeyAssignmentEditor()
        let workflow = KeyboardProfileWorkflow(files: files, state: rig.session.state, editor: editor)
        let url = root.appendingPathComponent("Combined.json")
        try profile().encoded().write(to: url)
        workflow.open(url, section: .assignments)
        workflow.open(url, section: .lighting)
        XCTAssertFalse(workflow.hasUnsavedChanges(.lighting))
        try editor.set(KeyAction(kind: .disabled), for: 39)
        workflow.newProfile(.assignments)
        XCTAssertEqual(workflow.pendingNew, .assignments)
        workflow.cancelLoad()
        XCTAssertFalse(editor.draft.assignments.isEmpty)
        workflow.newProfile(.assignments)
        workflow.confirmLoad()
        XCTAssertTrue(editor.draft.assignments.isEmpty)
        XCTAssertNil(workflow.document(for: .assignments))
        XCTAssertNotNil(workflow.document(for: .lighting))
        rig.session.state.brightness = 3
        workflow.newProfile(.lighting)
        XCTAssertEqual(workflow.pendingNew, .lighting)
        workflow.confirmLoad()
        XCTAssertNil(workflow.document(for: .lighting))
        XCTAssertEqual(rig.session.state.unpaintedKeys, LegacyKey.physicalIndices)
        XCTAssertFalse(workflow.canSave(.lighting))
        XCTAssertEqual(try KeyboardProfile.readForOpening(from: url).draft?.brightness, 8)
        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
    }

    @MainActor func testSavingAnimationKeepsHiddenBlanksAndBrightnessProtected() async throws {
        for blanks in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let rig = SessionFixture(root: root)
            await rig.session.start()
            let state = rig.session.state
            let workflow = KeyboardProfileWorkflow(
                files: KeyboardProfileFiles(root: root), state: state, editor: KeyAssignmentEditor())
            state.selectLightingMode(128)
            if blanks { state.clearPaint() } else { state.brightness = 3 }

            state.selectLightingMode(11)
            workflow.save(.lighting, to: root.appendingPathComponent("Animation.json"))
            XCTAssertNil(workflow.error)
            XCTAssertTrue(workflow.hasUnsavedChanges(.lighting))
            workflow.newProfile(.lighting)
            XCTAssertEqual(workflow.pendingNew, .lighting)
            workflow.cancelLoad()
            state.selectLightingMode(128)
            if blanks { XCTAssertTrue(state.hasUnpaintedKeys) } else { XCTAssertEqual(state.brightness, 3) }
        }
    }

    @MainActor func testExternalRemovalOrReplacementProtectsTheLastOpenedDraft() async throws {
        for removed in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let rig = SessionFixture(root: root)
            await rig.session.start()
            let editor = KeyAssignmentEditor()
            let workflow = KeyboardProfileWorkflow(
                files: KeyboardProfileFiles(root: root), state: rig.session.state, editor: editor)
            let source = root.appendingPathComponent("Source.json")
            var original = profile()
            original.keyAssignments = KeyAssignmentDraft(assignments: [
                KeyAssignment(source: 39, action: KeyAction(kind: .disabled))
            ])
            try original.encoded().write(to: source)
            workflow.open(source, section: .assignments)
            workflow.open(source, section: .lighting)
            XCTAssertFalse(workflow.hasUnsavedChanges(.assignments))
            XCTAssertFalse(workflow.hasUnsavedChanges(.lighting))
            if removed {
                try FileManager.default.removeItem(at: source)
            } else {
                try Data("external replacement".utf8).write(to: source)
            }

            for section in [KeyboardProfileDocument.Section.assignments, .lighting] {
                XCTAssertTrue(workflow.hasUnsavedChanges(section))
                workflow.newProfile(section)
                XCTAssertEqual(workflow.pendingNew, section)
                workflow.cancelLoad()
                XCTAssertEqual(editor.draft, original.keyAssignments)
                XCTAssertEqual(try rig.session.state.savedLightingDraft(), original.draft)
            }
        }
    }

    @MainActor func testPendingConfirmationBlocksOtherFileCommandsAndPreservesItsTarget() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        let editor = KeyAssignmentEditor()
        let workflow = KeyboardProfileWorkflow(
            files: KeyboardProfileFiles(root: root), state: rig.session.state, editor: editor)
        let first = root.appendingPathComponent("First.json")
        let second = root.appendingPathComponent("Second.json")
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 39, action: KeyAction(kind: .disabled))])
        try KeyboardProfile(name: "First", keyAssignments: draft).encoded().write(to: first)
        try KeyboardProfile(name: "Second", keyAssignments: KeyAssignmentDraft()).encoded().write(to: second)
        try editor.set(KeyAction(kind: .factoryDefault), for: 90)
        workflow.open(first, section: .assignments)
        XCTAssertFalse(workflow.isReady)
        let actions = KeyboardProfileActions(
            workflow: workflow, enabled: true, canLoadLighting: false, canSaveLighting: false)
        XCTAssertFalse(actions.isReady)
        workflow.open(second, section: .assignments)
        workflow.newProfile(.assignments)
        workflow.requestDeletion(.assignments)
        let forbidden = root.appendingPathComponent("MustNotBeSaved.json")
        workflow.save(.assignments, to: forbidden)
        XCTAssertFalse(FileManager.default.fileExists(atPath: forbidden.path))
        XCTAssertNil(workflow.pendingNew)
        XCTAssertNil(workflow.pendingDeletion)
        XCTAssertEqual(workflow.pendingLoad?.document.url, first)
        workflow.confirmLoad()
        XCTAssertEqual(editor.draft, draft)
        XCTAssertTrue(workflow.isReady)
    }

    @MainActor func testAvailabilityIsRecheckedBeforeConfirmingNewLoadOrDeletion() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        let editor = KeyAssignmentEditor()
        var available = true
        let workflow = KeyboardProfileWorkflow(
            files: KeyboardProfileFiles(root: root), state: rig.session.state, editor: editor,
            isAvailable: { available })
        let source = root.appendingPathComponent("Source.json")
        try KeyboardProfile(name: "Source", keyAssignments: KeyAssignmentDraft()).encoded().write(to: source)
        workflow.open(source, section: .assignments)
        try editor.set(KeyAction(kind: .disabled), for: 39)
        let staged = editor.draft
        for startsNew in [false, true] {
            available = true
            if startsNew { workflow.newProfile(.assignments) } else { workflow.open(source, section: .assignments) }

            available = false
            workflow.confirmLoad()
            XCTAssertEqual(editor.draft, staged)
            XCTAssertNil(workflow.pendingLoad)
            XCTAssertNil(workflow.pendingNew)
            XCTAssertNotNil(workflow.error)
        }

        available = true
        workflow.requestDeletion(.assignments)
        XCTAssertNotNil(workflow.pendingDeletion)
        available = false
        workflow.confirmDeletion()
        XCTAssertNil(workflow.pendingDeletion)
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path))
        XCTAssertEqual(editor.draft, staged)
        XCTAssertNotNil(workflow.document(for: .assignments))
    }

    @MainActor func testSeparateSavesPreserveBothSectionsWithoutDeviceOperations() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let files = KeyboardProfileFiles(root: root)
        let editor = KeyAssignmentEditor()
        let workflow = KeyboardProfileWorkflow(files: files, state: rig.session.state, editor: editor)
        let url = root.appendingPathComponent("Combined.json")
        try profile().encoded().write(to: url)
        workflow.open(url, section: .assignments)
        workflow.open(url, section: .lighting)
        try editor.set(KeyAction(kind: .disabled), for: 39)
        workflow.save(.assignments, to: url)
        XCTAssertNil(workflow.error)
        rig.session.state.brightness = 9
        workflow.save(.lighting, to: url)
        XCTAssertNil(workflow.error)
        let saved = try KeyboardProfile.readForOpening(from: url)
        XCTAssertEqual(saved.draft?.brightness, 9)
        XCTAssertEqual(saved.keyAssignments, editor.draft)
        XCTAssertEqual(workflow.document(for: .assignments)?.data, workflow.document(for: .lighting)?.data)
        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
    }

    @MainActor func testAssignmentSaveDoesNotHideAnUnloadedLightingConflict() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let files = KeyboardProfileFiles(root: root)
        let editor = KeyAssignmentEditor()
        let workflow = KeyboardProfileWorkflow(files: files, state: rig.session.state, editor: editor)
        let url = root.appendingPathComponent("Combined.json")
        var original = profile()
        try original.encoded().write(to: url)
        workflow.open(url, section: .lighting)
        original.draft?.brightness = 3
        try original.encoded().write(to: url)
        workflow.open(url, section: .assignments)
        try editor.set(KeyAction(kind: .disabled), for: 39)
        workflow.save(.assignments, to: url)
        XCTAssertNil(workflow.error)
        let savedAssignments = try Data(contentsOf: url)
        workflow.save(.lighting, to: url)
        XCTAssertNotNil(workflow.error)
        XCTAssertEqual(try Data(contentsOf: url), savedAssignments)
        XCTAssertEqual(rig.session.state.brightness, 8)
        XCTAssertEqual(try KeyboardProfile.readForOpening(from: url).draft?.brightness, 3)
    }

    @MainActor func testSaveAsCanRecoverLightingWhenItsOriginalFileWasRemoved() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let files = KeyboardProfileFiles(root: root)
        let workflow = KeyboardProfileWorkflow(files: files, state: rig.session.state, editor: KeyAssignmentEditor())
        let source = root.appendingPathComponent("Original.json")
        let copy = root.appendingPathComponent("Copy.json")
        let original = profile()
        try original.encoded().write(to: source)
        workflow.open(source, section: .lighting)
        try FileManager.default.removeItem(at: source)
        rig.session.state.brightness = 11
        workflow.save(.lighting, to: copy)
        XCTAssertNil(workflow.error)
        let saved = try KeyboardProfile.readForOpening(from: copy)
        XCTAssertEqual(saved.draft?.brightness, 11)
        XCTAssertEqual(saved.keyAssignments, original.keyAssignments)
        XCTAssertNotEqual(saved.id, original.id)
        XCTAssertEqual(workflow.document(for: .lighting)?.url, copy.standardizedFileURL)
    }

    @MainActor func testWrongSectionIsRejectedBeforeChangingOrRememberingTheDraft() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        let files = KeyboardProfileFiles(root: root)
        let editor = KeyAssignmentEditor()
        let workflow = KeyboardProfileWorkflow(files: files, state: rig.session.state, editor: editor)
        let url = root.appendingPathComponent("Lighting.json")
        try KeyboardProfile(name: "Lighting", draft: profile().draft).encoded().write(to: url)
        workflow.open(url, section: .assignments)
        XCTAssertNotNil(workflow.error)
        XCTAssertNil(workflow.pendingLoad)
        XCTAssertNil(workflow.document(for: .assignments))
        XCTAssertTrue(editor.draft.assignments.isEmpty)
        XCTAssertTrue(files.entries.isEmpty)
    }
}
