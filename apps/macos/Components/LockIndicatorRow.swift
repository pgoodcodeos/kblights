import SwiftUI

struct LockIndicatorRow: View {
    let indicators: LockIndicators?
    private let indicatorWidth: CGFloat = 76
    private let indicatorSpacing: CGFloat = 20

    var body: some View {
        Group {
            if let indicators {
                HStack(spacing: indicatorSpacing) {
                    indicator("Caps Lock", enabled: indicators.capsLock)
                    indicator("Num Lock", enabled: indicators.numLock)
                    indicator("Scroll Lock", enabled: indicators.scrollLock)
                }
            } else {
                Text("Lock state unavailable").foregroundStyle(.gray)
            }
        }.frame(width: 3 * indicatorWidth + 2 * indicatorSpacing, alignment: .trailing).font(
            .system(size: 11, weight: .semibold)
        ).fixedSize().accessibilityElement(children: .contain).accessibilityIdentifier("lock-indicators")
    }

    private func indicator(_ name: String, enabled: Bool) -> some View {
        let state = enabled ? "On" : "Off"
        return Text(name).foregroundStyle(enabled ? Color.green : Color.gray).frame(
            width: indicatorWidth, alignment: .leading
        ).accessibilityLabel(name).accessibilityValue(state).help("\(name): \(state). Last keyboard reading.")
    }
}
