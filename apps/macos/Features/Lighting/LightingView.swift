import AppKit
import SwiftUI

struct LightingView<Keyboard: View>: View {
    @ObservedObject var state: KeyboardState
    var keyboard: Keyboard
    var lockIndicators: LockIndicators? = nil
    var assignmentContent: AnyView? = nil
    var reviewChanges: (() -> Void)? = nil
    var canReviewChanges: Bool? = nil
    var reviewStatus: String? = nil
    var operationStatus: String? = nil
    var isOperationInProgress = false
    @ObservedObject private var activity = KeyboardDeviceActivity.shared
    @Environment(\.isEnabled) private var isEnabled
    private var isBusy: Bool { state.isBusy || isOperationInProgress }

    var body: some View {
        Group { if state.hasSnapshot { lightingEditor } else { unavailableKeyboard } }.safeAreaInset(
            edge: .bottom, spacing: 0
        ) {
            HStack(spacing: 16) {
                ZStack { if isBusy { ProgressView().controlSize(.small) } }.frame(width: 16, height: 16)
                Text(footerStatus).font(.caption).foregroundStyle(.secondary).lineLimit(1).help(footerStatus)
                    .accessibilityIdentifier("keyboard-footer-status")
                Spacer(minLength: 8)
                Button("Reset") { state.resetLighting() }.disabled(
                    !state.hasSnapshot || isBusy || !state.hasUnsavedLightingChanges)
                Button("Review changes…") { if let reviewChanges { reviewChanges() } else { state.prepareReview() } }
                    .buttonStyle(.borderedProminent).disabled(
                        !state.hasSnapshot || isBusy || !(canReviewChanges ?? state.hasLightingChangesToApply)
                            || activity.requiresInspection)
            }.frame(height: 28).padding(.horizontal, 20).padding(.vertical, 12).background(.bar).overlay(
                alignment: .top
            ) { Divider() }
        }.modifier(LightingReviewPresentation(state: state, presentsReview: reviewChanges == nil))
    }

    private var footerStatus: String {
        if let operationStatus { return operationStatus }

        if state.isBusy { return state.status }

        if state.hasUnpaintedKeys { return "Paint or fill every blank key before reviewing." }

        return reviewStatus ?? "Review to apply."
    }

