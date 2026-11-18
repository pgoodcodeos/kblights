import AppKit
import SwiftUI

struct AccentSlider: NSViewRepresentable {
    @Binding var value: Double
    let range: ClosedRange<Double>
    @Environment(\.isEnabled) private var isEnabled

    func makeNSView(context: Context) -> NSSlider {
        let slider = SteppedAccentSlider(
            value: value, minValue: range.lowerBound, maxValue: range.upperBound, target: context.coordinator,
            action: #selector(Coordinator.changed(_:)))
        slider.cell = AccentSliderCell()
        slider.target = context.coordinator
        slider.action = #selector(Coordinator.changed(_:))
        slider.isContinuous = true
        updateNSView(slider, context: context)
        return slider
    }

    func updateNSView(_ slider: NSSlider, context: Context) {
        context.coordinator.value = $value
        slider.minValue = range.lowerBound
        slider.maxValue = range.upperBound
        slider.doubleValue = value
        slider.isEnabled = isEnabled
        slider.needsDisplay = true
    }

    func makeCoordinator() -> Coordinator { Coordinator(value: $value) }

    @MainActor final class Coordinator: NSObject {
        var value: Binding<Double>
        init(value: Binding<Double>) { self.value = value }

        @objc func changed(_ slider: NSSlider) {
            guard slider.isEnabled, slider.doubleValue.isFinite else { return }

            let updated = slider.doubleValue.rounded()
            slider.doubleValue = updated
            guard updated != value.wrappedValue else { return }

            value.wrappedValue = updated
        }
    }
}

private final class SteppedAccentSlider: NSSlider {
    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 123, 125: _ = step(-1)
        case 124, 126: _ = step(1)
        default: super.keyDown(with: event)
        }
    }

    override func accessibilityPerformIncrement() -> Bool { step(1) }

    override func accessibilityPerformDecrement() -> Bool { step(-1) }

    private func step(_ amount: Double) -> Bool {
        guard isEnabled else { return false }

        let updated = min(maxValue, max(minValue, doubleValue.rounded() + amount))
        guard updated != doubleValue else { return false }

        doubleValue = updated
        sendAction(action, to: target)
        return true
    }
}

private final class AccentSliderCell: NSSliderCell {
    override func drawBar(inside rect: NSRect, flipped: Bool) {
        let track = NSRect(x: rect.minX, y: rect.midY - 2, width: rect.width, height: 4)
        NSColor.tertiaryLabelColor.withAlphaComponent(0.25).setFill()
        NSBezierPath(roundedRect: track, xRadius: 2, yRadius: 2).fill()
        let fraction = maxValue > minValue ? min(1, max(0, (doubleValue - minValue) / (maxValue - minValue))) : 0
        let filled = NSRect(x: track.minX, y: track.minY, width: track.width * fraction, height: track.height)
        (isEnabled ? NSColor.controlAccentColor : NSColor.disabledControlTextColor).setFill()
        NSBezierPath(roundedRect: filled, xRadius: 2, yRadius: 2).fill()
    }
}
