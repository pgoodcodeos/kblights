import SwiftUI

struct KeyboardProfileActions {
    let workflow: KeyboardProfileWorkflow
    let enabled: Bool
    let canLoadLighting: Bool
    let canSaveLighting: Bool

    @MainActor var isReady: Bool { enabled && workflow.isReady }
}

private struct KeyboardProfileActionsKey: FocusedValueKey { typealias Value = KeyboardProfileActions }

extension FocusedValues {
    var keyboardProfileActions: KeyboardProfileActions? {
        get { self[KeyboardProfileActionsKey.self] }

        set { self[KeyboardProfileActionsKey.self] = newValue }
    }
}

struct KeyboardProfileCommands: Commands {
    @FocusedValue(\.keyboardProfileActions) private var actions
    private var sectionTitle: String { activeSection == .lighting ? "Lighting Profile" : "Assignment Profile" }

    private var activeSection: KeyboardProfileDocument.Section { actions?.workflow.activeSection ?? .lighting }

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            profileMenu(.lighting, title: "Lighting Profile")
            profileMenu(.assignments, title: "Assignment Profile")
        }

        CommandGroup(replacing: .saveItem) {
            Button("Open \(sectionTitle)…") { actions?.workflow.open(activeSection) }.keyboardShortcut("o").disabled(
                !canLoad(activeSection))
            Button("Save \(sectionTitle)") { actions?.workflow.save(activeSection, asNewFile: false) }.keyboardShortcut(
                "s"
            ).disabled(!canSave(activeSection))
            Button("Save \(sectionTitle) As…") { actions?.workflow.save(activeSection, asNewFile: true) }
                .keyboardShortcut("s", modifiers: [.command, .shift]).disabled(!canSave(activeSection))
        }
    }

    private func canLoad(_ section: KeyboardProfileDocument.Section) -> Bool {
        actions?.isReady == true && (section == .assignments || actions?.canLoadLighting == true)
    }

    private func canSave(_ section: KeyboardProfileDocument.Section) -> Bool {
        actions?.isReady == true && (section == .assignments || actions?.canSaveLighting == true)
    }

    private func profileMenu(_ section: KeyboardProfileDocument.Section, title: String) -> some View {
        Menu(title) {
            Button("New") { activate(section) { $0.newProfile(section) } }.disabled(!canLoad(section))
            Button("Open…") { activate(section) { $0.open(section) } }.disabled(!canLoad(section))
            Button("Save") { activate(section) { $0.save(section, asNewFile: false) } }.disabled(!canSave(section))
            Button("Save As…") { activate(section) { $0.save(section, asNewFile: true) } }.disabled(!canSave(section))
            Divider()
            Button("Delete Current…", role: .destructive) { activate(section) { $0.requestDeletion(section) } }
                .disabled(actions?.isReady != true || actions?.workflow.document(for: section) == nil)
        }
    }

    private func activate(_ section: KeyboardProfileDocument.Section, action: (KeyboardProfileWorkflow) -> Void) {
        guard let actions, actions.isReady else { return }

        actions.workflow.activeSection = section
        action(actions.workflow)
    }
}
