import Foundation

struct LockIndicators: Equatable, Sendable {
    let rawValue: UInt8
    let sampledAt = ContinuousClock().now

    // Sampling time selects the newest reading without changing lock-state equality.
    static func == (left: Self, right: Self) -> Bool { left.rawValue == right.rawValue }

    static func latest(_ first: Self?, _ second: Self?) -> Self? {
        guard let first else { return second }

        guard let second else { return first }

        return first.sampledAt <= second.sampledAt ? second : first
    }

    var numLock: Bool { rawValue & 0x01 != 0 }

    var capsLock: Bool { rawValue & 0x02 != 0 }

    var scrollLock: Bool { rawValue & 0x04 != 0 }
}
