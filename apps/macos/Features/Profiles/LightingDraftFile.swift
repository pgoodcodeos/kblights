import Foundation

/// The lighting draft format shared with the Linux editor.
struct LightingDraftFile: Codable {
    var format = "c989-lighting-draft-v1"
    var settings: Settings
    var name: String?
    var id: UUID?

    enum CodingKeys: String, CodingKey { case format, settings, name, id }

    init(from decoder: Decoder) throws {
        let fields = try decoder.container(keyedBy: CodingKeys.self)
        format = try fields.decode(String.self, forKey: .format)
        settings = try fields.decode(Settings.self, forKey: .settings)
        if fields.contains(.name) || fields.contains(.id) {
            name = try fields.decode(String.self, forKey: .name)
            id = try fields.decode(UUID.self, forKey: .id)
        }
    }

    struct Settings: Codable {
        var style: String
        var color: String
        var brightness: Int
        var mode: BuiltInMode
        var speed: Int
        var generated: Bool
        var direction: AnimationDirection
        var slots: [Slot]
        var solidColor: String?
        var animationColor: String?
    }

    struct Slot: Codable {
        var enabled: Bool
        var color: String
    }

    init(draft: LightingProfileDraft, baseline: [LEDSlot], name: String? = nil, id: UUID? = nil) throws {
        self.name = name
        self.id = id
        let slots = try draft.mergingKeys(into: baseline)
        let style: String
        switch draft.style {
        case .solid: style = "solid"
        case .animation: style = "animation"
        case .perKey: style = "perKey"
        }

        settings = Settings(
            style: style, color: Self.hex(draft.style == .animation ? draft.animationColor : draft.solidColor),
            brightness: draft.brightness, mode: draft.mode, speed: draft.speed, generated: draft.generated,
            direction: draft.direction, slots: slots.map { Slot(enabled: $0.enabled, color: Self.hex($0.rgb)) },
            solidColor: Self.hex(draft.solidColor), animationColor: Self.hex(draft.animationColor))
    }

    func lightingDraft() throws -> LightingProfileDraft {
        let style: LightingEditorStyle
        switch settings.style {
        case "solid": style = .solid
        case "animation": style = .animation
        case "perKey": style = .perKey
        default: throw WorkflowError.message("Invalid lighting draft style.")
        }

        guard format == "c989-lighting-draft-v1", settings.slots.count == 144 else {
            throw WorkflowError.message("Choose a complete KeyboardLights lighting draft.")
        }

        let color = try Self.rgb(settings.color)
        let solidColor = try settings.solidColor.map(Self.rgb) ?? color
        let animationColor = try settings.animationColor.map(Self.rgb) ?? color
        let slots = try settings.slots.map { LEDSlot(enabled: $0.enabled, rgb: try Self.rgb($0.color)) }

        let draft = LightingProfileDraft(
            style: style, solidColor: style == .solid ? color : solidColor, brightness: settings.brightness,
            mode: settings.mode, animationColor: style == .animation ? color : animationColor,
            generated: settings.generated, speed: settings.speed, direction: settings.direction,
            keys: LegacyKey.physical.map { slots[$0.index] })
        try draft.validate()
        guard (name == nil) == (id == nil) else {
            throw WorkflowError.message("A named lighting profile needs both a name and an identifier.")
        }

        if let name { try KeyboardProfile(name: name, draft: draft).validate() }

        return draft
    }

    func encoded() throws -> Data {
        _ = try lightingDraft()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(self)
    }

    static func decodeProfile(_ data: Data) throws -> KeyboardProfile {
        let file = try decodeContents(data)
        var profile = KeyboardProfile(name: file.name ?? "Lighting draft", draft: try file.lightingDraft())
        if let id = file.id { profile.id = id }

        return profile
    }

    static func decode(_ data: Data) throws -> LightingDraftFile {
        let file = try decodeContents(data)
        _ = try file.lightingDraft()
        return file
    }

    private static func decodeContents(_ data: Data) throws -> LightingDraftFile {
        guard data.count <= KeyboardProfile.maximumFileSize else {
            throw WorkflowError.message("The lighting draft file is too large.")
        }

        return try JSONDecoder().decode(Self.self, from: data)
    }

    private static func hex(_ color: RGB) -> String {
        String(format: "#%02X%02X%02X", color.red, color.green, color.blue)
    }

    private static func rgb(_ value: String) throws -> RGB {
        let digits = value.dropFirst()
        guard value.first == "#", digits.utf8.count == 6,
            digits.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }),
            let number = UInt32(digits, radix: 16)
        else { throw WorkflowError.message("Invalid lighting draft color.") }

        return RGB(red: UInt8((number >> 16) & 255), green: UInt8((number >> 8) & 255), blue: UInt8(number & 255))
    }
}
