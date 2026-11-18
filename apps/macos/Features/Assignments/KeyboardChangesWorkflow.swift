import Combine
import Foundation

struct KeyboardChangesReview: Identifiable {
    let id = UUID()
    let lighting: WriteReview?
    let assignments: AssignmentReview?
    let lightingDraft: LightingProfileDraft?
    let assignmentDraft: KeyAssignmentDraft
    let unpainted: Set<Int>
}

@MainActor final class KeyboardChangesWorkflow: ObservableObject {
    @Published var review: KeyboardChangesReview?
    @Published private(set) var isApplying = false
    @Published private(set) var error: String?
    @Published private(set) var status = "Review to apply."
    private var isPreparing = false
    private let state: KeyboardState
    private let assignments: AssignmentWorkflow
    private let editor: KeyAssignmentEditor
    private let activity: KeyboardDeviceActivity

    init(
        state: KeyboardState, assignments: AssignmentWorkflow, editor: KeyAssignmentEditor,
        activity: KeyboardDeviceActivity
    ) {
        self.state = state
        self.assignments = assignments
        self.editor = editor
        self.activity = activity
    }

    private var hasAssignmentChanges: Bool {
        !editor.draft.assignments.isEmpty && !assignments.draftMatchesKeyboard(editor.draft)
    }

    var hasPendingChanges: Bool { state.hasUnsavedLightingChanges || hasAssignmentChanges }

    var canReview: Bool {
        !isApplying && !activity.isBusy && !activity.requiresInspection && !state.hasUnpaintedKeys
            && (state.hasLightingChangesToApply || hasAssignmentChanges)
            && (!hasAssignmentChanges || assignments.snapshot != nil)
    }

    func invalidateReview() {
        guard !isApplying, !isPreparing, let review else { return }

        guard (try? matches(review)) != true else { return }

        self.review = nil
        status = "Review to apply."
    }

    private func matches(_ reviewed: KeyboardChangesReview) throws -> Bool {
        try lightingDraft() == reviewed.lightingDraft && editor.draft == reviewed.assignmentDraft
            && state.unpaintedKeys == reviewed.unpainted
            && (reviewed.lighting == nil || state.review?.id == reviewed.lighting?.id)
            && (reviewed.assignments == nil || assignments.review?.id == reviewed.assignments?.id)
    }

    private func lightingDraft() throws -> LightingProfileDraft? {
        state.hasLightingDraft ? try state.savedLightingDraft() : nil
    }

    func prepare() {
        guard canReview else { return }

        isPreparing = true
        defer { isPreparing = false }

        review = nil
        error = nil
        do {
            let draft = try lightingDraft()
            let lightingChanges = state.hasLightingChangesToApply
            let assignmentChanges = hasAssignmentChanges
            if lightingChanges && assignmentChanges {
                guard let lighting = state.snapshot, let capture = assignments.snapshot,
                    UInt32(lighting.location, radix: 16) == capture.location
                else {
                    throw WorkflowError.message(
                        "Lighting and assignments use different captures. Save drafts, reset lighting, then Refresh before reviewing."
                    )
                }
            }

            if lightingChanges {
                state.prepareReview()
                guard state.review != nil else {
                    throw WorkflowError.message(
                        state.error ?? state.warning ?? "Lighting review could not be prepared.")
                }
            }

            if assignmentChanges {
                assignments.prepare(editor.draft)
                guard assignments.review != nil else {
                    throw WorkflowError.message(assignments.error ?? "Assignment review could not be prepared.")
                }
            }

            review = KeyboardChangesReview(
                lighting: lightingChanges ? state.review : nil,
                assignments: assignmentChanges ? assignments.review : nil, lightingDraft: draft,
                assignmentDraft: editor.draft, unpainted: state.unpaintedKeys)
        } catch {
            state.review = nil
            assignments.review = nil
            self.error = error.localizedDescription
        }
    }

    func apply(_ reviewed: KeyboardChangesReview) async {
        guard !isApplying, review?.id == reviewed.id else { return }

        guard !activity.isBusy, !activity.requiresInspection else {
            error =
                activity.requiresInspection
                ? "A keyboard update needs inspection before further writes."
                : "Another keyboard operation is underway. Review changes again when it finishes."
            review = nil
            state.review = nil
            assignments.review = nil
            return
        }

        do {
            guard try matches(reviewed) else {
                throw WorkflowError.message("The draft changed. Review all changes again before applying.")
            }
        } catch {
            self.error = error.localizedDescription
            review = nil
            state.review = nil
            assignments.review = nil
            return
        }

        let owner: UUID
        do { owner = try activity.reserve() } catch {
            self.error = error.localizedDescription
            return
        }

        isApplying = true
        error = nil
        var completed: [String] = []
        defer {
            review = nil
            state.review = nil
            assignments.review = nil
            isApplying = false
            activity.release(owner)
        }

        if let assignmentReview = reviewed.assignments {
            status = "Applying assignments…"
            guard await assignments.apply(assignmentReview, currentDraft: editor.draft, owner: owner) else {
                status = "Update stopped before lighting."
                error =
                    "Assignments did not finish. Lighting was not applied. " + (assignments.error ?? assignments.status)
                return
            }

            completed.append("Assignments")
        }

        if let lightingReview = reviewed.lighting {
            status = "Applying lighting…"
            guard await state.applyReviewedWrite(lightingReview, owner: owner) else {
                status = completed.isEmpty ? "Update stopped." : "Assignments applied; update stopped during lighting."
                error =
                    (completed.isEmpty ? "Lighting did not finish. " : "Assignments applied; lighting did not finish. ")
                    + (state.error ?? state.warning ?? state.status)
                return
            }

            completed.append("Lighting")
        }

        if let reviewedAssignments = reviewed.assignments {
            guard let capture = assignments.snapshot, capture.location == reviewedAssignments.location,
                capture.channels[0] == reviewedAssignments.candidate
            else {
                activity.blockWrites(evidence: assignments.lastEvidence)
                status = "Update needs inspection."
                error = "The verified assignment result differs from the reviewed update."
                return
            }
        }

        status = completed.joined(separator: " and ") + " applied and verified."
    }
}
