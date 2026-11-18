import Foundation
import KeyboardData

// Supported configuration actions; the same catalog is consumed by Linux.
enum KeyAssignmentCatalog {
    private struct Catalog: Decodable {
        let format: String
        let targets: [String: [AssignmentTarget]]
    }

    private static let catalog: Catalog = {
        do {
            let catalog = try JSONDecoder().decode(
                Catalog.self, from: KeyboardResources.data(named: "assignments.json"))
            guard catalog.format == "c989-assignment-catalog-v1",
                ["keyboard", "mouse", "media"].allSatisfy({ kind in
                    guard let targets = catalog.targets[kind], !targets.isEmpty else { return false }

                    return Set(targets.map(\.id)).count == targets.count && targets.allSatisfy { $0.bytes.count == 4 }
                })
            else { throw CocoaError(.fileReadCorruptFile) }

            return catalog
        } catch { fatalError("Required keyboard assignment catalog is missing or invalid: \(error)") }
    }()

    static let keyboard = catalog.targets["keyboard"]!
    static let mouse = catalog.targets["mouse"]!
    static let media = catalog.targets["media"]!
}
