import C989Native
import Foundation
import KeyboardData
import SwiftUI

struct RGB: Codable, Equatable, Sendable {
    var red: UInt8
    var green: UInt8
    var blue: UInt8

    var color: Color { Color(red: Double(red) / 255, green: Double(green) / 255, blue: Double(blue) / 255) }
}

struct LEDSlot: Codable, Equatable, Sendable {
    var enabled: Bool
    var rgb: RGB
}

enum LightingPaintTool: String, CaseIterable {
    case pointer = "Select keys"
    case brush = "Paint brush"
    case bucket = "Paint bucket"
}

enum LightingEditorStyle: String, CaseIterable, Identifiable, Codable, Sendable {
    case solid = "Solid color"
    case animation = "Animation"
    case perKey = "Per-key"

    var id: String { rawValue }
}

enum AnimationColorPolicy: Sendable {
    case generated
    case configurable
}

enum AnimationDirection: UInt8, CaseIterable, Identifiable, Codable, Sendable {
    case right = 0
    case left = 1

    var id: UInt8 { rawValue }

    var name: String { self == .right ? "Right" : "Left" }
}

enum BuiltInMode: UInt8, CaseIterable, Identifiable, Codable, Sendable {
    case colourful = 0x07
    case breath = 0x08
    case spectrum = 0x09
    case volcano = 0x0A
    case rolling = 0x0B
    case rotating = 0x0C
    case flowing = 0x0D
    case pulsating = 0x0E
    case tilt = 0x0F

    var id: UInt8 { rawValue }

    var name: String {
        switch self {
        case .colourful: "Colourful"
        case .breath: "Breath"
        case .spectrum: "Spectrum"
        case .volcano: "Volcano"
        case .rolling: "Rolling"
        case .rotating: "Rotating"
        case .flowing: "Flowing"
        case .pulsating: "Pulsating"
        case .tilt: "Tilt"
        }
    }

    var summary: String {
        switch self {
        case .colourful: "Rainbow colors cycle with diagonal phase motion."
        case .breath: "All keys fade in and out."
        case .spectrum: "All keys cycle through the spectrum together."
        case .volcano: "Color moves outward from the keyboard center."
        case .rolling: "Full-height color bands travel across the keyboard."
        case .rotating: "Grouped rings sweep around the keyboard."
        case .flowing: "Rows illuminate in a traveling sequence."
        case .pulsating: "Brightness and color pulse across the keys."
        case .tilt: "Grouped colors move along a diagonal."
        }
    }

    var colorPolicy: AnimationColorPolicy {
        switch self {
        case .colourful, .spectrum: .generated
        default: .configurable
        }
    }

    /// Rolling applies direction reliably on initial entry. Flowing and Tilt
    /// only honor Left on a later same-mode update, while Volcano and Rotating
    /// showed no useful visible direction difference, so those controls are not
    /// exposed as dependable product features.
    var supportsDirection: Bool { self == .rolling }
}

struct BuiltInSettings: Equatable, Sendable {
    var mode: BuiltInMode
    var color: RGB
    var usesGeneratedColor: Bool
    var brightness: Int
    var speed: Int
    var direction: AnimationDirection
}

struct LegacyKey: Identifiable, Sendable {
    let index: Int
    let name: String
    let row: Int
    let column: Int
    let display: KeyboardKeyPosition
    var id: Int { index }

    private struct Layout: Decodable {
        struct Slot: Decodable {
            let ledIndex: Int
            let matrixRow: Int
            let matrixColumn: Int
            let name: String?
            let physicalKey: Bool
            let lightingUiExposed: Bool
            let display: KeyboardKeyPosition?
        }

        let slots: [Slot]
    }

    private static let slots: [Layout.Slot] = {
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let layout = try decoder.decode(Layout.self, from: KeyboardResources.data(named: "layout.json"))
            guard layout.slots.count == 144, Set(layout.slots.map(\.ledIndex)) == Set(0..<144),
                layout.slots.allSatisfy({ slot in
                    slot.ledIndex == slot.matrixRow * 18 + slot.matrixColumn
                        && (!slot.physicalKey || (slot.name?.isEmpty == false && slot.display?.isValid == true))
                        && (!slot.lightingUiExposed || slot.physicalKey)
                })
            else { throw CocoaError(.fileReadCorruptFile) }

            return layout.slots.sorted { $0.ledIndex < $1.ledIndex }
        } catch { fatalError("Required keyboard layout is missing or invalid: \(error)") }
    }()

    static let exposed = slots.filter { $0.lightingUiExposed }.map {
        LegacyKey(index: $0.ledIndex, name: $0.name!, row: $0.matrixRow, column: $0.matrixColumn, display: $0.display!)
    }

    static let unexposed = slots.filter { $0.physicalKey && !$0.lightingUiExposed }.map {
        LegacyKey(index: $0.ledIndex, name: $0.name!, row: $0.matrixRow, column: $0.matrixColumn, display: $0.display!)
    }

    // Reserved matrix slots are deliberately excluded so a persistent-static
    // fill cannot alter them.
    static let physical: [LegacyKey] = (exposed + unexposed).sorted { $0.index < $1.index }

    // Shared key edges, including the layout's quarter-key horizontal gutters.
    static let neighbors: [Int: Set<Int>] = Dictionary(
        uniqueKeysWithValues: physical.map { key in
            let a = key.display
            let adjacent = physical.filter { other in
                guard other.index != key.index else { return false }

                let b = other.display
                let horizontal =
                    ((0...1).contains(b.left - a.left - a.width) || (0...1).contains(a.left - b.left - b.width))
                    && min(a.top + a.height, b.top + b.height) > max(a.top, b.top)
                let vertical =
                    (a.top + a.height == b.top || b.top + b.height == a.top)
                    && min(a.left + a.width, b.left + b.width) > max(a.left, b.left)
                return horizontal || vertical
            }

            return (key.index, Set(adjacent.map(\.index)))
        })

    static let physicalByIndex = Dictionary(uniqueKeysWithValues: physical.map { ($0.index, $0) })
    static let physicalIndices = Set(physicalByIndex.keys)
}

