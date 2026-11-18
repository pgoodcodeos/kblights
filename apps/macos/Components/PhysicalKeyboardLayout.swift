import SwiftUI

// Display coordinates describe the keycaps, not the firmware's row/column matrix.
struct KeyboardKeyPosition: Decodable, Equatable, Sendable {
    let left: Int
    let top: Int
    let width: Int
    let height: Int

    var isValid: Bool { left >= 0 && top >= 0 && width > 0 && height > 0 }
}

struct PhysicalKeyboardGeometry {
    static let minimumWidth: CGFloat = 700
    static let columns = LegacyKey.physical.map { $0.display.left + $0.display.width }.max() ?? 1
    static let rows = LegacyKey.physical.map { $0.display.top + $0.display.height }.max() ?? 1
    static let gap: CGFloat = 3

    let size: CGSize
    private let unit: CGFloat
    private let rowPitch: CGFloat

    init(width: CGFloat) {
        unit = width / CGFloat(Self.columns)
        rowPitch = max(32, unit * 4) + Self.gap
        size = CGSize(width: width, height: CGFloat(Self.rows) * rowPitch - Self.gap)
    }

    func frame(_ position: KeyboardKeyPosition) -> CGRect {
        CGRect(
            x: CGFloat(position.left) * unit, y: CGFloat(position.top) * rowPitch,
            width: CGFloat(position.width) * unit - Self.gap, height: CGFloat(position.height) * rowPitch - Self.gap)
    }
}

struct KeyboardPositionKey: LayoutValueKey {
    static let defaultValue = KeyboardKeyPosition(left: 0, top: 0, width: 4, height: 1)
}

struct PhysicalKeyboardLayout: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        PhysicalKeyboardGeometry(width: max(PhysicalKeyboardGeometry.minimumWidth, proposal.width ?? 800)).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let geometry = PhysicalKeyboardGeometry(width: bounds.width)
        for subview in subviews {
            let frame = geometry.frame(subview[KeyboardPositionKey.self])
            subview.place(
                at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY), anchor: .topLeading,
                proposal: ProposedViewSize(frame.size))
        }
    }
}
