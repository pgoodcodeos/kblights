import C989Native
import Foundation
import SwiftUI

struct AssignmentReview: Identifiable, Sendable {
    let id = UUID()
    let draft: KeyAssignmentDraft
    let candidate: Data
    let changedKeys: [String]
    let changedBytes: Int
    let location: UInt32
}

@MainActor final class AssignmentWorkflow: ObservableObject {
    @Published private(set) var snapshot: AssignmentSnapshot?
    @Published private(set) var operation: String?
    @Published private(set) var status = "Keyboard state has not loaded yet."
    @Published private(set) var error: String?
    @Published private(set) var needsInputMonitoringAccess = false
    var requiresInspection: Bool { activity.requiresInspection }

    @Published private var operationEvidence: URL?
    var lastEvidence: URL? {
        if requiresInspection { return activity.inspectionEvidence }

        return operationEvidence.flatMap { FileManager.default.fileExists(atPath: $0.path) ? $0 : nil }
    }

    @Published var review: AssignmentReview?
    private let service: any AssignmentDeviceService
    private let root: URL
    private let checkAccess: @MainActor () -> Bool
    private let activity: KeyboardDeviceActivity

    init(
        service: any AssignmentDeviceService = NativeAssignmentDeviceService(), root: URL? = nil,
        activity: KeyboardDeviceActivity = .shared,
        checkAccess: @escaping @MainActor () -> Bool = { KeyboardPermission.hasSystemAccess }
    ) {
        self.service = service
        self.root =
            root
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("KeyboardLights/Assignments")
        self.activity = activity
        self.checkAccess = checkAccess
    }

    var isBusy: Bool { operation != nil }

    private func output(_ name: String) -> URL { return root.appendingPathComponent("\(name)-\(UUID().uuidString)") }

    func read(owner: UUID? = nil) async {
        guard !isBusy else { return }

        do { try activity.begin(owner: owner) } catch {
            self.error = error.localizedDescription
            return
        }

        defer {
            operation = nil
            activity.finish()
        }

        snapshot = nil
        review = nil
        error = nil
        needsInputMonitoringAccess = !checkAccess()
        guard !needsInputMonitoringAccess else {
            error = "Enable KeyboardLights under Input Monitoring in System Settings, then return to the app."
            status = "Read stopped; Input Monitoring access is required."
            return
        }

        operation = "Reading keyboard state…"
        do {
            let output = output("capture")
            if !requiresInspection { operationEvidence = output }

            snapshot = try await service.capture(output: output).validated()
            status = "Current key assignments loaded."
        } catch {
            self.error = error.localizedDescription
            status = "Read stopped without writing."
        }
    }

    func invalidateIfKeyboardChanged(_ lighting: DeviceSnapshot?) {
        guard let snapshot else { return }

        if let lighting, UInt32(lighting.location, radix: 16) == snapshot.location { return }

        self.snapshot = nil
        review = nil
        status = "Keyboard state changed. Assignments will refresh when opened."
    }

    func readIfNeeded() async {
        guard snapshot == nil, !activity.isBusy else { return }

        await read()
    }

    func draftMatchesKeyboard(_ draft: KeyAssignmentDraft) -> Bool {
        guard !draft.assignments.isEmpty, let baseline = snapshot?.channels.first,
            let candidate = try? draft.candidate(from: baseline)
        else { return false }

        return candidate == baseline
    }

    func invalidateReview() { review = nil }

    func prepare(_ draft: KeyAssignmentDraft) {
        guard !isBusy else { return }

        review = nil
        error = nil
        do {
            try activity.checkWriteAllowed()
            guard let snapshot else { throw WorkflowError.message("Read assignments before reviewing a write.") }

            let candidate = try NativeAssignmentPolicy.candidate(baseline: snapshot.channels[0], draft: draft)
            let keys = draft.assignments.filter { assignment in
                let offset = assignment.source * 4
                return candidate[offset..<(offset + 4)] != snapshot.channels[0][offset..<(offset + 4)]
            }.map(\.keyName)
            review = AssignmentReview(
                draft: draft, candidate: candidate, changedKeys: keys,
                changedBytes: zip(candidate, snapshot.channels[0]).reduce(0) { $0 + ($1.0 != $1.1 ? 1 : 0) },
                location: snapshot.location)
            status = "Changes are ready to review."
        } catch { self.error = error.localizedDescription }
    }

    @discardableResult func apply(_ reviewed: AssignmentReview, currentDraft: KeyAssignmentDraft, owner: UUID? = nil)
        async -> Bool
    {
        guard !isBusy else { return false }

        guard !requiresInspection, let snapshot, let review, review.id == reviewed.id, reviewed.draft == currentDraft,
            reviewed.location == snapshot.location
        else {
            self.review = nil
            error = "This review is stale. Prepare a new review before applying."
            return false
        }

        do { try activity.begin(owner: owner) } catch {
            self.error = error.localizedDescription
            return false
        }

        defer {
            operation = nil
            activity.finish()
        }

        self.review = nil
        error = nil
        operation = "Updating key assignments…"
        let destination = output("write")
        operationEvidence = destination
        do {
            let verified = try await service.write(
                snapshot: snapshot, draft: reviewed.draft, candidate: reviewed.candidate, output: destination)
            var expected = snapshot.channels
            expected[0] = reviewed.candidate
            guard verified.location == snapshot.location, verified.channels == expected else {
                throw NativeDeviceError(
                    code: Int32(C989_ACTIVATION_UNCERTAIN),
                    message:
                        "The returned assignment state did not match the reviewed update. Further writes require inspection."
                )
            }

            self.snapshot = try verified.validated()
            status = "Key assignments updated."
            return true
        } catch {
            self.snapshot = nil

            // Any unclassified handoff failure after entering a write is conservative.
            if (error as? NativeDeviceError)?.requiresInspection ?? true {
                let records = FileManager.default.fileExists(atPath: destination.path) ? destination : nil
                activity.blockWrites(evidence: records)
            }

            self.error = error.localizedDescription
            status =
                requiresInspection
                ? "Stopped; the keyboard update needs inspection." : "Stopped before upload. Read assignments again."
            return false
        }
    }
}
