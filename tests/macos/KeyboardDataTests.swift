import C989Native
import KeyboardData
import XCTest

@testable import KeyboardLights

final class KeyboardDataTests: XCTestCase {
    func testBundledResourcesAndKnownPhysicalKeys() throws {
        XCTAssertFalse(try KeyboardResources.data(named: "assignments.json").isEmpty)
        XCTAssertEqual(LegacyKey.physical.count, 115)
        XCTAssertEqual(LegacyKey.exposed.count, 104)
        XCTAssertEqual(LegacyKey.physicalByIndex[39]?.name, "W")
        XCTAssertEqual(LegacyKey.physicalByIndex[109]?.name, "Right Space")
        XCTAssertNil(LegacyKey.physicalByIndex[0])
        XCTAssertThrowsError(try KeyboardResources.data(named: "missing.json"))
    }

    func testSharedLayoutMatchesPortableAssignmentSlotPolicy() {
        let baseline = [UInt8](repeating: 0, count: 640)
        for index in 0..<144 {
            var patch = C989AssignmentPatch(source: UInt16(index), record: (5, 3, 0, 0))
            var output = [UInt8](repeating: 0, count: 640)
            var error = [CChar](repeating: 0, count: 256)
            let status = baseline.withUnsafeBufferPointer {
                c989_assignments_prepare(
                    $0.baseAddress, $0.count, &patch, 1, &output, output.count, &error, error.count)
            }

            XCTAssertEqual(status == C989_OK, LegacyKey.physicalIndices.contains(index), "slot \(index)")
        }
    }
}