enum LightingCandidateError: LocalizedError {
    case wrongSize
    case invalidStructure(String)

    var errorDescription: String? {
        switch self {
        case .wrongSize: "Channel 1 must be exactly 1,152 bytes."
        case .invalidStructure(let reason): "The captured lighting image is not structurally valid: \(reason)"
        }
    }
}

enum LightingCandidateBuilder {
    static func slots(from data: Data) throws -> [LEDSlot] {
        try validate(data)
        return (0..<144).map { index in
            let offset = 0x200 + index * 4
            return LEDSlot(
                enabled: data[offset] == 0x80,
                rgb: RGB(red: data[offset + 1], green: data[offset + 2], blue: data[offset + 3]))
        }
    }

    static func brightness(from data: Data) throws -> Int {
        try validate(data)
        return Int(data[0x449])
    }

    static func build(from baseline: Data, slots: [LEDSlot], brightness: Int) throws -> Data {
        try validate(baseline)
        guard slots.count == 144 else { throw LightingCandidateError.invalidStructure("expected 144 LED slots") }

        guard (0...16).contains(brightness) else {
            throw LightingCandidateError.invalidStructure("brightness is outside 0–16")
        }

        var candidate = baseline
        for (index, slot) in slots.enumerated() {
            let offset = 0x200 + index * 4
            candidate[offset] = slot.enabled ? 0x80 : 0x00
            candidate[offset + 1] = slot.rgb.red
            candidate[offset + 2] = slot.rgb.green
            candidate[offset + 3] = slot.rgb.blue
        }

        candidate[0x440] = 0x80
        candidate[0x449] = UInt8(brightness)
        return candidate
    }

    /// Encodes a durable static color through the reconnect-safe UserDefine
    /// renderer. Firmware 1.38's built-in Static tags retain their bytes but can
    /// finish the startup wave blank, so production code must not use tags 1–6
    /// for a user-facing persistent static setting.
    static func buildPersistentStatic(from baseline: Data, color: RGB, brightness: Int) throws -> Data {
        var slots = try slots(from: baseline)
        for index in LegacyKey.physicalIndices { slots[index] = LEDSlot(enabled: true, rgb: color) }

        return try build(from: baseline, slots: slots, brightness: brightness)
    }

    static func buildBuiltIn(from baseline: Data, settings: BuiltInSettings) throws -> Data {
        try validate(baseline)
        guard (1...16).contains(settings.brightness) else {
            throw LightingCandidateError.invalidStructure("brightness is outside 1–16")
        }

        guard (2...16).contains(settings.speed) else {
            throw LightingCandidateError.invalidStructure("animation speed is outside the usable 2–16 range")
        }

        var record = [UInt8](repeating: 0, count: 16)
        record[0] = settings.mode.rawValue
        if settings.mode.colorPolicy == .configurable {
            if settings.usesGeneratedColor {
                record[8] = 1
            } else {
                record[1] = settings.color.red
                record[2] = settings.color.green
                record[3] = settings.color.blue
            }
        }

        record[9] = UInt8(settings.brightness)
        record[10] = UInt8(settings.speed)
        record[11] = settings.mode.supportsDirection ? settings.direction.rawValue : 0
        record[14] = 0xAA
        record[15] = 0x55

        var candidate = baseline
        candidate.replaceSubrange(0x440..<0x450, with: record)
        return candidate
    }

    static func persistentStaticColor(forLegacyTag tag: UInt8) -> RGB? {
        let palette: [RGB] = [
            RGB(red: 0xFF, green: 0xFF, blue: 0xFF), RGB(red: 0x00, green: 0xFF, blue: 0xFF),
            RGB(red: 0xFF, green: 0x00, blue: 0x00), RGB(red: 0xFF, green: 0xFF, blue: 0x00),
            RGB(red: 0x00, green: 0x00, blue: 0xFF), RGB(red: 0xFF, green: 0x00, blue: 0xFF)
        ]
        guard (1...6).contains(tag) else { return nil }

        return palette[Int(tag - 1)]
    }

