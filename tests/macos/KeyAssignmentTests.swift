import XCTest

@testable import KeyboardLights

final class KeyAssignmentTests: XCTestCase {
    func testEverySupportedCatalogActionHasTheExpectedEncoding() throws {
        XCTAssertEqual(KeyAssignmentCatalog.keyboard.count, 105)
        for kind in [KeyActionKind.keyboard, .mouse, .media] {
            for target in kind.targets {
                XCTAssertEqual(try KeyAction(kind: kind, target: target.id).encodedRecord(), target.bytes)
            }
        }
    }

    func testCapturedAssignmentDescriptionsPreserveUnknownRecords() throws {
        for kind in [KeyActionKind.keyboard, .mouse, .media] {
            for target in kind.targets { XCTAssertEqual(KeyAction.describe(record: target.bytes), target.name) }
        }

        XCTAssertEqual(KeyAction.describe(record: [2, 8, 6, 0]), "Command + C")
        XCTAssertEqual(KeyAction.describe(record: [0, 0, 0, 0]), "Factory default")
        XCTAssertEqual(KeyAction.describe(record: [5, 3, 0, 0]), "Disabled")
        XCTAssertEqual(
            KeyAction.describe(record: [255, 1, 2, 3]), "Custom assignment (preserved unless you change this key)")
        XCTAssertEqual(KeyAction.describe(record: []), "Unrecognized assignment")
    }

    func testOnKeyboardDescriptionChecksValidityAndSupportsDataSlices() throws {
        var channel = Data(repeating: 0, count: 640)
        let defaults = AssignmentSnapshot(location: 1, channels: [channel])
        XCTAssertEqual(defaults.assignmentDescription(for: 90), "Factory default")
        channel[360] = 2
        channel[362] = 4
        XCTAssertEqual(
            AssignmentSnapshot(location: 1, channels: [channel]).assignmentDescription(for: 90),
            "Inactive assignment table with unknown data")
        channel[638] = 0xAA
        channel[639] = 0x55
        let padded = Data([255]) + channel
        let sliced = AssignmentSnapshot(location: 1, channels: [padded.dropFirst()])
        XCTAssertEqual(sliced.assignmentDescription(for: 90), "A")
        XCTAssertEqual(sliced.assignmentDescription(for: 0), "Read assignments to see the current action.")
        let complete = AssignmentSnapshot(location: 1, channels: [padded.dropFirst()])
        XCTAssertEqual(try complete.validated().channels[0].startIndex, 0)
    }

    func testKnownWireEncodingsAndShortcuts() throws {
        let cases: [(KeyAction, [UInt8])] = [
            (KeyAction(kind: .keyboard, target: 4), [2, 0, 4, 0]),
            (KeyAction(kind: .keyboard, target: 6, modifiers: 8), [2, 8, 6, 0]),
            (KeyAction(kind: .keyboard, target: 0xE0), [2, 1, 0, 0]), (KeyAction(kind: .disabled), [5, 3, 0, 0]),
            (KeyAction(kind: .factoryDefault), [0, 0, 0, 0]), (KeyAction(kind: .mouse, target: 6), [1, 3, 255, 0]),
            (KeyAction(kind: .media, target: 2), [3, 226, 0, 0]), (KeyAction(kind: .media, target: 10), [3, 146, 1, 0])
        ]
        for (action, expected) in cases { XCTAssertEqual(try action.encodedRecord(), expected, action.summary) }
    }

    func testEveryPhysicalSourceMapsToItsOwnRecordAndReservedSlotsSurvive() throws {
        let entries = LegacyKey.physical.map { KeyAssignment(source: $0.index, action: KeyAction(kind: .disabled)) }

        let baseline = Data(repeating: 0, count: 640)
        let candidate = try KeyAssignmentDraft(assignments: entries).candidate(from: baseline)
        XCTAssertEqual(entries.count, 115)
        for index in 0..<144 {
            XCTAssertEqual(
                Array(candidate[(index * 4)..<(index * 4 + 4)]),
                LegacyKey.physicalIndices.contains(index) ? [5, 3, 0, 0] : [0, 0, 0, 0])
        }

        for modifier in AssignmentModifier.allCases {
            XCTAssertEqual(
                try KeyAction(kind: .keyboard, target: 4, modifiers: modifier.rawValue).encodedRecord(),
                [2, modifier.rawValue, 4, 0])
        }

        XCTAssertEqual(try KeyAction(kind: .keyboard, target: 4, modifiers: 255).encodedRecord(), [2, 255, 4, 0])
    }

