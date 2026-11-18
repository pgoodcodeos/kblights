import AppKit
import SwiftUI
import XCTest

@testable import KeyboardLights

final class AccentSliderTests: XCTestCase {
    @MainActor func testNativeActionsPublishOnlyChangedEnabledValues() {
        var value = 8.0
        var changes = 0
        let binding = Binding(
            get: { value },
            set: {
                value = $0
                changes += 1
            })
        let coordinator = AccentSlider.Coordinator(value: binding)
        let slider = NSSlider(value: 8.2, minValue: 2, maxValue: 16, target: nil, action: nil)
        coordinator.changed(slider)
        XCTAssertEqual(changes, 0)
        XCTAssertEqual(slider.doubleValue, 8)
        slider.doubleValue = 9.2
        coordinator.changed(slider)
        XCTAssertEqual(value, 9)
        XCTAssertEqual(changes, 1)
        slider.isEnabled = false
        slider.doubleValue = 10
        coordinator.changed(slider)
        XCTAssertEqual(value, 9)
        XCTAssertEqual(changes, 1)
    }
}