    static func validate(_ data: Data) throws {
        guard data.count == 0x480 else { throw LightingCandidateError.wrongSize }

        let tags: [UInt8] = [1, 2, 3, 4, 5, 6, 7, 0x80]
        for record in 0..<8 {
            let offset = record * 64
            guard data[offset] == tags[record] else {
                throw LightingCandidateError.invalidStructure("record \(record) tag")
            }

            guard data[offset + 62] == 0xAA, data[offset + 63] == 0x55 else {
                throw LightingCandidateError.invalidStructure("record \(record) trailer")
            }
        }

        for index in 0..<144 {
            let control = data[0x200 + index * 4]
            guard control == 0 || control == 0x80 else {
                throw LightingCandidateError.invalidStructure("slot \(index) control")
            }
        }

        let selected = data[0x440]
        guard (1...15).contains(selected) || selected == 0x80 else {
            throw LightingCandidateError.invalidStructure("selected tag")
        }

        guard data[0x47E] == 0xAA, data[0x47F] == 0x55 else {
            throw LightingCandidateError.invalidStructure("selected trailer")
        }

        let compactTrailer = (data[0x44E], data[0x44F])
        guard compactTrailer == (0, 0) || compactTrailer == (0xAA, 0x55) else {
            throw LightingCandidateError.invalidStructure("compact selected trailer")
        }
    }
}

struct Capture: Identifiable, Sendable {
    let id = UUID()
    let channel: Int
    let blocks: Int
    let bytes: Int
    let additiveChecksum: String
    let verified: Bool
    let location: String
    let revision: String
    let date: Date

    init(verifiedLighting data: Data, location: String) {
        channel = 1
        blocks = 18
        bytes = data.count
        let checksum = data.reduce(UInt64(0)) { $0 + UInt64($1) }

        additiveChecksum = String(format: "%08llX", checksum)
        verified = true
        self.location = location
        revision = "0138"
        date = .now
    }
}

struct DeviceSnapshot: Equatable, Sendable {
    let location: String
    let lightingData: Data
    var lockIndicators: LockIndicators? = nil
    var captures: [Capture] { [Capture(verifiedLighting: lightingData, location: location)] }
}

struct WriteReview: Identifiable, Sendable {
    let id = UUID()
    let candidate: Data
    let location: String
    let changedByteCount: Int
    let changedKeys: [String]
    let candidateChecksum: UInt64
    let selectionName: String
}

struct LightingState {
    struct Record: Identifiable {
        let id: Int
        let name: String
        let color1: Color
        let brightness: Int
        let speed: Int
    }

    let records: [Record]
    let selectedName: String
    let enabledCount: Int

    init?(data: Data) {
        guard (try? LightingCandidateBuilder.validate(data)) != nil else { return nil }

        let names = [
            1: "Static 1", 2: "Static 2", 3: "Static 3", 4: "Static 4", 5: "Static 5", 6: "Static 6", 7: "Colourful",
            8: "Breath", 9: "Spectrum", 10: "Volcano", 11: "Rolling", 12: "Rotating", 13: "Flowing", 14: "Pulsating",
            15: "Tilt", 0x80: "UserDefine"
        ]
        func color(_ at: Int) -> Color {
            Color(red: Double(data[at]) / 255, green: Double(data[at + 1]) / 255, blue: Double(data[at + 2]) / 255)
        }

        records = (0..<8).map { index in
            let offset = index * 64
            let tag = Int(data[offset])
            return Record(
                id: tag, name: names[tag] ?? String(format: "Unknown (%02X)", tag), color1: color(offset + 1),
                brightness: Int(data[offset + 58]), speed: Int(data[offset + 59]))
        }

        selectedName = names[Int(data[0x440])] ?? String(format: "Unknown (%02X)", data[0x440])
        enabledCount = (0..<144).filter { data[0x200 + $0 * 4] == 0x80 }.count
    }
}

enum DeviceOperation: Equatable, Sendable {
    case reading
    case preparingWrite
    case writing
}

@MainActor final class KeyboardState: ObservableObject {
    @Published private(set) var activeOperation: DeviceOperation?
    @Published var status = "No lighting state loaded"
    @Published var error: String?
    @Published var warning: String?
    @Published var needsInputMonitoringAccess = false
    @Published var captures: [Capture] = []
    @Published private(set) var currentKeyboardSlots: [LEDSlot] = []
    @Published var lighting: LightingState?
    @Published var slots: [LEDSlot] = []
    @Published var brightness = 16
    @Published var editorStyle: LightingEditorStyle = .solid {
        didSet { if editorStyle == .animation { paintTool = .pointer } }
    }