    private var unavailableKeyboard: some View {
        VStack(spacing: 14) {
            if let error = state.error {
                FeedbackBanner(text: error, symbol: "exclamationmark.triangle", tint: .orange)
            }

            if let warning = state.warning { FeedbackBanner(text: warning, tint: .orange) }

            if state.needsInputMonitoringAccess {
                Button("Open Input Monitoring settings", action: KeyboardPermission.openSettings)
            }

            ContentUnavailableView(
                "Keyboard settings", systemImage: "keyboard",
                description: Text("Connect your keyboard and allow access to load its settings.")
            ).frame(maxWidth: .infinity, maxHeight: .infinity)
        }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity).accessibilityElement(children: .contain)
            .accessibilityIdentifier("unavailable-keyboard-pane")
    }

    private var lightingEditor: some View {
        // Keep scrolling enabled while actions are disabled. Disabling the native
        // scroll view removes its scrollbar gutter and shifts the keyboard.
        ScrollView(.vertical) {
            VStack(alignment: .leading, spacing: 14) {
                Text("Keyboard").font(.largeTitle.bold())
                if let error = state.error {
                    FeedbackBanner(text: error, symbol: "exclamationmark.triangle", tint: .orange)
                }

                if let warning = state.warning { FeedbackBanner(text: warning, tint: .orange) }

                if state.needsInputMonitoringAccess {
                    Button("Open Input Monitoring settings", action: KeyboardPermission.openSettings)
                }

                Group {
                    WorkspaceCard {
                        ViewThatFits(in: .horizontal) {
                            HStack(alignment: .bottom, spacing: 16) {
                                modePicker
                                paintControls

                                adjustment
                            }

                            VStack(alignment: .leading, spacing: 12) {
                                HStack(alignment: .bottom, spacing: 16) {
                                    modePicker
                                    adjustment
                                }

                                paintControls
                            }
                        }
                    }
                }

                WorkspaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text(state.hasUnsavedLightingChanges ? "Preview changes" : "Current keyboard").font(
                                .headline)
                            Spacer()
                        }.overlay(alignment: .trailing) { LockIndicatorRow(indicators: lockIndicators) }

                        keyboard

                        Text(paintHint).font(.caption).foregroundStyle(.secondary)
                    }
                }

                assignmentContent
            }.padding(20).frame(maxWidth: 1100, alignment: .leading).disabled(!isEnabled)
        }.environment(\.isEnabled, true)
    }

    private var modePicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Lighting Mode").font(.caption).foregroundStyle(.secondary)
            Picker(
                "Lighting mode", selection: Binding(get: { state.selectedLightingMode }, set: state.selectLightingMode)
            ) {
                Text("Solid color").tag(128)
                ForEach(BuiltInMode.allCases) { mode in Text(mode.name).tag(Int(mode.rawValue)) }
            }.pickerStyle(.menu).labelsHidden().frame(width: 150)
        }
    }

    private var paintControls: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Tools · \(state.paintTool.rawValue)").font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                // Tool toggles select one tool; pressing the active tool keeps it selected.
                Toggle(isOn: Binding(get: { state.isPointerSelected }, set: { _ in state.selectPointer() })) {
                    Image(systemName: "cursorarrow").frame(width: 24, height: 24)
                }.toggleStyle(.button).tint(.accentColor).accessibilityLabel("Select keys").accessibilityAddTraits(
                    state.isPointerSelected ? .isSelected : []
                ).help("Select keys without painting")
                if state.editorStyle != .animation {
                    LightingPaintToolControl(state: state, tool: .brush)
                    LightingPaintToolControl(state: state, tool: .bucket)
                    Button("Clear") { state.clearPaint() }.fixedSize().disabled(
                        state.unpaintedKeys == LegacyKey.physicalIndices
                    ).help("Start a blank lighting preview")
                    Button("Fill") { state.fillPaint() }.fixedSize().disabled(state.unpaintedKeys.isEmpty).help(
                        "Color unpainted keys with the selected paint color")
                }
            }
        }
    }

    private var adjustment: some View {
        VStack(alignment: .leading, spacing: 6) {
            if state.editorStyle == .animation {
                Text("Speed \(state.animationSpeed)").font(.caption).foregroundStyle(.secondary)
                AccentSlider(
                    value: Binding(
                        get: { Double(state.animationSpeed) },
                        set: {
                            state.animationSpeed = Int($0.rounded())
                            state.draftChanged()
                        }), range: 2...16
                ).accessibilityLabel("Animation speed")
            } else {
                Text("Brightness \(state.brightnessPercent)%").font(.caption).foregroundStyle(.secondary)
                AccentSlider(
                    value: Binding(
                        get: { Double(state.brightness) },
                        set: {
                            state.brightness = Int($0.rounded())
                            state.draftChanged()
                        }), range: 0...16
                ).accessibilityLabel("Brightness")
            }
        }.frame(minWidth: 110, idealWidth: 150, maxWidth: 180)
    }

    private var paintHint: String {
        if state.editorStyle == .animation {
            return state.unpaintedKeys.isEmpty
                ? "Color preview; animation plays on the keyboard."
                : "The animation plays on the keyboard. Your unfinished solid-color draft is kept when you switch back."
        }

        if state.isPointerSelected { return "Click a key to inspect it, or choose a paint tool." }

        if state.hasUnpaintedKeys {
            return
                "\(115 - state.unpaintedKeys.count) painted · \(state.unpaintedKeys.count) unpainted. Fill colors blank keys only."
        }

        if state.paintTool == .bucket {
            return "Click a key to flood-fill its connected color region. Fill colors blank keys only."
        }

        return "Click keys to paint \(state.paintColorName). Fill colors blank keys only."
    }
}

