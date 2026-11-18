import SwiftUI

struct KeyInspector: View {
    @ObservedObject var state: KeyboardState
    @ObservedObject var workflow: AssignmentWorkflow
    @ObservedObject var editor: KeyAssignmentEditor
    @ObservedObject var activity: KeyboardDeviceActivity = .shared
    @State private var error: String?

    private var source: Int { editor.selectedSource }

    private var isBusy: Bool { activity.isBusy || state.isBusy || workflow.isBusy }

    private var currentAssignment: String { workflow.snapshot?.assignmentDescription(for: source) ?? "Not loaded" }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Selected key: " + (LegacyKey.physicalByIndex[source]?.name ?? "Key")).font(.headline)
            if let error { FeedbackBanner(text: error, symbol: "exclamationmark.circle", tint: .orange) }

            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 16) {
                    lightingDetails.frame(width: 300)
                    assignmentDetails.frame(minWidth: 300)
                }

                VStack(alignment: .leading, spacing: 12) {
                    lightingDetails
                    assignmentDetails
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading).disabled(isBusy).onChange(of: source) { _, _ in error = nil }
    }

    private var lightingDetails: some View {
        GroupBox("Lighting") {
            VStack(alignment: .leading, spacing: 8) {
                keyState(
                    current: state.keyLightingDescription(source, onKeyboard: true),
                    preview: state.keyLightingDescription(source, onKeyboard: false))
                if state.editorStyle != .animation {
                    HStack {
                        Button("Paint selected color") { state.paintSelectedColor(on: source) }.disabled(
                            !state.hasSnapshot)
                        if state.hasKeyLightingChange(source) {
                            Button("Revert") { state.revertKeyLighting(source) }.accessibilityLabel(
                                "Revert key lighting"
                            ).help("Restore this key’s color and Off setting")
                        }
                    }
                }

                Text(state.editorStyle == .animation ? "Animation affects all keys." : "Brightness affects all keys.")
                    .font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(8)
        }
    }

    private var assignmentDetails: some View {
        GroupBox("Assignment") {
            VStack(alignment: .leading, spacing: 12) {
                keyState(current: currentAssignment, preview: editor.selectedAction?.summary ?? currentAssignment)
                Picker(
                    "Action",
                    selection: Binding(
                        get: { editor.selectedAction?.kind }, set: { kind in perform { try editor.changeKind(kind) } })
                ) {
                    Text("Keep current").tag(nil as KeyActionKind?)
                    ForEach(KeyActionKind.allCases) { kind in Text(kind.name).tag(Optional(kind)) }
                }

                if let kind = editor.selectedAction?.kind, !kind.targets.isEmpty {
                    Picker(
                        "Output",
                        selection: Binding(
                            get: { editor.selectedAction?.target ?? kind.targets[0].id },
                            set: { target in perform { try editor.changeTarget(target) } })
                    ) { ForEach(kind.targets) { target in Text(target.name).tag(target.id) } }
                }

                if editor.selectedAction?.kind == .keyboard {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), alignment: .leading)]) {
                        ForEach(AssignmentModifier.allCases) { modifier in
                            Toggle(
                                modifier.name,
                                isOn: Binding(
                                    get: { (editor.selectedAction?.effectiveModifiers ?? 0) & modifier.rawValue != 0 },
                                    set: { enabled in perform { try editor.setModifier(modifier, enabled: enabled) } })
                            ).disabled((editor.selectedAction?.targetModifiers ?? 0) & modifier.rawValue != 0)
                        }
                    }

                    Text("Shortcuts send keys, not text. Your active keyboard layout determines characters.").font(
                        .caption
                    ).foregroundStyle(.secondary)
                }

                if editor.selectedAction?.kind == .media {
                    Text("Application and media actions depend on the operating system.").font(.caption)
                        .foregroundStyle(.secondary)
                }

                if editor.selectedAction != nil {
                    Button("Revert") { perform { try editor.set(nil, for: source) } }.accessibilityLabel(
                        "Revert key assignment")
                }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(8)
        }
    }

    private func keyState(current: String, preview: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if current == preview {
                Text(current)
            } else {
                Text("On keyboard: " + current).font(.callout).foregroundStyle(.secondary)
                Text("Preview: " + preview)
            }
        }
    }

    private func perform(_ action: () throws -> Void) {
        guard !isBusy else { return }

        error = nil
        do {
            try action()
            workflow.review = nil
        } catch { self.error = error.localizedDescription }
    }
}
