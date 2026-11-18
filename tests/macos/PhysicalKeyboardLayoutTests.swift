import XCTest

@testable import KeyboardLights

final class PhysicalKeyboardLayoutTests: XCTestCase {
    private func position(_ name: String) throws -> KeyboardKeyPosition {
        try XCTUnwrap(LegacyKey.physical.first { $0.name == name }?.display)
    }

    func testEveryPhysicalKeyHasUniqueNonoverlappingDisplayGeometry() {
        var occupied = Set<Int>()
        for key in LegacyKey.physical {
            let position = key.display
            XCTAssertTrue(position.isValid, key.name)
            for row in position.top..<(position.top + position.height) {
                for column in position.left..<(position.left + position.width) {
                    XCTAssertTrue(occupied.insert(row * PhysicalKeyboardGeometry.columns + column).inserted, key.name)
                }
            }
        }

        XCTAssertEqual(PhysicalKeyboardGeometry.columns, 107)
        XCTAssertEqual(PhysicalKeyboardGeometry.rows, 6)
    }

    func testMacroAndNavigationGroupsMatchTheKeyboardRatherThanItsFirmwareMatrix() throws {
        for number in 1...5 {
            let left = try position("M\(number)")
            let right = try position("M\(number + 5)")
            XCTAssertEqual(left.top, number)
            XCTAssertEqual(right.top, number)
            XCTAssertEqual(left.left, try position("M1").left)
            XCTAssertEqual(right.left, try position("M6").left)
            XCTAssertLessThan(left.left + left.width, right.left)
            XCTAssertLessThan(right.left + right.width, try position("Tab").left)
        }

        for names in [["Print", "Insert", "Delete"], ["Scroll Lock", "Home", "End"], ["Pause", "Page Up", "Page Down"]]
        {
            let positions = try names.map(position)
            XCTAssertEqual(Set(positions.map(\.left)).count, 1)
            XCTAssertEqual(positions.map(\.top), [0, 1, 2])
        }

        XCTAssertEqual(try position("Up").left, try position("Down").left)
        XCTAssertEqual(try position("Up").top + 1, try position("Down").top)
        XCTAssertEqual(try ["Left", "Down", "Right"].map { try position($0).top }, [5, 5, 5])
        XCTAssertLessThan(try position("Right").left, try position("Numpad 0").left)
    }

    func testWideAndTallKeycapsAndSplitTypingRowsKeepTheirPhysicalSpacing() throws {
        XCTAssertEqual(try position("Numpad 0").width, try 2 * position("Numpad 1").width)
        XCTAssertEqual(try position("Numpad +").height, 2)
        XCTAssertEqual(try position("Numpad Enter").height, 2)
        XCTAssertEqual(try position("Backspace").width, 8)
        XCTAssertEqual(try position("Tab").width, 6)
        XCTAssertEqual(try position("Enter").width, 9)
        for (left, right) in [("6", "7"), ("T", "Y"), ("G", "H"), ("B", "N"), ("Left Space", "Right Space")] {
            let before = try position(left)
            let after = try position(right)
            XCTAssertEqual(before.top, after.top)
            XCTAssertEqual(after.left - before.left - before.width, 4)
        }
    }

    func testResizingKeepsAllKeysInsideTheBoardAndPreservesTallKeyAlignment() throws {
        for width: CGFloat in [700, 900, 1050] {
            let geometry = PhysicalKeyboardGeometry(width: width)
            let board = CGRect(origin: .zero, size: geometry.size)
            let frames = LegacyKey.physical.map { geometry.frame($0.display) }

            for (key, frame) in zip(LegacyKey.physical, frames) {
                XCTAssertTrue(board.contains(frame), key.name)
                XCTAssertGreaterThanOrEqual(frame.width, 23)
                XCTAssertGreaterThanOrEqual(frame.height, 32)
            }

            for index in frames.indices {
                for other in frames.indices where other > index {
                    XCTAssertFalse(frames[index].intersects(frames[other]))
                }
            }

            let plus = geometry.frame(try position("Numpad +"))
            let six = geometry.frame(try position("Numpad 6"))
            XCTAssertEqual(plus.maxY, six.maxY, accuracy: 0.001)
        }
    }
}