    @Published private(set) var paintTool: LightingPaintTool = .pointer
    @Published var solidColor = RGB(red: 0xFF, green: 0xFF, blue: 0xFF)
    @Published var animationMode: BuiltInMode = .colourful
    @Published var animationColor = RGB(red: 0, green: 255, blue: 0)
    @Published var animationUsesGeneratedColor = true
    @Published var animationSpeed = 12
    @Published var animationDirection: AnimationDirection = .right
    @Published var review: WriteReview?
    @Published var confirmsLightingDiscard = false
    @Published private(set) var keyPaintColor = RGB(red: 0, green: 255, blue: 255)
    @Published private(set) var unpaintedKeys = Set<Int>()
    @Published private(set) var lastUsedColor = RGB(red: 0, green: 255, blue: 255)
    @Published private(set) var bucketColor = RGB(red: 0, green: 255, blue: 255)
    @Published private(set) var lastBucketColor = RGB(red: 0, green: 255, blue: 255)
    private let preferences: UserDefaults?
    private var staticBrightness: Int?

    private let activity: KeyboardDeviceActivity
    private var ownsActivity = false

    private let service: any LightingDeviceService
    private let root: URL
    private let checkAccess: @MainActor () -> Bool

    init(
        activity: KeyboardDeviceActivity = .shared, service: any LightingDeviceService = NativeLightingDeviceService(),
        root: URL? = nil, checkAccess: @escaping @MainActor () -> Bool = { KeyboardPermission.hasSystemAccess },
        preferences: UserDefaults? = nil
    ) {
        self.preferences = preferences
        if let hex = preferences?.string(forKey: "lastLightingColor"), let color = RGB(hex: hex), !color.isOff {
            lastUsedColor = color
            keyPaintColor = color
        }

        if let hex = preferences?.string(forKey: "lastBucketColor"), let color = RGB(hex: hex), !color.isOff {
            lastBucketColor = color
            bucketColor = color
        }

        self.activity = activity
        self.service = service
        self.root =
            root
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("KeyboardLights/Captures", isDirectory: true)
        self.checkAccess = checkAccess
    }

    @Published private(set) var snapshot: DeviceSnapshot? {
        didSet { currentKeyboardSlots = Self.keyboardPreview(from: snapshot) }
    }

    private var cleanDraft: LightingProfileDraft?
    var isBusy: Bool { activeOperation != nil }

    var hasSnapshot: Bool { snapshot != nil }

    var hasLightingDraft: Bool { cleanDraft != nil && slots.count == 144 }

    var hasUnsavedLightingChanges: Bool {
        guard let cleanDraft else { return false }

        return !unpaintedKeys.isEmpty || hasUnsavedStaticBrightness || currentDraft != cleanDraft
    }

    var hasUnsavedStaticBrightness: Bool {
        editorStyle == .animation && staticBrightness.map { $0 != cleanDraft?.brightness } == true
    }

    var hasLightingChangesToApply: Bool {
        guard !hasUnpaintedKeys, hasUnsavedLightingChanges, let snapshot else { return false }

        guard let candidate = try? lightingCandidate(from: snapshot.lightingData) else { return true }

        return candidate != snapshot.lightingData
    }

    var hasUnpaintedKeys: Bool { editorStyle != .animation && !unpaintedKeys.isEmpty }

    var selectedLightingMode: Int { editorStyle == .animation ? Int(animationMode.rawValue) : 128 }

    var selectedPaintColor: RGB { paintTool == .bucket ? bucketColor : keyPaintColor }

    var paintColorName: String {
        LightingColorPreset.all.first { $0.rgb == selectedPaintColor }?.name ?? selectedPaintColor.hex
    }

    var isPointerSelected: Bool { editorStyle == .animation || paintTool == .pointer }

    func selectPointer() { paintTool = .pointer }

    func paintClickedKey(_ source: Int) {
        guard !isPointerSelected else { return }

        if paintTool == .bucket { floodFill(on: source) } else { paintSelectedColor(on: source) }
    }

    func selectPaintColor(_ color: RGB) {
        paintTool = editorStyle == .animation ? .pointer : .brush
        keyPaintColor = color
    }

    func selectBucketColor(_ color: RGB) {
        bucketColor = color
        paintTool = editorStyle == .animation ? .pointer : .bucket
    }

    func selectPaintTool(_ tool: LightingPaintTool) { paintTool = editorStyle == .animation ? .pointer : tool }

    func floodFill(on source: Int) {
        guard hasSnapshot, !isBusy, editorStyle != .animation, LegacyKey.physicalIndices.contains(source),
            slots.count == 144
        else { return }

        let layout = paintLayout()
        func regionColor(_ index: Int) -> RGB? {
            if unpaintedKeys.contains(index) { return nil }

            return layout[index].enabled ? layout[index].rgb : RGB(red: 0, green: 0, blue: 0)
        }

        let original = regionColor(source)
        var visited: Set<Int> = [source]
        var targets = Set<Int>()
        var pending = [source]
        while let index = pending.popLast() {
            guard regionColor(index) == original else { continue }

            targets.insert(index)
            for neighbor in LegacyKey.neighbors[index, default: []] where visited.insert(neighbor).inserted {
                pending.append(neighbor)
            }
        }

        stageSelectedPaint(on: targets, color: bucketColor, tool: .bucket)
    }

