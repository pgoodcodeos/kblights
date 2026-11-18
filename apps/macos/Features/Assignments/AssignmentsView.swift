import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct KeyAssignmentsView: View {
    @ObservedObject var workflow: AssignmentWorkflow
    @ObservedObject private var activity = KeyboardDeviceActivity.shared
    @ObservedObject var editor: KeyAssignmentEditor
    var selectKey: (Int) -> Void
    @State private var error: String?
    @State private var confirmClear = false

    var body: some View {
        let matchesKeyboard = workflow.draftMatchesKeyboard(editor.draft)
        VStack(alignment: .leading, spacing: 18) {
            if let message = workflow.error {
                FeedbackBanner(text: message, symbol: "exclamationmark.triangle.fill", tint: .orange)
            }

            if workflow.needsInputMonitoringAccess {
                Button("Open Input Monitoring settings", action: KeyboardPermission.openSettings)
            }

            if workflow.requiresInspection {
                WorkspaceCard {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("An update needs inspection", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange).font(.headline)
                        Text("Further writes are blocked until the keyboard update has been investigated.")
                        if let evidence = workflow.lastEvidence {
                            Button("Show transaction records") {
                                NSWorkspace.shared.activateFileViewerSelecting([evidence])
                            }
                        }
                    }
                }
            }

            if let error { FeedbackBanner(text: error, symbol: "exclamationmark.circle.fill", tint: .red) }

            WorkspaceCard {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text(matchesKeyboard ? "Applied assignments" : "Planned changes").font(.headline)
                        Text("\(editor.draft.assignments.count)").foregroundStyle(.secondary)
                        Spacer()
                        Menu {
                            Button("Save offline plan…", action: exportPlan)
                            Divider()
                            Button("Clear draft…", role: .destructive) { confirmClear = true }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }.menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Draft options").disabled(
                            editor.draft.assignments.isEmpty)
                    }

                    if editor.draft.assignments.isEmpty {
                        Label("No assignment changes.", systemImage: "cursorarrow.click").foregroundStyle(.secondary)
                            .padding(.vertical, 12)
                    } else {
                        ForEach(editor.draft.assignments) { assignment in
                            HStack(spacing: 12) {
                                Button(assignment.keyName) { selectKey(assignment.source) }.buttonStyle(.borderless)
                                    .frame(width: 110, alignment: .leading)
                                Image(systemName: "arrow.right").font(.caption).foregroundStyle(.tertiary)
                                Text(assignment.action.summary).frame(maxWidth: .infinity, alignment: .leading)
                                Button {
                                    perform { try editor.set(nil, for: assignment.source) }
                                } label: {
                                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                                }.buttonStyle(.plain).help("Remove planned change for \(assignment.keyName)")
                                    .accessibilityLabel("Remove planned change for \(assignment.keyName)")
                            }.padding(.vertical, 5)
                            if assignment.id != editor.draft.assignments.last?.id { Divider() }
                        }
                    }
                }
            }
        }.disabled(activity.isBusy).confirmationDialog(
            "Clear all planned assignment changes?", isPresented: $confirmClear, titleVisibility: .visible
        ) {
            Button("Clear draft", role: .destructive) { editor.clear() }

            Button("Cancel", role: .cancel) {}
        }
    }

    private func perform(_ action: () throws -> Void) {
        error = nil
        do { try action() } catch { self.error = error.localizedDescription }
    }

    private func exportPlan() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.json]
        panel.nameFieldStringValue = "Key Assignment Offline Plan.json"
        guard panel.runModal() == .OK, let url = panel.url else { return }

        perform { try editor.draft.offlinePlan().write(to: url, options: .atomic) }
    }
}
