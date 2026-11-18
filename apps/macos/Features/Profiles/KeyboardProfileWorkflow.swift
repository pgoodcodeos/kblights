import AppKit
import Combine
import UniformTypeIdentifiers

/// Owns each window's profile documents and pending file actions independently of keyboard transfers.
@MainActor final class KeyboardProfileWorkflow: ObservableObject {
    typealias Section = KeyboardProfileDocument.Section

    struct LoadRequest {
        let document: KeyboardProfileDocument
        let section: Section
    }

    @Published var activeSection: Section = .lighting
    @Published var error: String?
    @Published private(set) var message: String?
    @Published private(set) var pendingNew: Section?
    @Published private(set) var pendingLoad: LoadRequest?
    @Published private(set) var pendingDeletion: KeyboardProfileDocument?
    @Published private var documents: [Section: KeyboardProfileDocument] = [:]
    @Published private(set) var isChoosingFile = false
    private let isAvailable: @MainActor () -> Bool
    private let files: KeyboardProfileFiles
    private let state: KeyboardState
    private let editor: KeyAssignmentEditor

    init(
        files: KeyboardProfileFiles, state: KeyboardState, editor: KeyAssignmentEditor,
        isAvailable: @escaping @MainActor () -> Bool = { true }
    ) {
        self.isAvailable = isAvailable
        self.files = files
        self.state = state
        self.editor = editor
    }

    private var canPerformActions: Bool { isAvailable() && !state.isBusy && !isChoosingFile }

    var isReady: Bool { canPerformActions && pendingLoad == nil && pendingNew == nil && pendingDeletion == nil }

    func document(for section: Section) -> KeyboardProfileDocument? { documents[section] }

    func canLoad(_ section: Section) -> Bool { section == .assignments || state.hasSnapshot }

    func canSave(_ section: Section) -> Bool {
        section == .assignments || (state.hasLightingDraft && !state.hasUnpaintedKeys)
    }

    func open(_ section: Section) {
        guard isReady, canLoad(section) else { return }

        message = nil
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.directoryURL = files.directory
        panel.title = section == .assignments ? "Open assignment profile" : "Open lighting draft"
        isChoosingFile = true
        let response = panel.runModal()
        isChoosingFile = false
        guard response == .OK, let url = panel.url else { return }

        guard isReady else {
            error = "The keyboard is busy or unavailable. Your draft is unchanged; try opening the profile again."
            return
        }

        open(url, section: section)
    }

    func open(_ url: URL, section: Section) {
        guard isReady, canLoad(section) else { return }

        do {
            let document = try KeyboardProfileDocument.open(url)
            guard section.contains(document.profile) else {
                throw WorkflowError.message(
                    "This profile does not contain \(section == .assignments ? "key assignments" : "lighting").")
            }

            let replacesDraft = hasUnsavedChanges(section)
            let request = LoadRequest(document: document, section: section)
            if replacesDraft { pendingLoad = request } else { load(request) }
        } catch { self.error = error.localizedDescription }
    }

    func newProfile(_ section: Section) {
        guard isReady, canLoad(section) else { return }

        if hasUnsavedChanges(section) { pendingNew = section } else { createProfile(section) }
    }

    func hasUnsavedChanges(_ section: Section) -> Bool {
        let changed: Bool
        switch section {
        case .assignments:
            changed = editor.draft != (documents[section]?.profile.keyAssignments ?? KeyAssignmentDraft())
        case .lighting:
            if !state.unpaintedKeys.isEmpty || state.hasUnsavedStaticBrightness { return true }

            if let saved = documents[section]?.profile.draft {
                changed = (try? state.savedLightingDraft()) != saved
            } else {
                changed = state.hasUnsavedLightingChanges
            }
        }

        if changed { return true }

        guard let document = documents[section] else { return false }

        // Recheck disk only when the draft would otherwise be safe to discard.
        return (try? ProfileFileReader.read(document.url, limit: KeyboardProfile.maximumFileSize)) != document.data
    }

    private func createProfile(_ section: Section) {
        if section == .assignments {
            editor.clear()
        } else {
            state.selectLightingMode(128)
            state.clearPaint()
        }

        documents[section] = nil
        message = "New profile started. Your keyboard has not changed."
    }

    func cancelLoad() {
        pendingLoad = nil
        pendingNew = nil
    }