    func selectLightingMode(_ mode: Int) {
        guard !isBusy else { return }

        if mode == 128 {
            let wasAnimation = editorStyle == .animation
            ensurePaintLayout()
            if wasAnimation, let staticBrightness { brightness = staticBrightness }
        } else if let animation = BuiltInMode(rawValue: UInt8(clamping: mode)) {
            if editorStyle != .animation {
                staticBrightness = brightness
                ensurePaintLayout()
            }

            if editorStyle != .animation || animationMode != animation {
                animationUsesGeneratedColor = true
                animationDirection = .right
                brightness = 16
            }

            editorStyle = .animation
            animationMode = animation
        } else {
            return
        }

        draftChanged()
    }

    private func paintLayout() -> [LEDSlot] {
        var layout = slots
        if editorStyle == .solid {
            for key in LegacyKey.physical where layout.indices.contains(key.index) {
                layout[key.index] = LEDSlot(enabled: true, rgb: solidColor)
            }
        }

        return layout
    }

    private func ensurePaintLayout() {
        if editorStyle == .solid { slots = paintLayout() }

        editorStyle = .perKey
    }

    func clearPaint() {
        guard hasSnapshot, !isBusy, editorStyle != .animation else { return }

        ensurePaintLayout()
        unpaintedKeys = LegacyKey.physicalIndices
        draftChanged()
    }

    func fillPaint(overwriting: Bool = false) {
        guard hasSnapshot, !isBusy, editorStyle != .animation else { return }

        let targets = overwriting ? LegacyKey.physicalIndices : unpaintedKeys
        guard !targets.isEmpty else { return }

        stageSelectedPaint(on: targets)
    }

    func paintSelectedColor(on source: Int) {
        guard hasSnapshot, !isBusy, editorStyle != .animation, LegacyKey.physicalIndices.contains(source),
            slots.indices.contains(source)
        else { return }

        stageSelectedPaint(on: [source])
    }

    private func paintSlot(_ source: Int, in draft: LightingProfileDraft) -> LEDSlot {
        if draft.style == .solid { return LEDSlot(enabled: true, rgb: draft.solidColor) }

        let position = LegacyKey.physical.firstIndex { $0.index == source }

        guard let position, draft.keys.indices.contains(position) else {
            return LEDSlot(enabled: false, rgb: RGB(red: 0, green: 0, blue: 0))
        }

        return draft.keys[position]
    }

    func hasKeyLightingChange(_ source: Int) -> Bool {
        guard let cleanDraft, LegacyKey.physicalIndices.contains(source), slots.indices.contains(source),
            editorStyle != .animation
        else { return false }

        let staged = editorStyle == .solid ? LEDSlot(enabled: true, rgb: solidColor) : slots[source]
        return unpaintedKeys.contains(source) || staged != paintSlot(source, in: cleanDraft)
    }

    func revertKeyLighting(_ source: Int) {
        guard hasSnapshot, !isBusy, editorStyle != .animation, LegacyKey.physicalIndices.contains(source),
            let cleanDraft, slots.indices.contains(source)
        else { return }

        var layout = paintLayout()
        layout[source] = paintSlot(source, in: cleanDraft)
        slots = layout
        editorStyle = .perKey
        unpaintedKeys.remove(source)
        draftChanged()
    }

    func keyLightingDescription(_ source: Int, onKeyboard: Bool) -> String {
        guard let cleanDraft, slots.indices.contains(source) else { return "Not loaded" }

        let draft = onKeyboard ? cleanDraft : currentDraft
        if draft.style == .animation {
            let color =
                draft.generated || draft.mode.colorPolicy == .generated ? "Full color" : draft.animationColor.hex
            return "\(draft.mode.name) · \(color) · speed \(draft.speed) · brightness \(draft.brightness)/16"
        }

        if !onKeyboard, unpaintedKeys.contains(source) { return "Unpainted" }

        let slot = paintSlot(source, in: draft)
        let color = slot.enabled && !slot.rgb.isOff ? slot.rgb.hex : "Off"
        return color + " · brightness \(draft.brightness)/16"
    }

    private func stageSelectedPaint(on targets: Set<Int>, color: RGB? = nil, tool: LightingPaintTool? = nil) {
        let color = color ?? selectedPaintColor
        let tool = tool ?? (paintTool == .bucket ? .bucket : .brush)
        let off = color.isOff
        var layout = paintLayout()
        for source in targets { layout[source] = LEDSlot(enabled: !off, rgb: off ? layout[source].rgb : color) }

        slots = layout
        editorStyle = .perKey
        unpaintedKeys.subtract(targets)
        rememberPaintColor(color, tool: tool)
        draftChanged()
    }

