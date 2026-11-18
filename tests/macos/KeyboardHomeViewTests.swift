import AppKit
import SwiftUI
import XCTest

@testable import KeyboardLights

final class KeyboardHomeViewTests: XCTestCase {
    @MainActor private func accessibilityElements(_ root: NSObject, includeSubviews: Bool = true) -> [NSObject] {
        var pending = [root]
        var found: [NSObject] = []
        var visited = Set<ObjectIdentifier>()
        while let element = pending.popLast() {
            guard visited.insert(ObjectIdentifier(element)).inserted else { continue }

            found.append(element)
            if includeSubviews, let view = element as? NSView { pending.append(contentsOf: view.subviews) }

            if includeSubviews, let window = element as? NSWindow, let content = window.contentView {
                pending.append(content)
            }

            if element.responds(to: NSSelectorFromString("accessibilityChildren")),
                let children = element.value(forKey: "accessibilityChildren") as? [NSObject]
            {
                pending.append(contentsOf: children)
            }
        }

        return found
    }

    @MainActor private func text(_ element: NSObject, _ key: String) -> String? {
        guard element.responds(to: NSSelectorFromString(key)) else { return nil }

        return element.value(forKey: key) as? String
    }

    @MainActor private func labels(in elements: [NSObject]) -> [String] {
        elements.flatMap { element in
            [
                text(element, "accessibilityLabel"), text(element, "accessibilityValue"),
                text(element, "accessibilityTitle")
            ].compactMap { $0 }
        }
    }

    @MainActor private func enableAccessibility() -> () -> Void {
        // SwiftUI creates this test process's accessibility tree on demand through the legacy API.
        let application = NSApplication.shared
        let attribute = NSAccessibility.Attribute(rawValue: "AXEnhancedUserInterface")
        let previous = application.accessibilityAttributeValue(attribute)
        application.accessibilitySetValue(true, forAttribute: attribute)
        return { application.accessibilitySetValue(previous, forAttribute: attribute) }
    }

    @MainActor private func host<Content: View>(_ content: Content, width: Int, height: Int) -> NSWindow {
        let controller = NSHostingController(rootView: content)
        controller.sizingOptions = []
        let frame = NSRect(x: 0, y: 0, width: width, height: height)
        let window = NSWindow(
            contentRect: frame, styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentViewController = controller
        window.setContentSize(frame.size)
        controller.view.frame = frame
        window.orderFront(nil)
        return window
    }

    @MainActor private func press(_ element: NSObject) throws -> Bool {
        let action = NSSelectorFromString("accessibilityPerformPress")
        guard element.responds(to: action) else {
            XCTFail("The accessibility element does not support pressing")
            return false
        }

        // SwiftUI's virtual nodes expose the action without declaring AppKit's full protocol.
        typealias Press = @convention(c) (AnyObject, Selector) -> Bool
        let implementation = try XCTUnwrap(element.method(for: action))
        let perform = unsafeBitCast(implementation, to: Press.self)
        return perform(element, action)
    }

    @MainActor private func savePreview(of view: NSView, named name: String) throws {
        guard let directory = ProcessInfo.processInfo.environment["KEYBOARDLIGHTS_PREVIEW_DIR"] else { return }

        let url = URL(fileURLWithPath: directory, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        let bitmap = try XCTUnwrap(view.bitmapImageRepForCachingDisplay(in: view.bounds))
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try XCTUnwrap(bitmap.representation(using: .png, properties: [:])).write(to: url.appendingPathComponent(name))
    }

    @MainActor func testLockIndicatorsChangeStateWithoutMovingKeysOrGrowingThePreview() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let model = LockPreviewModel()
        let window = host(LockPreviewHost(state: rig.session.state, model: model), width: 980, height: 800)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        var baseline: [CGRect]?
        var baselineHeight: CGFloat?
        var indicatorBaseline: [CGRect]?
        for reading in [nil, LockIndicators(rawValue: 7), LockIndicators(rawValue: 0)] {
            model.reading = reading
            let expected = reading.map { $0.rawValue == 7 ? "On" : "Off" } ?? "Unavailable"
            var elements: [NSObject] = []
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                elements = self.accessibilityElements(window)
                if reading == nil { return self.labels(in: elements).contains("Lock state unavailable") }

                return ["Caps Lock", "Num Lock", "Scroll Lock"].allSatisfy { name in
                    elements.contains {
                        self.labels(in: [$0]).contains(name)
                            && $0.value(forKey: "accessibilityValue") as? String == expected
                    }
                }
            }

            let indicatorNames = ["Caps Lock", "Num Lock", "Scroll Lock"]
            let names = reading == nil ? ["Esc", "Left Ctrl"] : indicatorNames + ["Esc", "Left Ctrl"]
            let frames = try names.map { name in
                let element = try XCTUnwrap(
                    elements.first {
                        self.labels(in: [$0]).contains(name)
                            && (!["Caps Lock", "Num Lock", "Scroll Lock"].contains(name)
                                || $0.value(forKey: "accessibilityValue") as? String == expected)
                    })
                return try XCTUnwrap(element.value(forKey: "accessibilityFrame") as? NSValue).rectValue
            }

            if reading != nil {
                let indicatorFrames = Array(frames.prefix(3))
                if let indicatorBaseline {
                    XCTAssertEqual(indicatorFrames, indicatorBaseline)
                } else {
                    indicatorBaseline = indicatorFrames
                }
            }

            if let baseline, let baselineHeight {
                XCTAssertEqual(Array(frames.suffix(2)), Array(baseline.suffix(2)))
                XCTAssertEqual(view.fittingSize.height, baselineHeight)
            } else {
                baseline = frames
                baselineHeight = view.fittingSize.height
            }

            try savePreview(of: view, named: "lock-indicators-\(expected.lowercased()).png")
        }
    }

