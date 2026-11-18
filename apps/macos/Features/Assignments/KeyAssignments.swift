import Combine
import Foundation

struct AssignmentTarget: Identifiable, Equatable, Sendable, Decodable {
    let id: Int
    let name: String
    let bytes: [UInt8]
}

enum KeyActionKind: String, Codable, CaseIterable, Identifiable, Sendable {
    case factoryDefault, keyboard, mouse, media, disabled
    var id: Self { self }

    var name: String {
        switch self {
        case .factoryDefault: "Factory default"
        case .keyboard: "Keyboard key / shortcut"
        case .mouse: "Mouse"
        case .media: "Media"
        case .disabled: "Disable key"
        }
    }

    var targets: [AssignmentTarget] {
        switch self {
        case .keyboard: KeyAssignmentCatalog.keyboard
        case .mouse: KeyAssignmentCatalog.mouse
        case .media: KeyAssignmentCatalog.media
        default: []
        }
    }

    private static let keyboardByID = Dictionary(
        uniqueKeysWithValues: KeyAssignmentCatalog.keyboard.map { ($0.id, $0) })
    private static let mouseByID = Dictionary(uniqueKeysWithValues: KeyAssignmentCatalog.mouse.map { ($0.id, $0) })
    private static let mediaByID = Dictionary(uniqueKeysWithValues: KeyAssignmentCatalog.media.map { ($0.id, $0) })

    func target(id: Int?) -> AssignmentTarget? {
        guard let id else { return nil }

        switch self {
        case .keyboard: return Self.keyboardByID[id]
        case .mouse: return Self.mouseByID[id]
        case .media: return Self.mediaByID[id]
        default: return nil
        }
    }

}

enum AssignmentModifier: UInt8, CaseIterable, Identifiable, Sendable {
    case control = 1
    case shift = 2
    case option = 4
    case command = 8
    case rightControl = 16
    case rightShift = 32
    case rightOption = 64
    case rightCommand = 128
    var id: UInt8 { rawValue }

    var name: String {
        switch self {
        case .control: "Ctrl"
        case .shift: "Shift"
        case .option: "Option"
        case .command: "Command"
        case .rightControl: "Right Ctrl"
        case .rightShift: "Right Shift"
        case .rightOption: "Right Option"
        case .rightCommand: "Right Command"
        }
    }
}

struct KeyAction: Codable, Equatable, Sendable {
    var kind: KeyActionKind
    var target: Int?
    var modifiers: UInt8 = 0

    func encodedRecord() throws -> [UInt8] {
        switch kind {
        case .factoryDefault, .disabled:
            guard target == nil, modifiers == 0 else {
                throw WorkflowError.message("Default and disabled assignments cannot contain a target or modifiers.")
            }

            return kind == .factoryDefault ? [0, 0, 0, 0] : [5, 3, 0, 0]
        case .keyboard, .mouse, .media:
            guard let selected = kind.target(id: target), kind == .keyboard || modifiers == 0 else {
                throw WorkflowError.message("The assignment target or modifiers are unsupported.")
            }

            var bytes = selected.bytes
            if kind == .keyboard { bytes[1] |= modifiers }

            return bytes
        }
    }

    static func describe(record: [UInt8]) -> String {
        guard record.count == 4 else { return "Unrecognized assignment" }

        if record == [0, 0, 0, 0] { return "Factory default" }

        if record == [5, 3, 0, 0] { return "Disabled" }

        for kind in [KeyActionKind.keyboard, .mouse, .media] {
            if let target = kind.targets.first(where: { $0.bytes == record }) { return target.name }

            if kind == .keyboard,
                let target = kind.targets.first(where: {
                    $0.bytes[0] == record[0] && $0.bytes[2] == record[2] && $0.bytes[3] == record[3]
                        && $0.bytes[1] & record[1] == $0.bytes[1]
                })
            {
                return KeyAction(kind: kind, target: target.id, modifiers: record[1] & ~target.bytes[1]).summary
            }
        }

        return "Custom assignment (preserved unless you change this key)"
    }

    var targetModifiers: UInt8 {
        guard kind == .keyboard else { return 0 }

        return kind.target(id: target)?.bytes[1] ?? 0
    }

    var effectiveModifiers: UInt8 { kind == .keyboard ? targetModifiers | modifiers : 0 }

    var summary: String {
        let targetName = kind.target(id: target)?.name ?? kind.name
        guard kind == .keyboard, modifiers != 0 else { return targetName }

        let additionalModifiers = modifiers & ~targetModifiers
        let names = AssignmentModifier.allCases.filter { additionalModifiers & $0.rawValue != 0 }.map(\.name)
        return (names + [targetName]).joined(separator: " + ")
    }
}

struct KeyAssignment: Codable, Equatable, Identifiable, Sendable {
    let source: Int
    var action: KeyAction
    var id: Int { source }

    var keyName: String { LegacyKey.physicalByIndex[source]?.name ?? "Unknown" }
}

struct KeyAssignmentDraft: Codable, Equatable, Sendable {
    var assignments: [KeyAssignment] = []

