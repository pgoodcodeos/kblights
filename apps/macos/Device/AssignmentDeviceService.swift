import C989Native
import Foundation

struct AssignmentSnapshot: Equatable, Sendable {
    let location: UInt32
    let channels: [Data]
    var lockIndicators: LockIndicators? = nil

    func validated() throws -> Self {
        guard location != 0, channels.count == 1, channels[0].count == 640 else {
            throw WorkflowError.message("A complete assignment capture is required.")
        }

        let normalized = channels.map { Data($0) }

        return Self(location: location, channels: normalized, lockIndicators: lockIndicators)
    }

    func assignmentDescription(for source: Int) -> String {
        guard LegacyKey.physicalIndices.contains(source), let channel = channels.first, channel.count == 640 else {
            return "Read assignments to see the current action."
        }

        let data = Data(channel)
        if data[636] != 0 || data[637] != 0
            || !((data[638] == 0 && data[639] == 0) || (data[638] == 0xAA && data[639] == 0x55))
        {
            return "Unrecognized assignment table (preserved unless you change it)"
        }

        if data[638] == 0 {
            return data.allSatisfy { $0 == 0 } ? "Factory default" : "Inactive assignment table with unknown data"
        }

        let offset = source * 4
        return KeyAction.describe(record: Array(data[offset..<(offset + 4)]))
    }

}

enum NativeAssignmentPolicy {
    static func patches(_ draft: KeyAssignmentDraft) throws -> [C989AssignmentPatch] {
        try draft.validate()
        return try draft.assignments.map { assignment in
            let bytes = try assignment.action.encodedRecord()
            return C989AssignmentPatch(
                source: UInt16(assignment.source), record: (bytes[0], bytes[1], bytes[2], bytes[3]))
        }
    }

    static func candidate(baseline: Data, draft: KeyAssignmentDraft) throws -> Data {
        let patches = try patches(draft)
        var candidate = Data(repeating: 0, count: 640)
        var message = [CChar](repeating: 0, count: 1024)
        let code = baseline.withUnsafeBytes { bytes in
            patches.withUnsafeBufferPointer { records in
                candidate.withUnsafeMutableBytes { output in
                    c989_assignments_prepare(
                        bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count, records.baseAddress, records.count,
                        output.bindMemory(to: UInt8.self).baseAddress, output.count, &message, message.count)
                }
            }
        }

        try NativeDeviceError.check(code, message: message)

        // Independent native policy must reproduce the editor's exact reviewed bytes.
        guard candidate == (try draft.candidate(from: baseline)) else {
            throw WorkflowError.message("The native writer and assignment editor disagree; no write is allowed.")
        }

        return candidate
    }
}

protocol AssignmentDeviceService: Sendable {
    func capture(output: URL) async throws -> AssignmentSnapshot
    func write(snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL) async throws
        -> AssignmentSnapshot
}

struct NativeAssignmentDeviceService: AssignmentDeviceService {
    func capture(output: URL) async throws -> AssignmentSnapshot {
        try await Task.detached(priority: .userInitiated) {
            let capture = try NativeKeyboardBackend().capture(channel: .assignments, output: output)
            return try AssignmentSnapshot(
                location: capture.location, channels: [capture.data], lockIndicators: capture.lockIndicators
            ).validated()
        }.value
    }

    func write(snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL) async throws
        -> AssignmentSnapshot
    {
        try await Task.detached(priority: .userInitiated) {
            let snapshot = try snapshot.validated()
            let lockIndicators = try NativeKeyboardBackend().writeAssignments(
                snapshot: snapshot, draft: draft, candidate: candidate, output: output)

            // Native success means exact assignment postflight passed against these
            // immutable inputs. Diagnostic files are never a response channel.
            var channels = snapshot.channels
            channels[0] = candidate
            return AssignmentSnapshot(location: snapshot.location, channels: channels, lockIndicators: lockIndicators)
        }.value
    }
}
