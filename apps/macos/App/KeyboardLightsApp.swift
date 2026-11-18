import AppKit
import SwiftUI

@main struct KeyboardLightsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var profiles = KeyboardProfileFiles()
    var body: some Scene {
        WindowGroup { KeyboardWindowView(profiles: profiles).frame(minWidth: 980, minHeight: 680) }.windowStyle(
            .titleBar
        ).commands { KeyboardProfileCommands() }
    }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    private let termination = KeyboardTermination(activity: .shared)

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            NSApp.activate(ignoringOtherApps: true)
            NSApp.windows.first?.makeKeyAndOrderFront(nil)
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        termination.request { sender.reply(toApplicationShouldTerminate: $0) }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { NSApp.windows.first?.makeKeyAndOrderFront(nil) }

        NSApp.activate(ignoringOtherApps: true)
        return true
    }
}

private enum AppSection: String, Identifiable {
    case keyboard = "Keyboard"
    case diagnostics = "Diagnostics"
    var id: String { rawValue }

    var icon: String {
        switch self {
        case .keyboard: "keyboard"
        case .diagnostics: "waveform.path.ecg"
        }
    }
}

private struct KeyboardWindowView: View {
    @StateObject private var session = KeyboardAppSession()
    @ObservedObject var profiles: KeyboardProfileFiles

    var body: some View { ContentView(profiles: profiles, session: session) }
}

struct ContentView: View {
    @ObservedObject private var session: KeyboardAppSession
    @ObservedObject private var connection: KeyboardConnection
    @ObservedObject private var permission: KeyboardPermission
    @ObservedObject private var state: KeyboardState
    @StateObject private var assignments: KeyAssignmentEditor
    @StateObject private var changes: KeyboardChangesWorkflow
    @ObservedObject private var assignmentWorkflow: AssignmentWorkflow
    @ObservedObject private var deviceActivity: KeyboardDeviceActivity
    @ObservedObject var profiles: KeyboardProfileFiles
    @State private var selection: AppSection? = .keyboard
    @StateObject private var profileWorkflow: KeyboardProfileWorkflow
    init(profiles: KeyboardProfileFiles, session: KeyboardAppSession, editor: KeyAssignmentEditor? = nil) {
        self.profiles = profiles
        self.session = session
        connection = session.connection
        permission = session.permission
        state = session.state
        assignmentWorkflow = session.assignmentWorkflow
        deviceActivity = session.activity
        let editor = editor ?? KeyAssignmentEditor()
        _assignments = StateObject(wrappedValue: editor)
        _profileWorkflow = StateObject(
            wrappedValue: KeyboardProfileWorkflow(
                files: profiles, state: session.state, editor: editor,
                isAvailable: {
                    !session.activity.isBusy && !session.activity.isQuitting && !session.connection.isChecking
                        && session.connection.isConnected && session.permission.isGranted
                }))
        _changes = StateObject(
            wrappedValue: KeyboardChangesWorkflow(
                state: session.state, assignments: session.assignmentWorkflow, editor: editor,
                activity: session.activity))
    }