    @MainActor func testPhysicalKeyboardRendersEveryKeyAndPreservesSelectionCallbacks() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        var selected: [Int] = []
        let slots = (0..<144).map { _ in LEDSlot(enabled: true, rgb: RGB(red: 0, green: 180, blue: 220)) }

        for width in [700, 1000] {
            let keyboard = AssignmentKeyboard(selected: 39, planned: [39], slots: slots) { selected.append($0) }

            let window = host(keyboard.padding(12), width: width, height: 280)
            let view = try XCTUnwrap(window.contentViewController?.view)
            defer {
                window.close()
                window.contentViewController = nil
            }

            var elements: [NSObject] = []
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                elements = self.accessibilityElements(window)
                let names = Set(self.labels(in: elements))
                return LegacyKey.physical.allSatisfy { names.contains($0.name) }
            }

            let keyNames = Set(LegacyKey.physical.map(\.name))
            let accessibleKeys = elements.compactMap { element -> String? in
                guard element.responds(to: NSSelectorFromString("accessibilityPerformPress")),
                    let name = text(element, "accessibilityLabel"), keyNames.contains(name)
                else { return nil }

                return name
            }

            XCTAssertEqual(accessibleKeys.count, 115)
            XCTAssertEqual(Set(accessibleKeys), keyNames)

            for name in ["M1", "M6", "W", "Up", "Numpad Enter"] {
                let key = try XCTUnwrap(LegacyKey.physical.first { $0.name == name })
                let pressAction = NSSelectorFromString("accessibilityPerformPress")
                let element = try XCTUnwrap(
                    elements.first { labels(in: [$0]).contains(name) && $0.responds(to: pressAction) })

                XCTAssertTrue(try self.press(element))
                XCTAssertEqual(selected.last, key.index)
            }

            let renderer = ImageRenderer(
                content: AssignmentKeyboard(selected: 39, planned: [39], slots: slots) { _ in }.keycaps.frame(
                    width: CGFloat(width)
                ).padding(12).background(Color(nsColor: .windowBackgroundColor)))
            renderer.scale = 2
            let image = try XCTUnwrap(renderer.nsImage)
            let bitmap = try XCTUnwrap(NSBitmapImageRep(data: XCTUnwrap(image.tiffRepresentation)))
            let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
            XCTAssertFalse(png.isEmpty)
            if let directory = ProcessInfo.processInfo.environment["KEYBOARDLIGHTS_PREVIEW_DIR"] {
                let url = URL(fileURLWithPath: directory, isDirectory: true)
                try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
                try png.write(to: url.appendingPathComponent("keyboard-\(width).png"))
                try savePreview(of: view, named: "keyboard-viewport-\(width).png")
            }

