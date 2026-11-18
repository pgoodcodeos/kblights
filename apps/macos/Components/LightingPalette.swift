import Foundation
import KeyboardData

extension RGB {
    var isOff: Bool { red == 0 && green == 0 && blue == 0 }

    init?(hex: String) {
        guard hex.utf8.count == 7, hex.first == "#",
            hex.utf8.dropFirst().allSatisfy({
                (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0)
            }), let value = UInt32(hex.dropFirst(), radix: 16)
        else { return nil }

        self.init(red: UInt8((value >> 16) & 255), green: UInt8((value >> 8) & 255), blue: UInt8(value & 255))
    }

    var hex: String { String(format: "#%02X%02X%02X", red, green, blue) }
}

struct LightingColorPreset: Identifiable, Sendable {
    let name: String
    let hex: String
    let rgb: RGB
    var id: String { hex }

    private struct Record: Decodable {
        let name: String
        let hex: String
    }

    static let all: [LightingColorPreset] = {
        do {
            let records = try JSONDecoder().decode(
                [Record].self, from: KeyboardResources.data(named: "lighting-colors.json"))
            let presets = try records.map { record in
                guard let rgb = RGB(hex: record.hex) else { throw CocoaError(.fileReadCorruptFile) }

                return LightingColorPreset(name: record.name, hex: record.hex, rgb: rgb)
            }

            guard !presets.isEmpty, Set(presets.map(\.hex)).count == presets.count else {
                throw CocoaError(.fileReadCorruptFile)
            }

            return presets
        } catch { fatalError("Required lighting palette is missing or invalid: \(error)") }
    }()
}
