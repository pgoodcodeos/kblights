import SwiftUI

struct KeyboardChangesReviewSheet: View {
    @ObservedObject var workflow: KeyboardChangesWorkflow
    let review: KeyboardChangesReview
    @State private var confirmed = false

    // Keep the opaque progress cover in place until the completed sheet is dismissed.
    @State private var hasStartedApplying = false
    @Environment(\.dismiss) private var dismiss

    private var isApplying: Bool { workflow.isApplying || hasStartedApplying }

    var body: some View {
        reviewContent.padding(24).frame(width: 620).disabled(isApplying).accessibilityHidden(isApplying).background(
            Color(nsColor: .windowBackgroundColor)
        ).overlay { if isApplying { applyingOverlay } }.interactiveDismissDisabled(isApplying).transaction {
            // Parent operation animations must not crossfade or move sheet text.
            $0.animation = nil
            $0.disablesAnimations = true
        }.onChange(of: workflow.isApplying, initial: true) { _, applying in if applying { hasStartedApplying = true } }
    }

    private var reviewContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Review keyboard changes").font(.title2.bold())
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let lighting = review.lighting {
                        Text("Lighting · " + lighting.selectionName).font(.headline)
                        Text(
                            lighting.changedKeys.isEmpty
                                ? "Effect or brightness changes" : lighting.changedKeys.joined(separator: ", "))
                    }

                    if let assignments = review.assignments {
                        Text("Assignments").font(.headline)
                        ForEach(assignments.draft.assignments) { assignment in
                            Text("\(assignment.keyName) → \(assignment.action.summary)")
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(maxHeight: 300)
            Text(
                "Keep the keyboard connected. Changes are verified in separate steps; if a step fails, the update stops."
            ).font(.callout).foregroundStyle(.secondary)
            Toggle("Apply all reviewed changes to my keyboard", isOn: $confirmed)
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Apply changes") { Task { await workflow.apply(review) } }.buttonStyle(.borderedProminent)
                    .disabled(!confirmed)
            }
        }
    }

    private var applyingOverlay: some View {
        VStack(spacing: 16) {
            ProgressView().progressViewStyle(.circular).controlSize(.large).accessibilityLabel(
                "Keyboard update in progress")
            Text("Applying keyboard changes").font(.title2.bold())
            Text(workflow.status).font(.headline).multilineTextAlignment(.center).fixedSize(
                horizontal: false, vertical: true
            ).frame(minHeight: 40).accessibilityIdentifier("keyboard-update-status")
            Text("Keep your keyboard connected until this window closes.").foregroundStyle(.secondary)
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
        }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity).background(
            Color(nsColor: .windowBackgroundColor)
        ).contentShape(Rectangle()).environment(\.isEnabled, true)
    }
}