    func testInvalidActionsAndDraftsAreRejected() throws {
        for action in [
            KeyAction(kind: .keyboard, target: 255), KeyAction(kind: .mouse, target: 7),
            KeyAction(kind: .media, target: -1), KeyAction(kind: .keyboard), KeyAction(kind: .disabled, target: 4),
            KeyAction(kind: .factoryDefault, modifiers: 1), KeyAction(kind: .mouse, target: 0, modifiers: 1)
        ] { XCTAssertThrowsError(try action.encodedRecord()) }

        let entry = KeyAssignment(source: 39, action: KeyAction(kind: .disabled))
        XCTAssertThrowsError(try KeyAssignmentDraft(assignments: [entry, entry]).validate())
        XCTAssertThrowsError(
            try KeyAssignmentDraft(assignments: [KeyAssignment(source: 0, action: entry.action)]).validate())
        XCTAssertThrowsError(
            try KeyAssignmentDraft(assignments: [KeyAssignment(source: -1, action: entry.action)]).validate())
        var draft = KeyAssignmentDraft(assignments: [entry])
        XCTAssertThrowsError(try draft.set(entry.action, for: 144))
        XCTAssertEqual(draft.assignments, [entry])
    }

    func testOfflineCandidatePreservesUnmentionedKeysAndMetadata() throws {
        var baseline = Data((0..<640).map { UInt8($0 % 256) })
        baseline[636] = 0
        baseline[637] = 0
        baseline[638] = 0xAA
        baseline[639] = 0x55
        var draft = KeyAssignmentDraft()
        try draft.set(KeyAction(kind: .keyboard, target: 4), for: 39)
        try draft.set(KeyAction(kind: .factoryDefault), for: 90)
        let candidate = try draft.candidate(from: baseline)
        XCTAssertEqual(Array(candidate[156..<160]), [2, 0, 4, 0])
        XCTAssertEqual(Array(candidate[360..<364]), [0, 0, 0, 0])
        let changedOffsets = Set((156..<160).map { $0 } + (360..<364).map { $0 } + [638, 639])
        for offset in 0..<640 where !changedOffsets.contains(offset) {
            XCTAssertEqual(candidate[offset], baseline[offset])
        }

        XCTAssertEqual(Array(candidate[638..<640]), [0xAA, 0x55])
        XCTAssertThrowsError(try draft.candidate(from: Data(repeating: 0, count: 639)))
        baseline[638] = 1
        XCTAssertThrowsError(try draft.candidate(from: baseline))
        try draft.set(nil, for: 39)
        XCTAssertEqual(draft.assignments.map(\.source), [90])
    }

    @MainActor func testOfflineEditorAndAssignmentOnlyProfileRoundTrip() throws {
        let editor = KeyAssignmentEditor()
        try editor.set(KeyAction(kind: .keyboard, target: 6, modifiers: 8), for: 90)
        let profile = KeyboardProfile(name: "Work", keyAssignments: editor.draft)
        let restored = try KeyboardProfile.decode(profile.encoded())
        XCTAssertNil(restored.draft)
        XCTAssertEqual(restored.keyAssignments, editor.draft)
        editor.clear()
        try editor.load(XCTUnwrap(restored.keyAssignments))
        XCTAssertEqual(editor.draft.assignments.first?.action.summary, "Command + C")
        let plan = try XCTUnwrap(JSONSerialization.jsonObject(with: editor.draft.offlinePlan()) as? [String: Any])
        XCTAssertEqual(plan["hardwareValidated"] as? Bool, false)
        XCTAssertEqual(plan["channel"] as? Int, 0)
        XCTAssertNil(plan["commands"])
        XCTAssertThrowsError(try KeyboardProfile(name: "Empty").encoded())
    }

    @MainActor func testEditorChangesAreImmediatelyAvailableForSavingAndSurviveSelection() throws {
        let editor = KeyAssignmentEditor()
        try editor.select(90)
        try editor.changeKind(.keyboard)
        try editor.changeTarget(6)
        try editor.setModifier(.command, enabled: true)
        let saved = try KeyboardProfile.decode(
            KeyboardProfile(name: "Immediate", keyAssignments: editor.draft).encoded())
        XCTAssertEqual(
            saved.keyAssignments?.assignments.first?.action, KeyAction(kind: .keyboard, target: 6, modifiers: 8))
        try editor.select(39)
        try editor.changeKind(.disabled)
        try editor.select(90)
        try editor.set(nil, for: 39)
        XCTAssertEqual(editor.selectedAction, KeyAction(kind: .keyboard, target: 6, modifiers: 8))
        try editor.changeKind(.mouse)
        XCTAssertEqual(editor.selectedAction, KeyAction(kind: .mouse, target: 0))
        try editor.changeKind(.media)
        XCTAssertEqual(editor.selectedAction, KeyAction(kind: .media, target: 0))
        try editor.changeKind(.factoryDefault)
        XCTAssertEqual(editor.selectedAction, KeyAction(kind: .factoryDefault))
        try editor.changeKind(nil)
        XCTAssertTrue(editor.draft.assignments.isEmpty)
        XCTAssertNil(editor.selectedAction)
    }