    func confirmLoad() {
        guard canPerformActions else {
            cancelLoad()
            error = "The keyboard is busy or unavailable. Your draft is unchanged; try the profile action again."
            return
        }

        if let section = pendingNew {
            pendingNew = nil
            createProfile(section)
            return
        }

        guard let request = pendingLoad else { return }

        pendingLoad = nil
        load(request)
    }

    private func load(_ request: LoadRequest) {
        do {
            switch request.section {
            case .assignments: if let draft = request.document.profile.keyAssignments { try editor.load(draft) }

            case .lighting: try state.loadLightingProfile(request.document.profile)
            }

            documents[request.section] = request.document
            remember(request.document.url)
            message = "Draft loaded. Review before applying."
        } catch { self.error = error.localizedDescription }
    }

    func save(_ section: Section, asNewFile: Bool) {
        guard isReady, canSave(section) else { return }

        message = nil
        let current = documents[section]?.url
        if !asNewFile, let current {
            save(section, to: current)
            return
        }

        do {
            try FileManager.default.createDirectory(at: files.directory, withIntermediateDirectories: true)
            let panel = NSSavePanel()
            panel.allowedContentTypes = [.json]
            panel.title = section == .assignments ? "Save assignment profile as" : "Save lighting profile as"
            panel.directoryURL = current?.deletingLastPathComponent() ?? files.directory
            panel.nameFieldStringValue =
                current?.lastPathComponent ?? (section == .assignments ? "Key assignments.json" : "Lighting.json")
            isChoosingFile = true
            let response = panel.runModal()
            isChoosingFile = false
            guard response == .OK, let url = panel.url else { return }

            guard isReady else {
                error = "The keyboard is busy or unavailable. Your draft was kept but not saved; try saving again."
                return
            }

            save(section, to: url)
        } catch { self.error = error.localizedDescription }
    }

    func save(_ section: Section, to destination: URL) {
        guard isReady, canSave(section) else { return }

        do {
            let url = destination.standardizedFileURL
            let original = documents[section]
            let settings: KeyboardProfileDocument.Settings =
                section == .assignments
                ? .assignments(editor.draft) : .lighting(try state.savedLightingDraft(), baseline: state.slots)
            let data = try KeyboardProfileDocument.contents(
                updating: settings, original: original, newCopy: url != original?.url)
            let expected =
                url == original?.url
                ? original?.data
                : FileManager.default.fileExists(atPath: url.path)
                    ? try ProfileFileReader.read(url, limit: KeyboardProfile.maximumFileSize) : nil
            let saved = try KeyboardProfileDocument.write(data, to: url, replacing: expected)
            var updated = documents
            for (storedSection, document) in documents {
                updated[storedSection] = document.advancingRevision(after: saved, for: storedSection)
            }

            updated[section] = saved
            documents = updated
            remember(url)
            message = "Saved \(url.lastPathComponent)."
        } catch { self.error = error.localizedDescription }
    }

    func requestDeletion(_ section: Section) {
        guard isReady else { return }

        pendingDeletion = documents[section]
    }

    var deletionMessage: String {
        let profile = pendingDeletion?.profile
        let contents =
            profile?.draft != nil && profile?.keyAssignments != nil
            ? " This file contains both lighting and assignments." : ""
        return "Move \(pendingDeletion?.url.lastPathComponent ?? "this profile") to the Trash?" + contents
            + " Your current edits and keyboard settings will be kept."
    }

    func cancelDeletion() { pendingDeletion = nil }

    func confirmDeletion() {
        guard let document = pendingDeletion else { return }

        pendingDeletion = nil
        guard isReady else {
            error = "The keyboard is busy or unavailable. The profile file was kept; try deleting it again."
            return
        }

        do {
            try document.moveToTrash()
            documents = documents.filter { $0.value.url != document.url }

            message = "Moved \(document.url.lastPathComponent) to the Trash."
            do { try files.forget(document.url) } catch {
                files.refresh()
                self.error =
                    "The file was moved to Trash, but its remembered location could not be removed: "
                    + error.localizedDescription
            }
        } catch {
            files.refresh()
            self.error = error.localizedDescription
        }
    }

    private func remember(_ url: URL) {
        do { try files.remember(url) } catch {
            self.error =
                "The profile file was opened or saved, but its location could not be remembered: "
                + error.localizedDescription
        }
    }
}