private struct LightingPaintToolControl: View {
    @ObservedObject var state: KeyboardState
    let tool: LightingPaintTool
    @State private var showsPalette = false
    @State private var showsCustom = false
    @State private var customColor = Color.white

    private var color: RGB { tool == .bucket ? state.bucketColor : state.keyPaintColor }

    private var lastColor: RGB { tool == .bucket ? state.lastBucketColor : state.lastUsedColor }

    private var selected: Bool { state.paintTool == tool && state.editorStyle != .animation }

    var body: some View {
        HStack(spacing: 0) {
            Toggle(isOn: Binding(get: { selected }, set: { _ in state.selectPaintTool(tool) })) {
                LightingPaintToolIcon(tool: tool).overlay(alignment: .bottomTrailing) {
                    Circle().fill(color.color).frame(width: 9, height: 9).overlay { Circle().stroke(.gray) }
                }
            }.toggleStyle(.button).accessibilityLabel(tool.rawValue).help(tool.rawValue + " · " + color.hex)
                .accessibilityAddTraits(selected ? .isSelected : [])
            Button {
                showsPalette.toggle()
            } label: {
                Image(systemName: "chevron.down").font(.caption).frame(height: 24)
            }.accessibilityLabel(tool.rawValue + " colors").popover(isPresented: $showsPalette) {
                VStack(alignment: .leading, spacing: 12) {
                    Text(tool.rawValue).font(.headline)
                    HStack(spacing: 6) {
                        Text("Last").font(.caption)
                        swatch(lastColor, name: "Last-used color")
                        Divider().frame(height: 22)
                        ForEach(LightingColorPreset.all) { preset in swatch(preset.rgb, name: preset.name) }
                    }

                    Button("Custom…") {
                        customColor = color.color
                        showsPalette = false
                        showsCustom = true
                    }
                }.padding(16)
            }
        }.tint(.accentColor).disabled(state.editorStyle == .animation).popover(isPresented: $showsCustom) {
            VStack(spacing: 0) {
                FullRangeColorEditor(selection: $customColor)
                HStack {
                    Button("Cancel") { showsCustom = false }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button("Use color") {
                        if state.editorStyle != .animation {
                            let converted = NSColor(customColor).usingColorSpace(.sRGB) ?? .white
                            choose(
                                RGB(
                                    red: UInt8(clamping: Int((converted.redComponent * 255).rounded())),
                                    green: UInt8(clamping: Int((converted.greenComponent * 255).rounded())),
                                    blue: UInt8(clamping: Int((converted.blueComponent * 255).rounded()))))
                        }

                        showsCustom = false
                    }.keyboardShortcut(.defaultAction)
                }.padding([.horizontal, .bottom], 16)
            }
        }.onChange(of: state.editorStyle) { _, style in
            if style == .animation {
                showsPalette = false
                showsCustom = false
            }
        }.onChange(of: state.paintTool) { _, active in if active != tool { showsPalette = false } }
    }

    private func choose(_ color: RGB) {
        if tool == .bucket { state.selectBucketColor(color) } else { state.selectPaintColor(color) }
    }

    private func swatch(_ value: RGB, name: String) -> some View {
        Button {
            choose(value)
            showsPalette = false
        } label: {
            Circle().fill(value.color).frame(width: 20, height: 20).overlay {
                if value.isOff { Text("╱").foregroundStyle(.white) }
            }.overlay { Circle().strokeBorder(color == value ? Color.accentColor : .gray, lineWidth: 2) }
        }.buttonStyle(.plain).help("\(name) · \(value.hex)").accessibilityLabel(tool.rawValue + " " + name)
            .accessibilityValue(value.hex)
    }
}

private struct LightingReviewPresentation: ViewModifier {
    @ObservedObject var state: KeyboardState
    let presentsReview: Bool