    var body: some View {
        ZStack {
            NavigationSplitView {
                List(selection: $selection) {
                    SwiftUI.Section("Keyboard") {
                        Label(AppSection.keyboard.rawValue, systemImage: AppSection.keyboard.icon).tag(
                            AppSection.keyboard)
                    }

                    SwiftUI.Section("Tools") {
                        Label(AppSection.diagnostics.rawValue, systemImage: AppSection.diagnostics.icon).tag(
                            AppSection.diagnostics)
                    }
                }.navigationTitle("KeyboardLights").navigationSplitViewColumnWidth(min: 185, ideal: 205, max: 240)
                    .safeAreaInset(edge: .bottom) {
                        VStack(alignment: .leading, spacing: 5) {
                            Label("Cloud Nine C989", systemImage: "keyboard").font(.caption.weight(.semibold))
                            Text("Firmware 1.38").font(.caption).foregroundStyle(.secondary)
                            if deviceActivity.requiresInspection {
                                Label("Update needs attention", systemImage: "exclamationmark.triangle.fill").font(
                                    .caption
                                ).foregroundStyle(.orange)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(16)
                    }
            } detail: {
                Group {
                    switch selection ?? .keyboard {
                    case .keyboard:
                        if connection.isConnected && !permission.isGranted {
                            KeyboardPermissionView(permission: permission)
                        } else {
                            keyboardWorkspace.disabled(!connection.isConnected)
                        }

                    case .diagnostics: DiagnosticsView(state: state).disabled(!connection.isConnected)

                    }
                }.safeAreaInset(edge: .top) {
                    if (selection ?? .keyboard) != .keyboard, !connection.isConnected {
                        HStack {
                            if connection.isChecking { ProgressView().controlSize(.small) }

                            Text(connection.message)
                            Spacer()
                        }.padding().background(.bar)
                    }
                }.toolbar {
                    Button {
                        Task { await refreshKeyboardState() }
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }.help("Read keyboard settings while keeping staged changes").disabled(
                        deviceActivity.isBusy || connection.isChecking)
                }
            }

            if let operation = state.activeOperation, operation != .reading, changes.review == nil {
                OperationInProgressOverlay(operation: operation, status: state.status).transition(
                    .opacity.combined(with: .scale(scale: 0.97)))
            }
        }.background { KeyboardCloseGuard(activity: deviceActivity) { changes.hasPendingChanges } }.focusedSceneValue(
            \.keyboardProfileActions,
            KeyboardProfileActions(
                workflow: profileWorkflow,
                enabled: !deviceActivity.isBusy && !deviceActivity.isQuitting && connection.isConnected
                    && permission.isGranted && changes.review == nil && !state.confirmsLightingDiscard
                    && profileWorkflow.error == nil, canLoadLighting: profileWorkflow.canLoad(.lighting),
                canSaveLighting: profileWorkflow.canSave(.lighting))
        ).disabled(deviceActivity.isBusy || changes.isApplying || deviceActivity.isQuitting).task {
            await session.start()
        }.onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            session.applicationBecameActive()
            profiles.refresh()
        }.animation(.easeInOut(duration: 0.18), value: state.activeOperation).alert(
            "Could not update profile file",
            isPresented: Binding(get: { profileWorkflow.error != nil }, set: { if !$0 { profileWorkflow.error = nil } })
        ) {
            Button("OK", role: .cancel) { profileWorkflow.error = nil }
        } message: {
            Text(profileWorkflow.error ?? "")
        }.alert(
            "Replace your editor draft?",
            isPresented: Binding(
                get: { profileWorkflow.pendingLoad != nil || profileWorkflow.pendingNew != nil },
                set: { if !$0 { profileWorkflow.cancelLoad() } })
        ) {
            Button("Keep editing", role: .cancel) { profileWorkflow.cancelLoad() }

            Button("Replace draft", role: .destructive) { profileWorkflow.confirmLoad() }
        } message: {
            Text("Save your current draft first if you want to keep it. Review the loaded draft before applying it.")
        }.confirmationDialog(
            "Delete current profile?",
            isPresented: Binding(
                get: { profileWorkflow.pendingDeletion != nil }, set: { if !$0 { profileWorkflow.cancelDeletion() } }),
            titleVisibility: .visible
        ) {
            Button("Move to Trash", role: .destructive) { profileWorkflow.confirmDeletion() }

            Button("Cancel", role: .cancel) { profileWorkflow.cancelDeletion() }
        } message: {
            Text(profileWorkflow.deletionMessage)
        }.alert("Discard lighting edits?", isPresented: $state.confirmsLightingDiscard) {
            Button("Keep editing", role: .cancel) {}

            Button("Discard and read", role: .destructive) {
                Task { await session.refresh(assignments: false, discardingLightingEdits: true) }
            }
        } message: {
            Text(
                "Reading the keyboard replaces your staged lighting edits. Save a profile first if you want to keep them."
            )
        }
    }

    private var keyboardWorkspace: some View {
        VStack(spacing: 0) {
            if let error = assignmentWorkflow.error {
                FeedbackBanner(
                    text: "Key assignments: " + error, symbol: "exclamationmark.triangle.fill", tint: .orange
                ).padding(.horizontal)
            }

            if let error = changes.error {
                FeedbackBanner(text: error, symbol: "exclamationmark.triangle", tint: .orange).padding(.horizontal)
            }

            if let message = profileWorkflow.message {
                FeedbackBanner(text: message, symbol: "checkmark.circle.fill", tint: .green).padding(.horizontal)
            }

            if let error = profiles.error {
                FeedbackBanner(text: error, symbol: "exclamationmark.triangle.fill", tint: .orange).padding(.horizontal)
            }

            LightingView(
                state: state, keyboard: sharedKeyboard, lockIndicators: previewLockIndicators,
                assignmentContent: AnyView(assignmentControls), reviewChanges: changes.prepare,
                canReviewChanges: changes.canReview,
                reviewStatus: changes.canReview ? "Changes staged. Review to apply." : changes.status,
                operationStatus: keyboardOperationStatus,
                isOperationInProgress: connection.isChecking || assignmentWorkflow.isBusy || changes.isApplying)
        }.onChange(of: assignments.draft) { _, _ in changes.invalidateReview() }.onChange(of: state.review?.id) {
            _, _ in changes.invalidateReview()
        }.onChange(of: assignmentWorkflow.review?.id) { _, _ in changes.invalidateReview() }.sheet(
            item: $changes.review
        ) { review in KeyboardChangesReviewSheet(workflow: changes, review: review) }
    }

    private var keyboardOperationStatus: String? {
        if let operation = assignmentWorkflow.operation { return operation }

        if state.isBusy { return state.status }

        if changes.isApplying { return changes.status }

        if !connection.isConnected { return connection.message }

        return nil
    }

    private var assignmentControls: KeyAssignmentsView {
        KeyAssignmentsView(workflow: assignmentWorkflow, editor: assignments, selectKey: selectKey)
    }

    private var previewLockIndicators: LockIndicators? {
        guard connection.isConnected else { return nil }

        return LockIndicators.latest(state.snapshot?.lockIndicators, assignmentWorkflow.snapshot?.lockIndicators)
    }

    private var sharedKeyboard: some View {
        VStack(alignment: .leading, spacing: 10) {
            AssignmentKeyboard(
                selected: assignments.selectedSource,
                planned: assignmentWorkflow.draftMatchesKeyboard(assignments.draft)
                    ? [] : Set(assignments.draft.assignments.map(\.source)), slots: state.previewSlots,
                unpainted: state.editorStyle == .animation ? [] : state.unpaintedKeys
            ) { source in
                selectKey(source)
                state.paintClickedKey(source)
            }

            Divider()
            KeyInspector(state: state, workflow: assignmentWorkflow, editor: assignments, activity: deviceActivity)
        }
    }

    private func selectKey(_ source: Int) {
        do { try assignments.select(source) } catch { state.error = error.localizedDescription }
    }

    private func refreshKeyboardState() async { await session.refresh(assignments: true) }

}

private struct OperationInProgressOverlay: View {
    let operation: DeviceOperation
    let status: String