    func validate() throws {
        guard assignments.count <= LegacyKey.physical.count, Set(assignments.map(\.source)).count == assignments.count,
            assignments.allSatisfy({ LegacyKey.physicalIndices.contains($0.source) })
        else { throw WorkflowError.message("Assignments must refer to unique physical C989 keys.") }

        for assignment in assignments { _ = try assignment.action.encodedRecord() }
    }

    mutating func set(_ action: KeyAction?, for source: Int) throws {
        guard LegacyKey.physicalIndices.contains(source) else {
            throw WorkflowError.message("Choose a physical C989 key.")
        }

        var updated = assignments.filter { $0.source != source }

        if let action { updated.append(KeyAssignment(source: source, action: action)) }

        let candidate = KeyAssignmentDraft(assignments: updated.sorted { $0.source < $1.source })
        try candidate.validate()
        self = candidate
    }

    // Offline preparation only. Unmentioned keys and all non-key bytes are preserved.
    func candidate(from baseline: Data) throws -> Data {
        try validate()
        guard baseline.count == 640 else { throw WorkflowError.message("Expected exactly 640 channel-0 bytes.") }

        // Data slices retain their original indices; protocol offsets are body-relative.
        let baseline = Data(baseline)
        guard baseline[636] == 0, baseline[637] == 0,
            (baseline[638] == 0 && baseline[639] == 0) || (baseline[638] == 0xAA && baseline[639] == 0x55)
        else {
            throw WorkflowError.message(
                "Expected a legacy channel-0 capture with a recognized four-byte validity word.")
        }

        if baseline[638] == 0, baseline.contains(where: { $0 != 0 }) {
            throw WorkflowError.message(
                "The inactive assignment table contains unknown data. Inspect the capture before writing.")
        }

        var result = baseline
        for assignment in assignments {
            let offset = assignment.source * 4
            result.replaceSubrange(offset..<(offset + 4), with: try assignment.action.encodedRecord())
        }

        // The Windows builder supplies this trailer even for an otherwise default table.
        result[638] = 0xAA
        result[639] = 0x55
        return result
    }

    func offlinePlan() throws -> Data {
        try validate()
        let patches = try assignments.sorted { $0.source < $1.source }.map { assignment in
            AssignmentPlan.Patch(
                source: assignment.source, key: assignment.keyName, action: assignment.action.summary,
                offset: assignment.source * 4, bytes: try assignment.action.encodedRecord())
        }

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(AssignmentPlan(patches: patches))
    }
}

private struct AssignmentPlan: Encodable {
    struct Patch: Encodable {
        let source: Int
        let key: String
        let action: String
        let offset: Int
        let bytes: [UInt8]
    }

    let format = "c989-key-assignment-offline-plan-v1"
    let hardwareValidated = false
    let channel = 0
    let bodySize = 640
    let requiredZeroOffsets = [636, 637]
    let trailerOffset = 638
    let trailerBytes: [UInt8] = [0xAA, 0x55]
    let semantics =
        "Listed key records and the trailer change; unlisted keys and other bytes retain captured values. No device commands are included."
    let patches: [Patch]
}

@MainActor final class KeyAssignmentEditor: ObservableObject {
    @Published private(set) var draft = KeyAssignmentDraft()
    @Published private(set) var selectedSource = 39

    var selectedAction: KeyAction? { draft.assignments.first { $0.source == selectedSource }?.action }

    func select(_ source: Int) throws {
        guard LegacyKey.physicalIndices.contains(source) else {
            throw WorkflowError.message("Choose a physical C989 key.")
        }

        selectedSource = source
    }

    func set(_ action: KeyAction?, for source: Int) throws { try draft.set(action, for: source) }

    func changeKind(_ kind: KeyActionKind?) throws {
        guard kind != selectedAction?.kind else { return }

        let action = kind.map { KeyAction(kind: $0, target: $0.targets.first?.id) }

        try set(action, for: selectedSource)
    }

    func changeTarget(_ target: Int) throws {
        guard var action = selectedAction else { throw WorkflowError.message("Choose an assignment action first.") }

        action.target = target
        try set(action, for: selectedSource)
    }

    func setModifier(_ modifier: AssignmentModifier, enabled: Bool) throws {
        guard var action = selectedAction, action.kind == .keyboard else {
            throw WorkflowError.message("Only keyboard assignments support modifiers.")
        }

        // A modifier selected as the output is inherent in that output.
        if enabled { action.modifiers |= modifier.rawValue } else { action.modifiers &= ~modifier.rawValue }

        action.modifiers &= ~action.targetModifiers
        try set(action, for: selectedSource)
    }

    func load(_ draft: KeyAssignmentDraft) throws {
        try draft.validate()
        self.draft = KeyAssignmentDraft(assignments: draft.assignments.sorted { $0.source < $1.source })
        if selectedAction == nil, let first = self.draft.assignments.first { selectedSource = first.source }
    }

    func clear() { draft = KeyAssignmentDraft() }
}