    private func rememberPaintColor(_ color: RGB, tool: LightingPaintTool) {
        guard !color.isOff else { return }

        if tool == .bucket {
            guard lastBucketColor != color else { return }

            lastBucketColor = color
            preferences?.set(color.hex, forKey: "lastBucketColor")
        } else {
            guard lastUsedColor != color else { return }

            lastUsedColor = color
            preferences?.set(color.hex, forKey: "lastLightingColor")
        }
    }

    private func lightingCandidate(from baseline: Data) throws -> Data {
        guard !hasUnpaintedKeys else { throw WorkflowError.message("Paint or fill every blank key before reviewing.") }

        switch editorStyle {
        case .solid:
            return try LightingCandidateBuilder.buildPersistentStatic(
                from: baseline, color: solidColor, brightness: brightness)
        case .animation:
            return try LightingCandidateBuilder.buildBuiltIn(
                from: baseline,
                settings: BuiltInSettings(
                    mode: animationMode, color: animationColor, usesGeneratedColor: animationUsesGeneratedColor,
                    brightness: max(1, brightness), speed: animationSpeed, direction: animationDirection))
        case .perKey: return try LightingCandidateBuilder.build(from: baseline, slots: slots, brightness: brightness)
        }
    }

    var previewSlots: [LEDSlot] {
        Self.preview(
            slots: slots, style: editorStyle, solidColor: solidColor, animationColor: animationColor,
            generated: animationMode.colorPolicy == .generated || animationUsesGeneratedColor, brightness: brightness)
    }

    private static func keyboardPreview(from snapshot: DeviceSnapshot?) -> [LEDSlot] {
        guard let snapshot, let slots = try? LightingCandidateBuilder.slots(from: snapshot.lightingData) else {
            return []
        }

        let draft = Self.editorDraft(from: snapshot.lightingData, slots: slots)
        return Self.preview(
            slots: slots, style: draft.style, solidColor: draft.solidColor, animationColor: draft.animationColor,
            generated: draft.generated, brightness: Int(snapshot.lightingData[0x449]))
    }

    static let brightnessLevels = [0, 0, 24, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 255]

    var brightnessPercent: Int {
        Int((Double(Self.brightnessLevels[min(16, max(0, brightness))]) / 255 * 100).rounded())
    }

    private static let animationPreviewColors = Dictionary(
        uniqueKeysWithValues: LegacyKey.physical.map { key in
            let hue = (Double(key.column) / 20 + Double(key.row) / 12).truncatingRemainder(dividingBy: 1)
            let phase = hue * 6
            let blend = UInt8(255 * (1 - abs(phase.truncatingRemainder(dividingBy: 2) - 1)))
            let rgb: RGB
            switch Int(phase) {
            case 0: rgb = RGB(red: 255, green: blend, blue: 0)
            case 1: rgb = RGB(red: blend, green: 255, blue: 0)
            case 2: rgb = RGB(red: 0, green: 255, blue: blend)
            case 3: rgb = RGB(red: 0, green: blend, blue: 255)
            case 4: rgb = RGB(red: blend, green: 0, blue: 255)
            default: rgb = RGB(red: 255, green: 0, blue: blend)
            }

            return (key.index, rgb)
        })

    private static func preview(
        slots: [LEDSlot], style: LightingEditorStyle, solidColor: RGB, animationColor: RGB, generated: Bool,
        brightness: Int
    ) -> [LEDSlot] {
        var preview = slots
        let color: RGB?
        switch style {
        case .solid: color = solidColor
        case .animation: color = generated ? nil : animationColor
        case .perKey: color = nil
        }

        let scale = Double(brightnessLevels[min(16, max(0, brightness))]) / 255
        for index in preview.indices {
            if let color {
                preview[index] = LEDSlot(enabled: true, rgb: color)
            } else if style == .animation, let rgb = animationPreviewColors[index] {
                preview[index] = LEDSlot(enabled: true, rgb: rgb)
            }

            let rgb = preview[index].rgb
            preview[index].rgb = RGB(
                red: UInt8((Double(rgb.red) * scale).rounded()), green: UInt8((Double(rgb.green) * scale).rounded()),
                blue: UInt8((Double(rgb.blue) * scale).rounded()))
        }

        return preview
    }

    private var currentDraft: LightingProfileDraft {
        LightingProfileDraft(
            style: editorStyle, solidColor: solidColor, brightness: brightness, mode: animationMode,
            animationColor: animationColor, generated: animationUsesGeneratedColor, speed: animationSpeed,
            direction: animationDirection,
            keys: LegacyKey.physical.compactMap { slots.indices.contains($0.index) ? slots[$0.index] : nil })
    }

    private func output(_ label: String) -> URL {
        return root.appendingPathComponent("\(label)-\(UUID().uuidString)", isDirectory: true)
    }

