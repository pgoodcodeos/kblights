import XCTest

@testable import KeyboardLights

final class ColorFieldMathTests: XCTestCase {
    func testFieldEdgesMapToExactEndpoints() {
        XCTAssertEqual(ColorFieldMath.normalized(0, length: 240), 0)
        XCTAssertEqual(ColorFieldMath.normalized(240, length: 240), 1)
    }

    func testDraggingPastFieldClampsToEndpoints() {
        XCTAssertEqual(ColorFieldMath.normalized(-20, length: 240), 0)
        XCTAssertEqual(ColorFieldMath.normalized(260, length: 240), 1)
    }
}
