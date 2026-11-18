import Darwin
import Foundation

struct LightingProfileDraft: Codable, Equatable {
    var style: LightingEditorStyle
    var solidColor: RGB
    var brightness: Int
    var mode: BuiltInMode
    var animationColor: RGB
    var generated: Bool
    var speed: Int
    var direction: AnimationDirection

    // Only physical keys are portable; reserved bytes always come from the live capture.
    var keys: [LEDSlot]

    func mergingKeys(into baseline: [LEDSlot]) throws -> [LEDSlot] {
        try validate()
        guard baseline.count == 144 else {
            throw WorkflowError.message("A complete live lighting capture is required.")
        }

        var merged = baseline
        for (key, slot) in zip(LegacyKey.physical, keys) { merged[key.index] = slot }

        return merged
    }

    func validate() throws {
        guard keys.count == LegacyKey.physical.count, (0...16).contains(brightness), (2...16).contains(speed),
            style != .animation || brightness >= 1
        else { throw WorkflowError.message("The profile contains invalid lighting settings.") }
    }
}

enum ProfileFileReader {
    static func read(_ url: URL, limit: Int) throws -> Data {
        guard url.isFileURL else { throw WorkflowError.message("Choose a local profile file.") }

        let descriptor = url.withUnsafeFileSystemRepresentation { path in
            path.map { Darwin.open($0, O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC) } ?? -1
        }

        guard descriptor >= 0 else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno), userInfo: [NSFilePathErrorKey: url.path])
        }

        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? file.close() }

        // Validate the opened handle so a path replacement cannot turn the read into a blocking FIFO open.
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG else {
            throw WorkflowError.message("Choose a regular profile file.")
        }

        // Bound the read itself, including when the file grows after it was selected.
        var data = Data()
        while data.count <= limit {
            let chunk = try file.read(upToCount: min(65_536, limit + 1 - data.count)) ?? Data()
            if chunk.isEmpty { return data }

            data.append(chunk)
        }

        throw WorkflowError.message("The profile file is too large.")
    }
}

struct KeyboardProfile: Codable, Identifiable {
    private static let supportedFormat = "c989-profile-v2"
    static let maximumFileSize = 128_000
    private static let maximumNameLength = 80
    private static let maximumNameBytes = 320

    var format = supportedFormat
    var id = UUID()
    var name: String
    var draft: LightingProfileDraft? = nil
    var keyAssignments: KeyAssignmentDraft? = nil

    enum CodingKeys: String, CodingKey { case format, id, name, draft, keyAssignments }

    init(name: String, draft: LightingProfileDraft? = nil, keyAssignments: KeyAssignmentDraft? = nil) {
        self.name = name
        self.draft = draft
        self.keyAssignments = keyAssignments
    }

    init(from decoder: Decoder) throws {
        let fields = try decoder.container(keyedBy: CodingKeys.self)
        let storedFormat = try fields.decode(String.self, forKey: .format)
        guard storedFormat == Self.supportedFormat || storedFormat == "c989-lighting-profile-v1" else {
            throw WorkflowError.message("This profile format is not supported by this version of KeyboardLights.")
        }

        id = try fields.decode(UUID.self, forKey: .id)
        name = try fields.decode(String.self, forKey: .name)
        draft = try fields.decodeIfPresent(LightingProfileDraft.self, forKey: .draft)
        keyAssignments = try fields.decodeIfPresent(KeyAssignmentDraft.self, forKey: .keyAssignments)
        if storedFormat == "c989-lighting-profile-v1", draft == nil || keyAssignments != nil {
            throw WorkflowError.message("A legacy lighting profile cannot contain key assignments.")
        }

        try validate()
    }

    func validate() throws {
        guard format == Self.supportedFormat else {
            throw WorkflowError.message("This is not a supported KeyboardLights profile.")
        }

        guard name.utf8.count <= Self.maximumNameBytes, name.count <= Self.maximumNameLength,
            !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            name.rangeOfCharacter(from: .controlCharacters) == nil
        else { throw WorkflowError.message("Use a profile name between 1 and 80 characters.") }

        guard draft != nil || keyAssignments != nil else {
            throw WorkflowError.message("A profile must contain lighting or key assignments.")
        }

        try draft?.validate()
        try keyAssignments?.validate()
    }

    static func read(from url: URL) throws -> KeyboardProfile {
        try decode(ProfileFileReader.read(url, limit: maximumFileSize))
    }

    static func readForOpening(from url: URL) throws -> KeyboardProfile {
        let data = try ProfileFileReader.read(url, limit: maximumFileSize)
        return try decodeForOpening(data)
    }

    static func decodeForOpening(_ data: Data) throws -> KeyboardProfile {
        guard data.count <= maximumFileSize else { throw WorkflowError.message("The profile file is too large.") }

        struct Header: Decodable { let format: String }

        let header = try JSONDecoder().decode(Header.self, from: data)
        if header.format == "c989-lighting-draft-v1" { return try LightingDraftFile.decodeProfile(data) }

        return try decode(data)
    }

    static func decode(_ data: Data) throws -> KeyboardProfile {
        guard data.count <= maximumFileSize else { throw WorkflowError.message("The profile file is too large.") }

        return try JSONDecoder().decode(Self.self, from: data)
    }

    func encoded() throws -> Data {
        try validate()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(self)
    }
}

/// The former combined library is read only, solely to migrate existing installations.
enum LegacyProfileLibrary {
    static func read(from url: URL) throws -> [KeyboardProfile] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }

        let data = try ProfileFileReader.read(url, limit: 16 * 1024 * 1024)
        let profiles = try JSONDecoder().decode([KeyboardProfile].self, from: data)
        guard Set(profiles.map(\.id)).count == profiles.count else {
            throw WorkflowError.message("The saved profile library contains duplicate identifiers.")
        }

        return profiles
    }
}