            let attachment = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
            attachment.name = "Physical keyboard at \(width) points"
            attachment.lifetime = .keepAlways
            add(attachment)
        }
    }

    @MainActor func testKeyboardPreservesAccessibleLightingAndSelectionWhenDisabled() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let off = try XCTUnwrap(LegacyKey.physical.first { $0.name == "Esc" })
        let blank = try XCTUnwrap(LegacyKey.physical.first { $0.name == "M1" })
        let selected = try XCTUnwrap(LegacyKey.physical.first { $0.name == "W" })
        let painted = try XCTUnwrap(LegacyKey.physical.first { $0.name == "Numpad Enter" })
        var slots = Array(repeating: LEDSlot(enabled: true, rgb: RGB(red: 0, green: 180, blue: 220)), count: 144)
        slots[off.index].enabled = false
        slots[selected.index].rgb = RGB(red: 0, green: 0, blue: 0)
        var presses = 0
        let window = host(
            AssignmentKeyboard(
                selected: selected.index, planned: [selected.index], slots: slots, unpainted: [blank.index]
            ) { _ in presses += 1 }.disabled(true).padding(12), width: 1000, height: 280)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        let action = NSSelectorFromString("accessibilityPerformPress")
        var keys: [NSObject] = []
        let names = Set(LegacyKey.physical.map(\.name))
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            keys = self.accessibilityElements(window).filter {
                names.contains(self.text($0, "accessibilityLabel") ?? "") && $0.responds(to: action)
            }

            return keys.count == 115
        }

        func element(_ key: LegacyKey) throws -> NSObject {
            try XCTUnwrap(keys.first { text($0, "accessibilityLabel") == key.name })
        }

        for key in keys { XCTAssertEqual(key.value(forKey: "accessibilityEnabled") as? Bool, false) }

        XCTAssertEqual(text(try element(selected), "accessibilityValue"), "Selected. Planned change, lighting off")
        XCTAssertEqual(text(try element(off), "accessibilityValue"), "No planned change, lighting off")
        XCTAssertEqual(text(try element(blank), "accessibilityValue"), "No planned change, unpainted")
        XCTAssertEqual(text(try element(painted), "accessibilityValue"), "No planned change, color #00B4DC")
        _ = try press(element(selected))
        XCTAssertEqual(presses, 0)
    }

    @MainActor func testKeyboardKeepsItsFullHeightInsideTheEditorScrollView() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        for width in [700, 1000] {
            let editor = ScrollView(.vertical) {
                VStack {
                    Text("Before keyboard")
                    AssignmentKeyboard(selected: -1, planned: []) { _ in }

                    Text("After keyboard")
                }.padding(12)
            }

            let window = host(editor, width: width, height: 500)
            let view = try XCTUnwrap(window.contentViewController?.view)
            defer {
                window.close()
                window.contentViewController = nil
            }

            var elements: [NSObject] = []
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                elements = self.accessibilityElements(window)
                return self.labels(in: elements).contains("After keyboard")
            }

            func frame(_ name: String) throws -> CGRect {
                let element = try XCTUnwrap(elements.first { self.labels(in: [$0]).contains(name) })
                return try XCTUnwrap(element.value(forKey: "accessibilityFrame") as? NSValue).rectValue
            }

            let escape = try frame("Esc")
            let control = try frame("Left Ctrl")
            let after = try frame("After keyboard")
            XCTAssertGreaterThan(escape.height, 20)
            XCTAssertGreaterThan(control.height, 20)
            XCTAssertLessThan(after.maxY, control.minY)
            XCTAssertGreaterThan(escape.minY, control.maxY)
        }
    }

    @MainActor func testCompactLightingControlsPaintAndSwapBrightnessForSpeed() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let window = host(
            LightingView(
                state: rig.session.state,
                keyboard: AssignmentKeyboard(selected: -1, planned: [], slots: rig.session.state.previewSlots) {
                    rig.session.state.paintClickedKey($0)
                }), width: 800, height: 700)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        var elements: [NSObject] = []
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window, includeSubviews: false)
            return self.labels(in: elements).contains("Brightness")
        }

        let initialLabels = Set(labels(in: elements))
        XCTAssertTrue(initialLabels.contains("Lighting mode"))
        XCTAssertTrue(initialLabels.contains("Select keys"))
        XCTAssertTrue(rig.session.state.isPointerSelected)
        XCTAssertTrue(initialLabels.contains("Paint brush"))
        XCTAssertTrue(initialLabels.contains("Paint bucket"))
        XCTAssertTrue(initialLabels.contains("Paint brush colors"))
        XCTAssertTrue(initialLabels.contains("Paint bucket colors"))
        XCTAssertFalse(initialLabels.contains("Animation speed"))
        XCTAssertFalse(initialLabels.contains("Apply to all"))

        let clear = try XCTUnwrap(
            elements.first {
                text($0, "accessibilityLabel") == "Clear"
                    && text($0, "accessibilityRole") == NSAccessibility.Role.button.rawValue
            })
        _ = try press(clear)
        try await waitForCondition { rig.session.state.unpaintedKeys.count == 115 }

        XCTAssertFalse(rig.session.state.hasLightingChangesToApply)
        rig.session.state.fillPaint()
        XCTAssertTrue(rig.session.state.unpaintedKeys.isEmpty)
        rig.session.state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window, includeSubviews: false)
            return self.labels(in: elements).contains("Animation speed")
        }

        let animationLabels = Set(labels(in: elements))
        XCTAssertTrue(animationLabels.contains("Select keys"))
        XCTAssertTrue(rig.session.state.isPointerSelected)
        XCTAssertFalse(animationLabels.contains("Brightness"))
        XCTAssertFalse(animationLabels.contains("Clear"))
        XCTAssertFalse(animationLabels.contains("Off"))
        XCTAssertFalse(animationLabels.contains("Custom…"))

        let slider = try XCTUnwrap(
            accessibilityElements(window).compactMap { $0 as? NSSlider }.first { $0.minValue == 2 && $0.maxValue == 16 }
        )
        XCTAssertTrue(slider.isEnabled)
        rig.session.state.animationSpeed = 8
        try await waitForCondition { slider.doubleValue == 8 }

        XCTAssertTrue(slider.accessibilityPerformIncrement())
        XCTAssertEqual(rig.session.state.animationSpeed, 9)
        XCTAssertTrue(slider.accessibilityPerformDecrement())
        XCTAssertEqual(rig.session.state.animationSpeed, 8)

        rig.session.state.animationSpeed = 16
        try await waitForCondition { slider.doubleValue == 16 }

        XCTAssertFalse(slider.accessibilityPerformIncrement())
        XCTAssertEqual(rig.session.state.animationSpeed, 16)
    }

    @MainActor func testPaintToolIconsRenderVisibleArtwork() throws {
        for tool in [LightingPaintTool.brush, .bucket] {
            let renderer = ImageRenderer(
                content: LightingPaintToolIcon(tool: tool).foregroundStyle(.black).background(.white))
            renderer.scale = 2
            let image = try XCTUnwrap(renderer.nsImage)
            let bitmap = try XCTUnwrap(NSBitmapImageRep(data: XCTUnwrap(image.tiffRepresentation)))
            var inkPixels = 0
            for y in 0..<bitmap.pixelsHigh {
                for x in 0..<bitmap.pixelsWide {
                    if let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.sRGB),
                        color.redComponent < 0.5 && color.greenComponent < 0.5 && color.blueComponent < 0.5
                    {
                        inkPixels += 1
                    }
                }
            }

            XCTAssertGreaterThan(inkPixels, 40, "Missing artwork for \(tool.rawValue)")
        }
    }

    @MainActor func testPaintControlsKeepWholeLabelsAndFitNarrowWindows() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        rig.session.state.selectBucketColor(RGB(red: 0, green: 255, blue: 255))
        for width in [540, 680, 800] {
            let disabled = width == 540
            let window = host(
                LightingView(state: rig.session.state, keyboard: EmptyView()).disabled(disabled), width: width,
                height: 500)
            let view = try XCTUnwrap(window.contentViewController?.view)
            defer {
                window.close()
                window.contentViewController = nil
            }

            let names = [
                "Select keys", "Paint brush", "Paint brush colors", "Paint bucket", "Paint bucket colors", "Clear",
                "Fill"
            ]
            var controls: [String: NSObject] = [:]
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                let elements = self.accessibilityElements(window)
                for name in names {
                    controls[name] = elements.first {
                        self.text($0, "accessibilityLabel") == name
                            && [NSAccessibility.Role.button.rawValue, NSAccessibility.Role.checkBox.rawValue].contains(
                                self.text($0, "accessibilityRole"))
                    }
                }

                return controls.count == names.count
            }

            let bounds = window.convertToScreen(view.bounds).insetBy(dx: -1, dy: -1)
            for name in ["Select keys", "Paint brush", "Paint bucket"] {
                let control = try XCTUnwrap(controls[name])
                XCTAssertEqual(control.value(forKey: "accessibilityValue") as? Int, name == "Paint bucket" ? 1 : 0)
            }

            for name in names {
                let control = try XCTUnwrap(controls[name])
                let frame = try XCTUnwrap(control.value(forKey: "accessibilityFrame") as? NSValue).rectValue
                XCTAssertTrue(bounds.contains(frame), "\(name) overflows at \(width) points")
                if name == "Clear" || name == "Fill" {
                    let labelWidth = (name as NSString).size(withAttributes: [
                        .font: NSFont.systemFont(ofSize: NSFont.systemFontSize)
                    ]).width
                    XCTAssertGreaterThanOrEqual(frame.width, labelWidth + 8, "\(name) is compressed at \(width) points")
                }
            }

            if disabled {
                for control in controls.values {
                    XCTAssertEqual(control.value(forKey: "accessibilityEnabled") as? Bool, false)
                }

                _ = try press(XCTUnwrap(controls["Clear"]))
                XCTAssertTrue(rig.session.state.unpaintedKeys.isEmpty)
            }

            try savePreview(of: view, named: "paint-controls-\(width).png")
        }
    }

    @MainActor func testPaintToolsStaySelectedAndInactivePalettesRemainEnabled() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let state = rig.session.state
        let draft = try state.savedLightingDraft()
        let window = host(LightingView(state: state, keyboard: EmptyView()), width: 800, height: 700)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        let pressAction = NSSelectorFromString("accessibilityPerformPress")
        func control(_ label: String) async throws -> NSObject {
            var result: NSObject?
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                result = NSApplication.shared.windows.filter(\.isVisible).flatMap { self.accessibilityElements($0) }
                    .first { self.text($0, "accessibilityLabel") == label && $0.responds(to: pressAction) }

                return result != nil
            }

            return try XCTUnwrap(result)
        }

        let tools: [(LightingPaintTool, String)] = [
            (.pointer, "Select keys"), (.brush, "Paint brush"), (.bucket, "Paint bucket")
        ]
        for (tool, label) in tools {
            for _ in 0..<2 {
                _ = try press(await control(label))
                XCTAssertEqual(state.paintTool, tool)
                for (candidate, name) in tools {
                    let button = try await control(name)
                    XCTAssertEqual(button.value(forKey: "accessibilityValue") as? Int, candidate == tool ? 1 : 0)
                }
            }
        }

        let swatchNames = ["Last-used color"] + LightingColorPreset.all.map(\.name)
        let choices = ["Last-used color", LightingColorPreset.all[0].name]
        for (tool, label) in tools where tool != .pointer {
            for (other, _) in tools where other != tool {
                for swatch in choices {
                    state.selectPaintTool(other)
                    _ = try press(await control(label + " colors"))
                    XCTAssertEqual(state.paintTool, other, "Opening a palette must not change tools")
                    let buttons = swatchNames.map { label + " " + $0 } + ["Custom…"]
                    try await waitForCondition {
                        let elements = NSApplication.shared.windows.filter(\.isVisible).flatMap {
                            self.accessibilityElements($0)
                        }

                        return buttons.allSatisfy { name in
                            elements.contains {
                                self.text($0, "accessibilityLabel") == name && $0.responds(to: pressAction)
                                    && $0.value(forKey: "accessibilityEnabled") as? Bool == true
                            }
                        }
                    }

                    _ = try press(await control(label + " " + swatch))
                    XCTAssertEqual(state.paintTool, tool)
                    XCTAssertEqual(try state.savedLightingDraft(), draft)
                }
            }
        }
    }

    @MainActor func testCustomColorRequiresConfirmationBeforeChangingThePointerTool() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let state = rig.session.state
        let draft = try state.savedLightingDraft()
        let originalColor = state.selectedPaintColor
        let window = host(LightingView(state: state, keyboard: EmptyView()), width: 800, height: 700)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        let action = NSSelectorFromString("accessibilityPerformPress")
        for confirm in [false, true, false] {
            let pointerBefore = state.isPointerSelected
            var palette: NSObject?
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                palette = self.accessibilityElements(window).first {
                    self.text($0, "accessibilityLabel") == "Paint brush colors" && $0.responds(to: action)
                }

                return palette != nil
            }

            _ = try press(XCTUnwrap(palette))
            var custom: NSObject?
            try await waitForCondition {
                custom = NSApplication.shared.windows.filter(\.isVisible).flatMap { self.accessibilityElements($0) }
                    .first { self.labels(in: [$0]).contains("Custom…") && $0.responds(to: action) }

                return custom != nil
            }

            _ = try press(XCTUnwrap(custom))
            var choice: NSObject?
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                choice = NSApplication.shared.windows.filter(\.isVisible).flatMap { self.accessibilityElements($0) }
                    .first {
                        self.text($0, "accessibilityLabel") == (confirm ? "Use color" : "Cancel")
                            && $0.responds(to: action)
                    }

                return choice != nil
            }

            XCTAssertEqual(state.isPointerSelected, pointerBefore)
            XCTAssertEqual(state.selectedPaintColor, originalColor)
            _ = try press(XCTUnwrap(choice))
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                return !NSApplication.shared.windows.filter(\.isVisible).flatMap {
                    self.labels(in: self.accessibilityElements($0))
                }.contains("Use color")
            }

            XCTAssertEqual(state.isPointerSelected, confirm ? false : pointerBefore)
            XCTAssertEqual(state.selectedPaintColor, originalColor)
            XCTAssertEqual(try state.savedLightingDraft(), draft)
        }
    }

    @MainActor func testKeyInspectorShowsBothDomainsAndRevertsThemIndependently() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let state = rig.session.state
        state.selectLightingMode(128)
        state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        state.paintSelectedColor(on: 39)
        let editor = KeyAssignmentEditor()
        try editor.set(KeyAction(kind: .disabled), for: 39)
        let window = host(
            KeyInspector(
                state: state, workflow: rig.session.assignmentWorkflow, editor: editor, activity: rig.session.activity),
            width: 580, height: 620)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        var elements: [NSObject] = []
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window)
            return self.labels(in: elements).contains("Revert key lighting")
        }

        let labels = self.labels(in: elements)
        XCTAssertTrue(labels.contains("Lighting"))
        XCTAssertTrue(labels.contains("Assignment"))
        XCTAssertTrue(labels.contains(where: { $0.contains("Preview: #123456") }))

        // Another window can own the shared device activity while these models are idle.
        try rig.session.activity.begin()
        defer { rig.session.activity.finish() }

        XCTAssertFalse(state.isBusy)
        XCTAssertFalse(rig.session.assignmentWorkflow.isBusy)
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            let action = self.accessibilityElements(window).first {
                self.text($0, "accessibilityLabel") == "Revert key assignment"
            }

            return action?.value(forKey: "accessibilityEnabled") as? Bool == false
        }

        XCTAssertEqual(editor.selectedAction?.kind, .disabled)
        rig.session.activity.finish()
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window)
            let action = elements.first { self.text($0, "accessibilityLabel") == "Revert key assignment" }

            return action?.value(forKey: "accessibilityEnabled") as? Bool == true
        }

        let revertLighting = try XCTUnwrap(elements.first { text($0, "accessibilityLabel") == "Revert key lighting" })
        _ = try press(revertLighting)
        try await waitForCondition { !state.hasKeyLightingChange(39) }

        XCTAssertEqual(editor.selectedAction?.kind, .disabled)
        elements = accessibilityElements(window)
        let revertAssignment = try XCTUnwrap(
            elements.first { text($0, "accessibilityLabel") == "Revert key assignment" })
        _ = try press(revertAssignment)
        try await waitForCondition { editor.selectedAction == nil }

        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
    }

    @MainActor func testKeyInspectorCondensesMatchingStateAndShowsGlobalLightingChanges() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root)
        await rig.session.start()
        let state = rig.session.state
        let editor = KeyAssignmentEditor()
        let currentLighting = state.keyLightingDescription(editor.selectedSource, onKeyboard: true)
        let currentAssignment = try XCTUnwrap(rig.session.assignmentWorkflow.snapshot).assignmentDescription(
            for: editor.selectedSource)
        let window = host(
            KeyInspector(
                state: state, workflow: rig.session.assignmentWorkflow, editor: editor, activity: rig.session.activity),
            width: 580, height: 620)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            let labels = self.labels(in: self.accessibilityElements(window))
            return labels.contains(currentLighting) && labels.contains(currentAssignment)
        }

        var labels = self.labels(in: self.accessibilityElements(window))
        XCTAssertFalse(labels.contains(where: { $0.hasPrefix("Preview:") || $0.hasPrefix("On keyboard:") }))
        XCTAssertFalse(labels.contains("Revert key lighting"))
        XCTAssertFalse(labels.contains("Revert key assignment"))

        state.selectLightingMode(11)
        let preview = state.keyLightingDescription(editor.selectedSource, onKeyboard: false)
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            return self.labels(in: self.accessibilityElements(window)).contains("Preview: " + preview)
        }

        labels = self.labels(in: self.accessibilityElements(window))
        XCTAssertTrue(labels.contains("On keyboard: " + currentLighting))
        XCTAssertTrue(labels.contains("Animation affects all keys."))
        XCTAssertFalse(labels.contains("Paint selected color"))
        XCTAssertFalse(labels.contains("Revert key lighting"))
        XCTAssertFalse(state.hasKeyLightingChange(editor.selectedSource))
        XCTAssertTrue(state.hasLightingChangesToApply)

        let events = await rig.log.events
        XCTAssertEqual(events, ["lighting", "assignments"])
    }

    @MainActor func testPermissionSetupStaysInMainWindowAndResumesAfterSettingsApproval() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root, access: .denied)
        let profiles = KeyboardProfileFiles(root: root)
        let window = host(ContentView(profiles: profiles, session: rig.session), width: 1100, height: 760)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        var elements: [NSObject] = []
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window)
            return self.labels(in: elements).contains("Open Input Monitoring")
        }

        XCTAssertNil(window.attachedSheet)
        XCTAssertFalse(rig.session.activity.isBusy)
        XCTAssertEqual(rig.permission.requests, 1)
        let settings = try XCTUnwrap(elements.first { text($0, "accessibilityLabel") == "Open Input Monitoring" })
        _ = try press(settings)
        try await waitForCondition { rig.permission.settingsOpened == 1 }

        XCTAssertEqual(rig.permission.requests, 2)
        let finder = try XCTUnwrap(elements.first { text($0, "accessibilityLabel") == "Show app in Finder" })
        _ = try press(finder)
        try await waitForCondition { rig.permission.applicationRevealed == 1 }

        XCTAssertNil(window.attachedSheet)
        let initialReads = await rig.log.events
        XCTAssertTrue(initialReads.isEmpty)
        rig.permission.access = .granted
        rig.session.applicationBecameActive()
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            return rig.session.assignmentWorkflow.snapshot != nil
                && !self.labels(in: self.accessibilityElements(window)).contains("Open Input Monitoring")
        }

        XCTAssertNil(window.attachedSheet)
        let reads = await rig.log.events
        XCTAssertEqual(reads, ["lighting", "assignments"])
    }

    @MainActor func testLoadingStatusUsesFooterWithoutMovingKeyboardOrButtons() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let detection = BlockingDetection()
        detection.release.signal()
        defer { detection.release.signal() }

        let lightingGate = SessionReadGate()
        let assignmentGate = SessionReadGate()
        await lightingGate.release()
        await assignmentGate.release()
        defer {
            Task {
                await lightingGate.release()
                await assignmentGate.release()
            }
        }

        let rig = SessionFixture(
            root: root, lightingGate: lightingGate, connection: KeyboardConnection(detect: { try detection.detect() }),
            assignmentGate: assignmentGate)
        let profiles = KeyboardProfileFiles(root: root)
        let window = host(ContentView(profiles: profiles, session: rig.session), width: 1100, height: 760)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        try await waitForCondition { rig.session.assignmentWorkflow.snapshot != nil && !rig.session.activity.isBusy }

        func frame(_ label: String) throws -> CGRect {
            view.layoutSubtreeIfNeeded()
            let element = try XCTUnwrap(
                self.accessibilityElements(window).first { self.text($0, "accessibilityLabel") == label })
            return try XCTUnwrap(element.value(forKey: "accessibilityFrame") as? NSValue).rectValue
        }

        func checkFooter(_ status: String, keyFrame: CGRect, reviewFrame: CGRect) async throws {
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                return self.accessibilityElements(window).contains {
                    self.text($0, "accessibilityIdentifier") == "keyboard-footer-status"
                        && self.labels(in: [$0]).contains(status)
                }
            }

            let elements = self.accessibilityElements(window)
            let statusElement = try XCTUnwrap(
                elements.first {
                    self.text($0, "accessibilityIdentifier") == "keyboard-footer-status"
                        && self.labels(in: [$0]).contains(status)
                })
            let statusFrame = try XCTUnwrap(statusElement.value(forKey: "accessibilityFrame") as? NSValue).rectValue
            XCTAssertEqual(statusFrame.midY, reviewFrame.midY, accuracy: 2)
            XCTAssertEqual(try frame("Esc"), keyFrame)
            XCTAssertEqual(try frame("Review changes…"), reviewFrame)
        }

        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            return self.labels(in: self.accessibilityElements(window)).contains("Review to apply.")
        }

        let keyFrame = try frame("Esc")
        let reviewFrame = try frame("Review changes…")
        await assignmentGate.reset()
        let refresh = Task { await rig.session.refresh(assignments: true) }

        try await waitForCondition { rig.session.connection.isChecking }

        try await checkFooter("Checking keyboard connection…", keyFrame: keyFrame, reviewFrame: reviewFrame)
        detection.release.signal()
        try await waitForCondition { rig.session.assignmentWorkflow.isBusy }

        try await checkFooter("Reading keyboard state…", keyFrame: keyFrame, reviewFrame: reviewFrame)
        await assignmentGate.release()
        await refresh.value
        try await checkFooter("Review to apply.", keyFrame: keyFrame, reviewFrame: reviewFrame)
        await lightingGate.reset()
        let lightingRead = Task { await rig.session.state.read() }

        try await waitForCondition { rig.session.state.isBusy }

        try await checkFooter("Reading keyboard state…", keyFrame: keyFrame, reviewFrame: reviewFrame)
        await lightingGate.release()
        await lightingRead.value
        try await checkFooter("Review to apply.", keyFrame: keyFrame, reviewFrame: reviewFrame)
    }

    @MainActor func testActualHomeShowsCombinedEditorsAndDisconnectedRefresh() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root, connected: false)
        let profiles = KeyboardProfileFiles(root: root)
        let editor = KeyAssignmentEditor()
        let window = host(
            ContentView(profiles: profiles, session: rig.session, editor: editor), width: 1100, height: 760)
        let view = try XCTUnwrap(window.contentViewController?.view)
        defer {
            window.close()
            window.contentViewController = nil
        }

        var elements: [NSObject] = []
        var labels: [String] = []
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window)
            labels = self.labels(in: elements)
            return labels.contains("Keyboard not connected")
        }

        XCTAssertTrue(labels.contains("Keyboard"))
        XCTAssertFalse(labels.contains("Profiles"))
        XCTAssertFalse(labels.contains("Assignment profiles"))
        XCTAssertFalse(labels.contains("Lighting profiles"))

        XCTAssertFalse(labels.contains("Overview"))
        let pressAction = NSSelectorFromString("accessibilityPerformPress")
        XCTAssertFalse(labels.contains("Edit assignments"))

        let refresh = try XCTUnwrap(elements.first { text($0, "accessibilityLabel") == "Refresh" })
        XCTAssertEqual(refresh.value(forKey: "accessibilityEnabled") as? Bool, true)
        let reads = await rig.log.events
        XCTAssertTrue(reads.isEmpty)
        XCTAssertEqual(rig.permission.requests, 0)
        rig.probe.setConnected(true)
        _ = try press(refresh)
        try await waitForCondition { rig.session.assignmentWorkflow.snapshot != nil && !rig.session.activity.isBusy }

        XCTAssertTrue(rig.session.connection.isConnected)
        XCTAssertNotNil(rig.session.state.snapshot)
        XCTAssertNotNil(rig.session.assignmentWorkflow.snapshot)
        let reconnectedReads = await rig.log.events
        XCTAssertEqual(reconnectedReads, ["lighting", "assignments"])
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            elements = self.accessibilityElements(window)
            return !self.labels(in: elements).contains("Keyboard not connected")
                && self.labels(in: elements).contains("Selected key: W")
        }

        XCTAssertTrue(self.labels(in: elements).contains("Selected key: W"))
        XCTAssertFalse(self.labels(in: elements).contains("Inspect selected key…"))

        // One refresh updates captured state while preserving staged edits.
        rig.session.state.selectLightingMode(Int(BuiltInMode.rolling.rawValue))
        rig.session.state.brightness = 15
        let lightingDraft = try rig.session.state.savedLightingDraft()
        elements = accessibilityElements(window)
        let physicalNames = Set(LegacyKey.physical.map(\.name))
        let keys = elements.filter {
            physicalNames.contains(text($0, "accessibilityLabel") ?? "")
                && text($0, "accessibilityRole") == NSAccessibility.Role.button.rawValue && $0.responds(to: pressAction)
        }

        XCTAssertEqual(keys.count, 115)

        let originalKey = try XCTUnwrap(keys.first { text($0, "accessibilityLabel") == "F1" })
        let originalFrame = try XCTUnwrap(originalKey.value(forKey: "accessibilityFrame") as? NSValue).rectValue
        try editor.set(KeyAction(kind: .keyboard, target: KeyActionKind.keyboard.targets[0].id), for: 39)
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            return self.labels(in: self.accessibilityElements(window)).contains("Output")
        }

        let stableKey = try XCTUnwrap(
            accessibilityElements(window).first {
                text($0, "accessibilityLabel") == "F1" && $0.responds(to: pressAction)
            })
        let stableFrame = try XCTUnwrap(stableKey.value(forKey: "accessibilityFrame") as? NSValue).rectValue
        XCTAssertEqual(stableFrame.width, originalFrame.width, accuracy: 0.1)
        XCTAssertEqual(stableFrame.height, originalFrame.height, accuracy: 0.1)

        let assignmentRefresh = accessibilityElements(window, includeSubviews: false).filter {
            text($0, "accessibilityLabel") == "Refresh"
                && text($0, "accessibilityRole") == NSAccessibility.Role.button.rawValue
        }

        XCTAssertEqual(assignmentRefresh.count, 1)
        let refreshAssignments = try XCTUnwrap(assignmentRefresh.first)

        // The hosting bridge can return false after dispatching; verify the resulting reads instead.
        _ = try self.press(refreshAssignments)
        try await waitForCondition {
            let events = await rig.log.events
            return events == ["lighting", "assignments", "assignments"] && !rig.session.activity.isBusy
        }

        XCTAssertEqual(try rig.session.state.savedLightingDraft(), lightingDraft)
        XCTAssertFalse(rig.session.state.confirmsLightingDiscard)

        var key: NSObject?
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            key = self.accessibilityElements(window).first {
                self.text($0, "accessibilityLabel") == "F1" && $0.responds(to: pressAction)
                    && $0.value(forKey: "accessibilityEnabled") as? Bool == true
            }

            return key != nil
        }

        _ = try press(XCTUnwrap(key))
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            return self.labels(in: self.accessibilityElements(window)).contains("Selected key: F1")
        }

        XCTAssertNil(window.attachedSheet)
        XCTAssertEqual(try rig.session.state.savedLightingDraft(), lightingDraft)

        rig.session.state.selectLightingMode(128)
        rig.session.state.selectPaintColor(RGB(red: 0x12, green: 0x34, blue: 0x56))
        let nextKey = try XCTUnwrap(LegacyKey.physical.first { $0.name == "F2" })
        view.layoutSubtreeIfNeeded()

        let paintKey = try XCTUnwrap(
            accessibilityElements(window).first {
                self.text($0, "accessibilityLabel") == "F2" && $0.responds(to: pressAction)
            })
        _ = try press(paintKey)
        try await waitForCondition { self.labels(in: self.accessibilityElements(window)).contains("Selected key: F2") }

        XCTAssertEqual(rig.session.state.slots[nextKey.index].rgb.hex, "#123456")
        XCTAssertNil(window.attachedSheet)

        var reviewButton: NSObject?
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            reviewButton = self.accessibilityElements(window).first {
                self.text($0, "accessibilityLabel") == "Review changes…" && $0.responds(to: pressAction)
                    && $0.value(forKey: "accessibilityEnabled") as? Bool == true
            }

            return reviewButton != nil
        }

        _ = try press(XCTUnwrap(reviewButton))
        try await waitForCondition {
            view.layoutSubtreeIfNeeded()
            return window.attachedSheet != nil
        }

        let sheet = try XCTUnwrap(window.attachedSheet)
        try await waitForCondition { self.labels(in: self.accessibilityElements(sheet)).contains("Apply changes") }

        let reviewLabels = self.labels(in: accessibilityElements(sheet))
        XCTAssertTrue(reviewLabels.contains("Assignments"))
        XCTAssertTrue(reviewLabels.contains(where: { $0.hasPrefix("Lighting ·") }))
        let cancel = try XCTUnwrap(accessibilityElements(sheet).first { text($0, "accessibilityLabel") == "Cancel" })
        _ = try press(cancel)
        try await waitForCondition { window.attachedSheet == nil }
    }

    @MainActor func testReviewShowsOpaqueProgressUntilSuccessOrFailureWithoutResizing() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        for fails in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }

            let gate = SessionReadGate()
            defer { Task { await gate.release() } }

            let activity = KeyboardDeviceActivity()
            let state = KeyboardState(
                activity: activity, service: SessionLightingService(log: SessionReadLog(), gate: nil), root: root,
                checkAccess: { true })
            let service = ReviewProgressService(gate: gate, fails: fails)
            let assignments = AssignmentWorkflow(
                service: service, root: root, activity: activity, checkAccess: { true })
            await assignments.read()
            let editor = KeyAssignmentEditor()
            try editor.set(KeyAction(kind: .disabled), for: 39)
            let workflow = KeyboardChangesWorkflow(
                state: state, assignments: assignments, editor: editor, activity: activity)
            let window = host(ReviewProgressHost(workflow: workflow), width: 800, height: 700)
            defer {
                window.close()
                window.contentViewController = nil
            }

            workflow.prepare()
            try await waitForCondition { window.attachedSheet != nil }

            let sheet = try XCTUnwrap(window.attachedSheet)
            let view = try XCTUnwrap(sheet.contentView)
            let action = NSSelectorFromString("accessibilityPerformPress")
            func control(_ label: String) -> NSObject? {
                accessibilityElements(sheet, includeSubviews: false).first {
                    text($0, "accessibilityLabel") == label && $0.responds(to: action)
                }
            }

            try await waitForCondition { control("Apply all reviewed changes to my keyboard") != nil }

            _ = try press(XCTUnwrap(control("Apply all reviewed changes to my keyboard")))
            try await waitForCondition {
                control("Apply changes")?.value(forKey: "accessibilityEnabled") as? Bool == true
            }

            let size = sheet.frame.size
            let applyButton = try XCTUnwrap(control("Apply changes"))
            _ = try press(applyButton)
            try await waitForCondition {
                view.layoutSubtreeIfNeeded()
                return workflow.isApplying
                    && self.labels(in: self.accessibilityElements(sheet, includeSubviews: false)).contains(
                        "Applying keyboard changes")
            }

            XCTAssertEqual(sheet.frame.size, size)
            let elements = accessibilityElements(sheet, includeSubviews: false)
            let labels = self.labels(in: elements)
            XCTAssertTrue(labels.contains("Keyboard update in progress"))
            XCTAssertTrue(labels.contains("Applying assignments…"))
            XCTAssertFalse(labels.contains("Review keyboard changes"))
            XCTAssertFalse(labels.contains("Apply all reviewed changes to my keyboard"))
            XCTAssertNil(control("Cancel"))
            XCTAssertNil(control("Apply changes"))
            try await waitForCondition { await service.writes == 1 }

            _ = try press(applyButton)
            let escape = try XCTUnwrap(
                NSEvent.keyEvent(
                    with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: sheet.windowNumber,
                    context: nil, characters: "\u{1b}", charactersIgnoringModifiers: "\u{1b}", isARepeat: false,
                    keyCode: 53))
            sheet.sendEvent(escape)
            await Task.yield()
            XCTAssertNotNil(workflow.review)
            XCTAssertTrue(window.attachedSheet === sheet)
            let writes = await service.writes
            XCTAssertEqual(writes, 1)
            let status = try XCTUnwrap(
                elements.first { text($0, "accessibilityIdentifier") == "keyboard-update-status" })
            let statusFrame = try XCTUnwrap(status.value(forKey: "accessibilityFrame") as? NSValue).rectValue
            XCTAssertTrue(sheet.frame.contains(statusFrame))
            try savePreview(of: view, named: "review-progress-\(fails ? "failure" : "success").png")

            await gate.release()
            try await waitForCondition { !workflow.isApplying && window.attachedSheet == nil }

            XCTAssertNil(workflow.review)
            XCTAssertEqual(workflow.error != nil, fails)
            XCTAssertFalse(activity.isBusy)
            if !fails {
                try editor.set(KeyAction(kind: .disabled), for: 90)
                workflow.prepare()
                try await waitForCondition { window.attachedSheet != nil }

                let nextSheet = try XCTUnwrap(window.attachedSheet)
                func nextControl(_ label: String) -> NSObject? {
                    self.accessibilityElements(nextSheet, includeSubviews: false).first {
                        self.text($0, "accessibilityLabel") == label && $0.responds(to: action)
                    }
                }

                try await waitForCondition { nextControl("Apply all reviewed changes to my keyboard") != nil }

                XCTAssertEqual(
                    try XCTUnwrap(nextControl("Apply changes")).value(forKey: "accessibilityEnabled") as? Bool, false)
                XCTAssertFalse(
                    self.labels(in: self.accessibilityElements(nextSheet, includeSubviews: false)).contains(
                        "Applying keyboard changes"))
                _ = try press(XCTUnwrap(nextControl("Cancel")))
                try await waitForCondition { window.attachedSheet == nil }
            }
        }
    }

    @MainActor func testProfileRecoveryRemainsAvailableAfterConnectedCaptureFails() async throws {
        let restoreAccessibility = enableAccessibility()
        defer { restoreAccessibility() }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let rig = SessionFixture(root: root, lightingError: .message("Injected capture failure"))
        let profiles = KeyboardProfileFiles(root: root)
        let window = host(ContentView(profiles: profiles, session: rig.session), width: 1100, height: 760)
        defer {
            window.close()
            window.contentViewController = nil
        }

        try await waitForCondition { rig.session.state.error != nil && !rig.session.activity.isBusy }

        XCTAssertTrue(rig.session.connection.isConnected)
        XCTAssertFalse(rig.session.state.hasSnapshot)
        let workflow = KeyboardProfileWorkflow(files: profiles, state: rig.session.state, editor: KeyAssignmentEditor())
        XCTAssertTrue(workflow.canLoad(.assignments))
        XCTAssertTrue(workflow.canSave(.assignments))
        XCTAssertFalse(workflow.canLoad(.lighting))
        XCTAssertFalse(workflow.canSave(.lighting))
    }

}

