import Foundation

/// The bytes last opened or saved are the revision for coordinated file operations.
struct KeyboardProfileDocument {
    let url: URL
    let data: Data
    let profile: KeyboardProfile

    enum Settings {
        case assignments(KeyAssignmentDraft)
        case lighting(LightingProfileDraft, baseline: [LEDSlot])
    }

    enum Section: Hashable {
        case assignments
        case lighting

        func contains(_ profile: KeyboardProfile) -> Bool {
            self == .assignments ? profile.keyAssignments != nil : profile.draft != nil
        }
    }

    /// A save in the other section only advances this revision if our own saved settings are unchanged.
    func advancingRevision(after saved: KeyboardProfileDocument, for section: Section) -> KeyboardProfileDocument {
        guard url == saved.url else { return self }

        let unchanged: Bool
        switch section {
        case .assignments: unchanged = profile.keyAssignments == saved.profile.keyAssignments
        case .lighting: unchanged = profile.draft == saved.profile.draft
        }

        return unchanged ? saved : self
    }

    static func open(_ url: URL) throws -> KeyboardProfileDocument {
        let data = try ProfileFileReader.read(url, limit: KeyboardProfile.maximumFileSize)
        return KeyboardProfileDocument(
            url: url.standardizedFileURL, data: data, profile: try KeyboardProfile.decodeForOpening(data))
    }

    static func contents(updating settings: Settings, original: KeyboardProfileDocument?, newCopy: Bool) throws -> Data
    {
        switch settings {
        case .assignments(let draft):
            var profile = original?.profile ?? KeyboardProfile(name: "Key assignments")
            profile.keyAssignments = draft
            if newCopy { profile.id = UUID() }

            return try profile.encoded()
        case .lighting(let draft, let baseline):
            if var profile = original?.profile, profile.keyAssignments != nil {
                profile.draft = draft
                if newCopy { profile.id = UUID() }

                return try profile.encoded()
            }

            return try LightingDraftFile(
                draft: draft, baseline: baseline, name: original?.profile.name ?? "Lighting profile",
                id: newCopy ? UUID() : original?.profile.id ?? UUID()
            ).encoded()
        }
    }

    static func write(_ data: Data, to url: URL, replacing expected: Data?) throws -> KeyboardProfileDocument {
        let profile = try KeyboardProfile.decodeForOpening(data)
        try withUnchangedFile(at: url, expected: expected, options: .forReplacing) { coordinatedURL in
            try data.write(to: coordinatedURL, options: .atomic)
        }

        return KeyboardProfileDocument(url: url.standardizedFileURL, data: data, profile: profile)
    }

    func moveToTrash() throws {
        try Self.withUnchangedFile(at: url, expected: data, options: .forDeleting) { coordinatedURL in
            try FileManager.default.trashItem(at: coordinatedURL, resultingItemURL: nil)
        }
    }

    static func withUnchangedFile(
        at url: URL, expected: Data?, options: NSFileCoordinator.WritingOptions, perform action: (URL) throws -> Void
    ) throws {
        var coordinationError: NSError?
        var actionError: Error?
        NSFileCoordinator().coordinate(writingItemAt: url, options: options, error: &coordinationError) {
            coordinatedURL in
            do {
                let current =
                    FileManager.default.fileExists(atPath: coordinatedURL.path)
                    ? try ProfileFileReader.read(coordinatedURL, limit: KeyboardProfile.maximumFileSize) : nil
                guard current == expected else {
                    throw WorkflowError.message(
                        "The profile file changed or was removed outside this window. Open it again before replacing or deleting it, or use Save As to keep your current edits in a new file."
                    )
                }

                try action(coordinatedURL)
            } catch { actionError = error }
        }

        if let coordinationError { throw coordinationError }

        if let actionError { throw actionError }
    }
}