    func savedLightingDraft() throws -> LightingProfileDraft {
        guard !hasUnpaintedKeys else { throw WorkflowError.message("Paint or fill every blank key before saving.") }

        guard hasLightingDraft, !isBusy else {
            throw WorkflowError.message("Read the keyboard lighting before saving a profile.")
        }

        let draft = currentDraft
        try draft.validate()
        return draft
    }

    func loadLightingProfile(_ profile: KeyboardProfile) throws {
        guard hasSnapshot, !isBusy else {
            throw WorkflowError.message("Read the keyboard lighting before loading a profile.")
        }

        try profile.validate()
        guard let draft = profile.draft else {
            throw WorkflowError.message("This profile does not contain lighting settings.")
        }

        try restoreDraft(draft)
        status = "Loaded \(profile.name) into the editor. Review your changes to apply them."
    }

    private func restoreDraft(_ draft: LightingProfileDraft) throws {
        let mergedSlots = try draft.mergingKeys(into: slots)
        editorStyle = draft.style
        solidColor = draft.solidColor
        brightness = draft.brightness
        animationMode = draft.mode
        animationColor = draft.animationColor
        animationUsesGeneratedColor = draft.generated
        animationSpeed = draft.speed
        animationDirection = draft.direction
        slots = mergedSlots
        unpaintedKeys.removeAll()
        staticBrightness = nil
        draftChanged()
    }

    func refresh(discardingEdits: Bool = false) { Task { await read(discardingEdits: discardingEdits) } }

    func read(discardingEdits: Bool = false) async {
        guard !isBusy, !activity.isBusy else { return }

        guard discardingEdits || !hasUnsavedLightingChanges else {
            confirmsLightingDiscard = true
            return
        }

        confirmsLightingDiscard = false
        guard checkAccess() else {
            needsInputMonitoringAccess = true
            fail("Enable KeyboardLights under Input Monitoring in System Settings, then return to the app.")
            return
        }

        needsInputMonitoringAccess = false
        guard begin(.reading, status: "Reading keyboard state…") else { return }

        review = nil
        do {
            let snapshot = try await service.capture(output: output("capture"))
            try adoptSnapshot(snapshot)
            finish(status: "Current lighting loaded.")
        } catch { fail("Read stopped without writing: \(error.localizedDescription)") }
    }

    private func adoptSnapshot(_ snapshot: DeviceSnapshot) throws {
        let slots = try LightingCandidateBuilder.slots(from: snapshot.lightingData)
        let brightness = try LightingCandidateBuilder.brightness(from: snapshot.lightingData)
        guard brightness <= 16 else {
            throw WorkflowError.message("Captured UserDefine brightness is outside the legacy 0–16 range.")
        }

        let draft = Self.editorDraft(from: snapshot.lightingData, slots: slots)
        self.snapshot = snapshot
        captures = snapshot.captures
        lighting = LightingState(data: snapshot.lightingData)
        self.slots = slots
        unpaintedKeys.removeAll()
        staticBrightness = nil
        self.brightness = brightness
        editorStyle = draft.style
        solidColor = draft.solidColor
        animationMode = draft.mode
        animationColor = draft.animationColor
        animationUsesGeneratedColor = draft.generated
        animationSpeed = draft.speed
        animationDirection = draft.direction
        cleanDraft = currentDraft
        review = nil
    }

    func resetLighting() {
        guard hasSnapshot, !isBusy, let cleanDraft else { return }

        do {
            try restoreDraft(cleanDraft)
            error = nil
            status = "Lighting changes reset."
        } catch { self.error = error.localizedDescription }
    }

    func paintKey(_ source: Int, color: RGB, enabled: Bool = true) {
        guard !isBusy, LegacyKey.physicalIndices.contains(source), slots.indices.contains(source) else { return }

        unpaintedKeys.remove(source)
        slots[source] = LEDSlot(enabled: enabled, rgb: enabled ? color : slots[source].rgb)
        draftChanged()
    }

    func draftChanged() { review = nil }

    func prepareReview() {
        guard !isBusy, writeAllowed() else { return }

        guard let snapshot else {
            fail("Read the keyboard before reviewing a lighting change.")
            return
        }

        guard begin(.preparingWrite, status: "Validating lighting changes…") else { return }

        review = nil
        do {
            let candidate = try lightingCandidate(from: snapshot.lightingData)
            let selectionName = editorStyle == .animation ? animationMode.name : "Solid color"
            try NativeLightingPolicy.validate(baseline: snapshot.lightingData, candidate: candidate)
            let changedKeys = LegacyKey.physical.filter { key in
                let offset = 0x200 + key.index * 4
                return snapshot.lightingData[offset..<(offset + 4)] != candidate[offset..<(offset + 4)]
            }.map(\.name)
            review = WriteReview(
                candidate: candidate, location: snapshot.location,
                changedByteCount: zip(snapshot.lightingData, candidate).reduce(0) { $0 + ($1.0 != $1.1 ? 1 : 0) },
                changedKeys: changedKeys, candidateChecksum: candidate.reduce(UInt64(0)) { $0 + UInt64($1) },
                selectionName: selectionName)
            finish(status: "Lighting changes validated; review required before applying")
        } catch { fail("Review stopped without accessing the keyboard: \(error.localizedDescription)") }
    }

