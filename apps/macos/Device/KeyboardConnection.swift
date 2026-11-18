import Combine
import Foundation

@MainActor final class KeyboardConnection: ObservableObject {
    enum State: Equatable {
        case unknown
        case checking
        case connected
        case disconnected
        case unavailable(String)
    }

    @Published private(set) var state = State.unknown
    private var isRunning = false
    private let detect: @Sendable () throws -> Bool

    init(detect: @escaping @Sendable () throws -> Bool = { try MacKeyboardTransport.isConnected() }) {
        self.detect = detect
    }

    var isConnected: Bool { state == .connected }

    var isChecking: Bool { state == .checking }

    var message: String {
        switch state {
        case .unknown, .checking: "Checking keyboard connection…"
        case .connected: "Keyboard connected"
        case .disconnected: "Keyboard not connected"
        case .unavailable(let reason): reason
        }
    }

    func check(then operation: @MainActor () async -> Void) async {
        guard !isRunning, !Task.isCancelled else { return }

        isRunning = true
        defer { isRunning = false }

        state = .checking
        let detect = detect
        do {
            let connected = try await Task.detached { try detect() }.value
            guard !Task.isCancelled else {
                state = .unknown
                return
            }

            state = connected ? .connected : .disconnected
            if connected { await operation() }
        } catch { state = .unavailable(error.localizedDescription) }
    }
}
