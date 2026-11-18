import SwiftUI

struct AssignmentKeyboard: View {
    let selected: Int
    let planned: Set<Int>
    var slots: [LEDSlot] = []
    var unpainted = Set<Int>()
    let select: (Int) -> Void
    private static let surroundingInset: CGFloat = 12

    var body: some View {
        ScrollView(.horizontal) {
            keycaps.containerRelativeFrame(.horizontal) { length, _ in
                max(length - Self.surroundingInset * 2, PhysicalKeyboardGeometry.minimumWidth)
            }.padding(Self.surroundingInset)
        }.accessibilityLabel("Physical C989 keyboard")
    }

    var keycaps: some View {
        PhysicalKeyboardLayout {
            ForEach(LegacyKey.physical) { key in
                let isSelected = selected == key.index
                let isPlanned = planned.contains(key.index)
                let lighting = lighting(for: key.index)
                let isUnpainted = unpainted.contains(key.index)
                Button {
                    select(key.index)
                } label: {
                    Text(Self.compactName(key.name)).font(.system(size: 10, weight: .medium)).lineLimit(1)
                        .minimumScaleFactor(0.7).padding(.horizontal, 2).frame(
                            maxWidth: .infinity, maxHeight: .infinity)
                }.buttonStyle(
                    KeyboardKeycapStyle(
                        illumination: lighting.color, selected: isSelected, planned: isPlanned, unpainted: isUnpainted)
                ).help(key.name + lighting.description).accessibilityLabel(key.name).accessibilityValue(
                    "\(isSelected ? "Selected. " : "")\(isPlanned ? "Planned change" : "No planned change")\(lighting.description)"
                ).accessibilityAddTraits(isSelected ? .isSelected : []).layoutValue(
                    key: KeyboardPositionKey.self, value: key.display)
            }
        }.background {
            let shape = RoundedRectangle(cornerRadius: 12)
            shape.fill(
                LinearGradient(
                    colors: [Color(red: 0.17, green: 0.19, blue: 0.23), Color(red: 0.08, green: 0.10, blue: 0.13)],
                    startPoint: .topLeading, endPoint: .bottomTrailing)
            ).overlay { shape.strokeBorder(.white.opacity(0.14)) }.padding(-7).shadow(
                color: .black.opacity(0.2), radius: 3, y: 1)
        }
    }

    private func lighting(for source: Int) -> (color: Color?, description: String) {
        if unpainted.contains(source) { return (nil, ", unpainted") }

        guard slots.indices.contains(source) else { return (nil, "") }

        let slot = slots[source]
        guard slot.enabled, !slot.rgb.isOff else { return (nil, ", lighting off") }

        return (slot.rgb.color, ", color \(slot.rgb.hex)")
    }

    private static let compactNames = [
        "Left Shift": "⇧ L", "Right Shift": "⇧ R", "Left Ctrl": "⌃ L", "Right Ctrl": "⌃ R", "Left GUI": "⌘",
        "Left Alt": "⌥ L", "Right Alt": "⌥ R", "Left Space": "Sp L", "Right Space": "Sp R", "Backspace": "⌫",
        "Enter": "↩", "Caps Lock": "⇪", "Tab": "⇥", "Application": "Menu", "Num Lock": "Num", "Scroll Lock": "Scr",
        "Print": "Prt", "Home": "Hm", "Pause": "Paus", "Page Up": "Pg↑", "Page Down": "Pg↓", "Insert": "Ins",
        "Delete": "Del", "Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "Numpad Decimal": ".", "Numpad Enter": "↩"
    ]

    private static func compactName(_ name: String) -> String {
        compactNames[name] ?? name.replacingOccurrences(of: "Numpad ", with: "")
    }
}

private struct KeyboardKeycapStyle: ButtonStyle {
    let illumination: Color?
    let selected: Bool
    let planned: Bool
    let unpainted: Bool
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused
    @State private var hovered = false

    private static let face = LinearGradient(
        colors: [Color(red: 0.16, green: 0.19, blue: 0.23), Color(red: 0.10, green: 0.12, blue: 0.16)],
        startPoint: .topLeading, endPoint: .bottomTrailing)
    private static let bevel = LinearGradient(
        colors: [.white.opacity(0.22), .black.opacity(0.3)], startPoint: .top, endPoint: .bottom)

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: 5)

        // Press feedback changes brightness so the hit target stays stationary.
        configuration.label.foregroundStyle(.white.opacity(0.88)).background {
            shape.fill(Self.face).overlay { shape.fill((illumination ?? .clear).opacity(0.08)) }.overlay {
                shape.strokeBorder(Self.bevel)
            }.shadow(color: .black.opacity(0.5), radius: 1, y: 2)
        }.overlay(alignment: .bottom) {
            RoundedRectangle(cornerRadius: 2).fill(illumination ?? .black.opacity(0.35)).frame(height: 3).padding(
                .horizontal, 2
            ).padding(.bottom, 1).shadow(color: (illumination ?? .clear).opacity(0.8), radius: 3, y: 1)
        }.overlay {
            shape.strokeBorder((illumination ?? .white).opacity(hovered && isEnabled ? 0.65 : 0.20))
            if unpainted { shape.strokeBorder(.white.opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [3, 2])) }
        }.overlay(alignment: .topTrailing) {
            if planned { Circle().fill(Color.accentColor).frame(width: 5, height: 5).padding(4) }
        }.overlay {
            if selected { shape.stroke(Color.accentColor, lineWidth: 2).padding(-1) }

            if isFocused && isEnabled { shape.strokeBorder(.white, lineWidth: 1).padding(2) }
        }.brightness(configuration.isPressed && isEnabled ? -0.10 : 0).contentShape(shape).onHover { hovered = $0 }
    }
}