    func executeReviewedWrite(_ reviewed: WriteReview) { Task { await applyReviewedWrite(reviewed) } }

    @discardableResult func applyReviewedWrite(_ reviewed: WriteReview, owner: UUID? = nil) async -> Bool {
        guard !isBusy, writeAllowed() else { return false }

        guard !hasUnpaintedKeys else {
            review = nil
            error = "Paint or fill every blank key before applying."
            return false
        }

        guard let snapshot, let review, review.id == reviewed.id, reviewed.location == snapshot.location else {
            self.review = nil
            error = "This review is stale. Prepare a new review before applying."
            return false
        }

        guard begin(.writing, status: "Updating keyboard lighting…", owner: owner) else { return false }

        self.review = nil
        let destination = output("write")
        do {
            let verified = try await service.write(
                snapshot: snapshot, candidate: reviewed.candidate, output: destination)
            guard verified.location == snapshot.location, verified.lightingData == reviewed.candidate else {
                throw NativeDeviceError(
                    code: Int32(C989_ACTIVATION_UNCERTAIN),
                    message: "The device result did not match the reviewed lighting. Further writes require inspection."
                )
            }

            self.snapshot = verified
            lighting = LightingState(data: verified.lightingData)
            slots = try LightingCandidateBuilder.slots(from: verified.lightingData)
            captures = verified.captures
            unpaintedKeys.removeAll()
            staticBrightness = nil
            cleanDraft = currentDraft
            finish(status: "Keyboard lighting updated.")
            return true
        } catch {
            self.snapshot = nil
            self.review = nil
            if (error as? NativeDeviceError)?.requiresInspection ?? true {
                let records = FileManager.default.fileExists(atPath: destination.path) ? destination : nil
                activity.blockWrites(evidence: records)
                warning = error.localizedDescription + (records.map { " Records: " + $0.path } ?? "")
                finish(status: "Stopped; the keyboard update needs inspection.")
            } else {
                fail(error.localizedDescription)
            }

            return false
        }
    }

    private func writeAllowed() -> Bool {
        do {
            try activity.checkWriteAllowed()
            return true
        } catch {
            review = nil
            self.error = error.localizedDescription
            return false
        }
    }

    private func endActivity() {
        activeOperation = nil
        if ownsActivity {
            ownsActivity = false
            activity.finish()
        }
    }

    private func fail(_ message: String) {
        error = message
        status = "Operation stopped"
        endActivity()
    }

    private func begin(_ operation: DeviceOperation, status: String, owner: UUID? = nil) -> Bool {
        do { try activity.begin(owner: owner) } catch {
            self.error = error.localizedDescription
            return false
        }

        ownsActivity = true
        error = nil
        warning = nil
        self.status = status
        activeOperation = operation
        return true
    }

    private func finish(status: String) {
        self.status = status
        endActivity()
    }

    nonisolated private static func editorDraft(from data: Data, slots: [LEDSlot]) -> (
        style: LightingEditorStyle, solidColor: RGB, mode: BuiltInMode, animationColor: RGB, generated: Bool,
        speed: Int, direction: AnimationDirection
    ) {
        let tag = data[0x440]
        let fallbackAnimationColor = RGB(red: 0, green: 255, blue: 0)
        if let mode = BuiltInMode(rawValue: tag) {
            let capturedColor = RGB(red: data[0x441], green: data[0x442], blue: data[0x443])
            let direction: AnimationDirection = (data[0x44B] == 1 || data[0x44B] == 2) ? .left : .right
            return (
                .animation, RGB(red: 0xFF, green: 0xFF, blue: 0xFF), mode, capturedColor,
                mode.colorPolicy == .configurable && data[0x448] != 0, max(2, min(16, Int(data[0x44A]))), direction
            )
        }

        if let color = LightingCandidateBuilder.persistentStaticColor(forLegacyTag: tag) {
            return (.solid, color, .colourful, fallbackAnimationColor, true, 12, .right)
        }

        if tag == 0x80, let firstKey = LegacyKey.physical.first {
            let first = slots[firstKey.index]
            if first.enabled, LegacyKey.physical.allSatisfy({ slots[$0.index] == first }) {
                return (.solid, first.rgb, .colourful, fallbackAnimationColor, true, 12, .right)
            }
        }

        return (.perKey, RGB(red: 0xFF, green: 0xFF, blue: 0xFF), .colourful, fallbackAnimationColor, true, 12, .right)
    }
}

enum WorkflowError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        if case .message(let message) = self { return message }

        return nil
    }
}