    @ViewBuilder func body(content: Content) -> some View {
        if presentsReview {
            content.sheet(item: $state.review) { WriteReviewSheet(state: state, review: $0) }
        } else {
            content
        }
    }
}

enum ColorFieldMath {
    static func normalized(_ position: CGFloat, length: CGFloat) -> CGFloat {
        guard length > 0 else { return 0 }

        return min(max(position / length, 0), 1)
    }
}

private struct HSBColor {
    var hue: CGFloat
    var saturation: CGFloat
    var brightness: CGFloat

    init(_ color: Color) {
        let converted = NSColor(color).usingColorSpace(.sRGB) ?? .white
        var hue: CGFloat = 0
        var saturation: CGFloat = 0
        var brightness: CGFloat = 0
        converted.getHue(&hue, saturation: &saturation, brightness: &brightness, alpha: nil)
        self.hue = hue
        self.saturation = saturation
        self.brightness = brightness
    }

    var color: Color { Color(hue: hue, saturation: saturation, brightness: brightness) }
}

private struct FullRangeColorEditor: View {
    @Binding var selection: Color
    @State private var hsb: HSBColor

    init(selection: Binding<Color>) {
        _selection = selection
        _hsb = State(initialValue: HSBColor(selection.wrappedValue))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Choose color").font(.headline)
            SaturationBrightnessField(hue: hsb.hue, saturation: $hsb.saturation, brightness: $hsb.brightness)
            HueField(hue: $hsb.hue)
            HStack(spacing: 10) {
                ForEach(0..<3) { channel in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(["Red", "Green", "Blue"][channel]).font(.caption)
                        TextField(
                            ["Red", "Green", "Blue"][channel], value: channelBinding(channel),
                            format: .number.grouping(.never)
                        ).frame(width: 65)
                    }
                }
            }

            HStack {
                RoundedRectangle(cornerRadius: 5).fill(hsb.color).frame(width: 34, height: 22)
                Text(hexColor).monospaced().foregroundStyle(.secondary)
            }
        }.padding(16).onChange(of: hsb.hue) { _, _ in commit() }.onChange(of: hsb.saturation) { _, _ in commit() }
            .onChange(of: hsb.brightness) { _, _ in commit() }
    }

    private func channelBinding(_ channel: Int) -> Binding<Int> {
        Binding(
            get: {
                let color = NSColor(selection).usingColorSpace(.sRGB) ?? .white
                return Int(([color.redComponent, color.greenComponent, color.blueComponent][channel] * 255).rounded())
            },
            set: { value in
                let color = NSColor(selection).usingColorSpace(.sRGB) ?? .white
                var channels = [color.redComponent, color.greenComponent, color.blueComponent]
                channels[channel] = CGFloat(min(255, max(0, value))) / 255
                let next = Color(red: Double(channels[0]), green: Double(channels[1]), blue: Double(channels[2]))
                selection = next
                hsb = HSBColor(next)
            })
    }

    private var hexColor: String {
        let color = NSColor(hsb.color).usingColorSpace(.sRGB) ?? .white
        return String(
            format: "#%02X%02X%02X", Int((color.redComponent * 255).rounded()),
            Int((color.greenComponent * 255).rounded()), Int((color.blueComponent * 255).rounded()))
    }

    private func commit() { selection = hsb.color }
}