    private var presentation: (title: String, icon: String, tint: Color, footnote: String) {
        switch operation {
        case .reading:
            (
                "Reading keyboard", "arrow.down.doc.fill", .cyan,
                "Keep the keyboard connected while its lighting state is verified."
            )
        case .preparingWrite: ("Preparing changes", "checklist", .blue, "Review your changes before applying them.")
        case .writing:
            (
                "Updating keyboard lighting", "arrow.up.doc.fill", .orange,
                "Keep the keyboard connected until the update finishes."
            )
        }
    }

    var body: some View {
        let presentation = presentation
        ZStack {
            Rectangle().fill(.black.opacity(0.28)).ignoresSafeArea()
            VStack(spacing: 16) {
                ZStack {
                    Circle().fill(presentation.tint.opacity(0.16)).frame(width: 76, height: 76)
                    Image(systemName: presentation.icon).font(.system(size: 30, weight: .semibold)).foregroundStyle(
                        presentation.tint)
                    ProgressView().controlSize(.small).offset(x: 27, y: 27)
                }

                VStack(spacing: 7) {
                    Text(presentation.title).font(.title2.bold())
                    Text(status).multilineTextAlignment(.center).foregroundStyle(.secondary)
                    Text(presentation.footnote).font(.caption).multilineTextAlignment(.center).foregroundStyle(
                        presentation.tint)
                }
            }.frame(maxWidth: 430).padding(.horizontal, 34).padding(.vertical, 28).background(
                .regularMaterial, in: RoundedRectangle(cornerRadius: 18)
            ).overlay { RoundedRectangle(cornerRadius: 18).stroke(presentation.tint.opacity(0.45), lineWidth: 1) }
                .shadow(color: .black.opacity(0.28), radius: 24, y: 10).padding(24)
        }.accessibilityElement(children: .combine).accessibilityLabel(
            "\(presentation.title). \(status). \(presentation.footnote)")
    }
}