    @MainActor func testInvalidEditorOperationsDoNotMutateTheDraftOrSelection() throws {
        let editor = KeyAssignmentEditor()
        try editor.changeKind(.media)
        let original = editor.draft
        XCTAssertThrowsError(try editor.select(0))
        XCTAssertThrowsError(try editor.set(nil, for: 0))
        XCTAssertThrowsError(try editor.changeTarget(99))
        XCTAssertThrowsError(try editor.setModifier(.command, enabled: true))
        XCTAssertThrowsError(
            try editor.load(
                KeyAssignmentDraft(assignments: [KeyAssignment(source: -1, action: KeyAction(kind: .disabled))])))
        XCTAssertEqual(editor.draft, original)
        XCTAssertEqual(editor.selectedSource, 39)
        editor.clear()
        XCTAssertThrowsError(try editor.changeTarget(4))
        XCTAssertTrue(editor.draft.assignments.isEmpty)
    }

    @MainActor func testIntrinsicModifierIsShownAndNotDuplicatedInSummary() throws {
        let editor = KeyAssignmentEditor()
        try editor.changeKind(.keyboard)
        try editor.changeTarget(0xE0)
        XCTAssertEqual(editor.selectedAction?.effectiveModifiers, 1)
        XCTAssertEqual(editor.selectedAction?.summary, "Left Ctrl")
        try editor.setModifier(.control, enabled: false)
        XCTAssertEqual(editor.selectedAction?.effectiveModifiers, 1)
        try editor.setModifier(.rightCommand, enabled: true)
        XCTAssertEqual(editor.selectedAction?.effectiveModifiers, 129)
        XCTAssertEqual(try editor.selectedAction?.encodedRecord(), [2, 129, 0, 0])
        XCTAssertEqual(KeyAction(kind: .keyboard, target: 0xE0, modifiers: 1).summary, "Left Ctrl")
        try editor.changeTarget(4)
        XCTAssertEqual(try editor.selectedAction?.encodedRecord(), [2, 128, 4, 0])
    }

    func testOfflineCandidateNormalizesDataSlices() throws {
        let backing = Data(repeating: 0, count: 650)
        let slice = backing[10..<650]
        XCTAssertEqual(slice.startIndex, 10)
        let draft = KeyAssignmentDraft(assignments: [KeyAssignment(source: 1, action: KeyAction(kind: .disabled))])
        let candidate = try draft.candidate(from: slice)
        XCTAssertEqual(candidate.startIndex, 0)
        XCTAssertEqual(Array(candidate[4..<8]), [5, 3, 0, 0])
        XCTAssertEqual(Array(candidate[638..<640]), [0xAA, 0x55])
    }

    func testOfflinePlanIncludesEveryCandidateMutation() throws {
        var baseline = Data((0..<640).map { UInt8($0 % 256) })
        baseline[636] = 0
        baseline[637] = 0
        baseline[638] = 0xAA
        baseline[639] = 0x55
        let draft = KeyAssignmentDraft(assignments: [
            KeyAssignment(source: 39, action: KeyAction(kind: .keyboard, target: 4))
        ])
        struct Plan: Decodable {
            struct Patch: Decodable {
                let offset: Int
                let bytes: [UInt8]
            }

            let patches: [Patch]
            let trailerOffset: Int
            let trailerBytes: [UInt8]
        }

        let plan = try JSONDecoder().decode(Plan.self, from: draft.offlinePlan())
        var reconstructed = baseline
        for patch in plan.patches {
            reconstructed.replaceSubrange(patch.offset..<(patch.offset + patch.bytes.count), with: patch.bytes)
        }

        reconstructed.replaceSubrange(
            plan.trailerOffset..<(plan.trailerOffset + plan.trailerBytes.count), with: plan.trailerBytes)
        XCTAssertEqual(reconstructed, try draft.candidate(from: baseline))
        XCTAssertEqual(plan.trailerOffset, 638)
        XCTAssertEqual(plan.trailerBytes, [0xAA, 0x55])
    }

    func testFirmwareValidityWordRejectsUnknownPrefixBytes() throws {
        let draft = KeyAssignmentDraft(assignments: [
            KeyAssignment(source: 90, action: KeyAction(kind: .keyboard, target: 4))
        ])
        for offset in [636, 637] {
            var baseline = Data(repeating: 0, count: 640)
            baseline[offset] = 1
            XCTAssertThrowsError(try draft.candidate(from: baseline))
        }

        let candidate = try draft.candidate(from: Data(repeating: 0, count: 640))
        XCTAssertEqual(Array(candidate[636..<640]), [0, 0, 0xAA, 0x55])
    }

    @MainActor func testReselectingActionCategoryKeepsTargetAndModifiers() throws {
        let editor = KeyAssignmentEditor()
        try editor.changeKind(.keyboard)
        try editor.changeTarget(6)
        try editor.setModifier(.command, enabled: true)
        let draft = editor.draft
        try editor.changeKind(.keyboard)
        XCTAssertEqual(editor.draft, draft)
        try editor.changeKind(.media)
        try editor.changeTarget(5)
        let media = editor.draft
        try editor.changeKind(.media)
        XCTAssertEqual(editor.draft, media)
    }
}