private struct SaturationBrightnessField: View {
    let hue: CGFloat
    @Binding var saturation: CGFloat
    @Binding var brightness: CGFloat
    private let size = CGSize(width: 240, height: 170)

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 6).fill(
                LinearGradient(
                    colors: [.white, Color(hue: hue, saturation: 1, brightness: 1)], startPoint: .leading,
                    endPoint: .trailing)
            ).overlay {
                RoundedRectangle(cornerRadius: 6).fill(
                    LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom))
            }.clipShape(RoundedRectangle(cornerRadius: 6))
            Circle().fill(.clear).stroke(.white, lineWidth: 2).background(
                Circle().stroke(.black.opacity(0.75), lineWidth: 4)
            ).frame(width: 13, height: 13).position(x: saturation * size.width, y: (1 - brightness) * size.height)
                .allowsHitTesting(false)
        }.frame(width: size.width, height: size.height).contentShape(Rectangle()).gesture(
            DragGesture(minimumDistance: 0).onChanged { value in
                saturation = ColorFieldMath.normalized(value.location.x, length: size.width)
                brightness = 1 - ColorFieldMath.normalized(value.location.y, length: size.height)
            }
        ).padding(7).accessibilityElement(children: .ignore).accessibilityLabel("Saturation and brightness")
            .accessibilityValue(
                "Saturation \(Int((saturation * 100).rounded())) percent, "
                    + "brightness \(Int((brightness * 100).rounded())) percent"
            ).accessibilityAction(named: "Increase saturation") { saturation = min(saturation + 0.01, 1) }
            .accessibilityAction(named: "Decrease saturation") { saturation = max(saturation - 0.01, 0) }
            .accessibilityAction(named: "Increase brightness") { brightness = min(brightness + 0.01, 1) }
            .accessibilityAction(named: "Decrease brightness") { brightness = max(brightness - 0.01, 0) }
    }
}

private struct HueField: View {
    @Binding var hue: CGFloat
    private let width: CGFloat = 240
    private let height: CGFloat = 14
    private static let colors: [Color] = stride(from: 0.0, through: 1.0, by: 1.0 / 12.0).map {
        Color(hue: $0, saturation: 1, brightness: 1)
    }

    var body: some View {
        ZStack(alignment: .leading) {
            RoundedRectangle(cornerRadius: height / 2).fill(
                LinearGradient(colors: Self.colors, startPoint: .leading, endPoint: .trailing))
            Circle().fill(Color(hue: hue, saturation: 1, brightness: 1)).stroke(.white, lineWidth: 2).shadow(radius: 1)
                .frame(width: 16, height: 16).position(x: hue * width, y: height / 2).allowsHitTesting(false)
        }.frame(width: width, height: height).contentShape(Rectangle()).gesture(
            DragGesture(minimumDistance: 0).onChanged { value in
                hue = ColorFieldMath.normalized(value.location.x, length: width)
            }
        ).padding(.horizontal, 8).accessibilityElement(children: .ignore).accessibilityLabel("Hue").accessibilityValue(
            "\(Int((hue * 360).rounded())) degrees"
        ).accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: hue = min(hue + 1 / 360, 1)
            case .decrement: hue = max(hue - 1 / 360, 0)
            @unknown default: break
            }
        }
    }
}

struct WriteReviewSheet: View {
    @ObservedObject var state: KeyboardState
    let review: WriteReview
    @Environment(\.dismiss) private var dismiss
    @State private var acknowledged = false
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("Review lighting changes", systemImage: "exclamationmark.shield.fill").font(.title.bold())
                .foregroundStyle(.orange)
            Text("These changes will be saved to your keyboard.")
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                GridRow {
                    Text("Lighting").foregroundStyle(.secondary)
                    Text(review.selectionName)
                }

                GridRow {
                    Text("Keys").foregroundStyle(.secondary)
                    Text(
                        review.changedKeys.isEmpty
                            ? "Effect or brightness only" : review.changedKeys.joined(separator: ", ")
                    ).lineLimit(4)
                }
            }

            Text("Keep your keyboard connected until the update finishes.").font(.callout).foregroundStyle(.secondary)
            Toggle("Apply these lighting changes to my keyboard.", isOn: $acknowledged)
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Apply lighting") { state.executeReviewedWrite(review) }.buttonStyle(.borderedProminent)
                    .disabled(!acknowledged || state.isBusy)
            }
        }.padding(26).frame(width: 650)
    }
}
