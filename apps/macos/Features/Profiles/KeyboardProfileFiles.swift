import Combine
import Foundation

struct KeyboardProfileFile: Equatable, Identifiable {
    let url: URL
    let profile: KeyboardProfile
    let data: Data
    var id: URL { url }

    var name: String { url.deletingPathExtension().lastPathComponent }

    static func == (lhs: Self, rhs: Self) -> Bool { lhs.url == rhs.url && lhs.data == rhs.data }
}

/// Profiles are ordinary files; the index only remembers files opened outside the default folder.
@MainActor final class KeyboardProfileFiles: ObservableObject {
    @Published private(set) var entries: [KeyboardProfileFile] = []
    @Published private(set) var error: String?
    let directory: URL
    private let indexURL: URL
    private let legacyURL: URL
    private static let maximumIndexSize = 1_048_576

    private struct Index: Codable, Equatable {
        var files: [URL] = []
        var migratedLegacyLibrary = false
    }

    init(root: URL? = nil) {
        let root =
            root
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("KeyboardLights", isDirectory: true)
        directory = root.appendingPathComponent("Profiles", isDirectory: true)
        indexURL = root.appendingPathComponent("profile-files.json")
        legacyURL = root.appendingPathComponent("lighting-profiles.json")
        refresh()
    }

    func refresh() {
        var failures: [String] = []
        var urls = Set<URL>()
        do {
            let index = try readIndex()
            urls.formUnion(index.files.map { $0.standardizedFileURL })
            if !index.migratedLegacyLibrary {
                do { try migrateLegacyLibrary() } catch {
                    failures.append("Could not migrate the old profile library: \(error.localizedDescription)")
                }
            }
        } catch { failures.append("Could not read remembered profile locations: \(error.localizedDescription)") }

        do {
            if FileManager.default.fileExists(atPath: directory.path) {
                let local = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
                    .filter { $0.pathExtension.lowercased() == "json" }

                urls.formUnion(local.map { $0.standardizedFileURL })
            }
        } catch { failures.append("Could not read the profiles folder: \(error.localizedDescription)") }

        let previous = Dictionary(uniqueKeysWithValues: entries.map { ($0.url, $0) })
        var loaded: [KeyboardProfileFile] = []
        for url in urls.sorted(by: { $0.path < $1.path }) {
            guard FileManager.default.fileExists(atPath: url.path) else { continue }

            do {
                let data = try ProfileFileReader.read(url, limit: KeyboardProfile.maximumFileSize)
                if let cached = previous[url], cached.data == data {
                    loaded.append(cached)
                } else {
                    loaded.append(
                        KeyboardProfileFile(url: url, profile: try KeyboardProfile.decodeForOpening(data), data: data))
                }
            } catch { failures.append("\(url.lastPathComponent): \(error.localizedDescription)") }
        }

        loaded.sort { $0.name.localizedStandardCompare($1.name) == .orderedAscending }

        if loaded != entries { entries = loaded }

        let message = failures.isEmpty ? nil : failures.joined(separator: "\n")
        if error != message { error = message }
    }

    func remember(_ url: URL) throws {
        guard url.isFileURL else { throw WorkflowError.message("Choose a local profile file.") }

        if url.standardizedFileURL.deletingLastPathComponent() == directory.standardizedFileURL {
            refresh()
            return
        }

        try updateIndex { index in
            let url = url.standardizedFileURL
            if !index.files.contains(url) { index.files.append(url) }
        }

        refresh()
    }

    func forget(_ url: URL) throws {
        try updateIndex { index in index.files.removeAll { $0.standardizedFileURL == url.standardizedFileURL } }

        refresh()
    }

    private func readIndex() throws -> Index {
        guard FileManager.default.fileExists(atPath: indexURL.path) else { return Index() }

        let index = try JSONDecoder().decode(
            Index.self, from: ProfileFileReader.read(indexURL, limit: Self.maximumIndexSize))
        guard index.files.allSatisfy({ $0.isFileURL && $0.path.hasPrefix("/") }) else {
            throw WorkflowError.message("The profile index contains an invalid file location.")
        }

        return index
    }

    private func updateIndex(_ update: (inout Index) throws -> Void) throws {
        try FileManager.default.createDirectory(
            at: indexURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        var coordinationError: NSError?
        var writeError: Error?
        NSFileCoordinator().coordinate(writingItemAt: indexURL, options: .forReplacing, error: &coordinationError) {
            _ in
            do {
                var index = try readIndex()
                let previous = index
                try update(&index)
                guard index != previous else { return }

                let data = try JSONEncoder().encode(index)
                guard data.count <= Self.maximumIndexSize else {
                    throw WorkflowError.message("Too many saved profile locations.")
                }

                try data.write(to: indexURL, options: .atomic)
            } catch { writeError = error }
        }

        if let coordinationError { throw coordinationError }

        if let writeError { throw writeError }
    }

    private func migrateLegacyLibrary() throws {
        // Coordinate the migration and its marker together across app instances.
        try updateIndex { index in
            guard !index.migratedLegacyLibrary else { return }

            if FileManager.default.fileExists(atPath: legacyURL.path) {
                let legacy = try LegacyProfileLibrary.read(from: legacyURL)
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                for profile in legacy {
                    var name = profile.name.replacingOccurrences(of: "/", with: "-")
                    while name.utf8.count > 180 { name.removeLast() }

                    let data = try profile.encoded()
                    var suffix = 0
                    while true {
                        let recovery = suffix == 0 ? "" : " (recovered \(suffix))"
                        let url = directory.appendingPathComponent("\(name)-\(profile.id.uuidString)\(recovery).json")
                        if FileManager.default.fileExists(atPath: url.path) {
                            // A partial earlier migration may already have written this exact profile.
                            if (try? ProfileFileReader.read(url, limit: KeyboardProfile.maximumFileSize)) == data {
                                break
                            }

                            suffix += 1
                            continue
                        }

                        try data.write(to: url, options: .withoutOverwriting)
                        break
                    }
                }
            }

            // Keep the original library for recovery, but never recreate files the user has deleted.
            index.migratedLegacyLibrary = true
        }
    }
}