private struct ReviewProgressHost: View {
    @ObservedObject var workflow: KeyboardChangesWorkflow

    var body: some View {
        Color.clear.sheet(item: $workflow.review) { review in
            KeyboardChangesReviewSheet(workflow: workflow, review: review)
        }.disabled(workflow.isApplying)
    }
}

private actor ReviewProgressService: AssignmentDeviceService {
    private(set) var writes = 0
    let gate: SessionReadGate
    let fails: Bool
    private var channels = [Data(repeating: 0, count: 640)]

    init(gate: SessionReadGate, fails: Bool) {
        self.gate = gate
        self.fails = fails
    }

    func capture(output: URL) async throws -> AssignmentSnapshot {
        AssignmentSnapshot(location: 0x0114_0000, channels: channels)
    }

    func write(snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL) async throws
        -> AssignmentSnapshot
    {
        writes += 1
        await gate.wait()
        if fails { throw WorkflowError.message("Injected review progress failure") }

        channels[0] = candidate
        return AssignmentSnapshot(location: snapshot.location, channels: channels)
    }
}

@MainActor private final class LockPreviewModel: ObservableObject { @Published var reading: LockIndicators? }

private struct LockPreviewHost: View {
    let state: KeyboardState
    @ObservedObject var model: LockPreviewModel

    var body: some View {
        LightingView(
            state: state, keyboard: AssignmentKeyboard(selected: -1, planned: [], slots: state.previewSlots) { _ in },
            lockIndicators: model.reading)
    }
}
