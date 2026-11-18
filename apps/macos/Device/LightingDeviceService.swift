import C989Native
import Foundation

enum NativeLightingPolicy {
    static func validate(baseline: Data, candidate: Data) throws {
        var message = [CChar](repeating: 0, count: 1024)
        let code = baseline.withUnsafeBytes { before in
            candidate.withUnsafeBytes { after in
                c989_lighting_prepare(
                    before.bindMemory(to: UInt8.self).baseAddress, before.count,
                    after.bindMemory(to: UInt8.self).baseAddress, after.count, &message, message.count)
            }
        }

        try NativeDeviceError.check(code, message: message)
    }
}

protocol LightingDeviceService: Sendable {
    func capture(output: URL) async throws -> DeviceSnapshot
    func write(snapshot: DeviceSnapshot, candidate: Data, output: URL) async throws -> DeviceSnapshot
}

struct NativeLightingDeviceService: LightingDeviceService {
    func capture(output: URL) async throws -> DeviceSnapshot {
        try await Task.detached(priority: .userInitiated) {
            let capture = try NativeKeyboardBackend().capture(channel: .lighting, output: output)
            let lighting = capture.data
            let location = capture.location
            try LightingCandidateBuilder.validate(lighting)
            guard location != 0 else { throw WorkflowError.message("Invalid keyboard location.") }

            return DeviceSnapshot(
                location: String(format: "%08X", location), lightingData: lighting,
                lockIndicators: capture.lockIndicators)
        }.value
    }

    func write(snapshot: DeviceSnapshot, candidate: Data, output: URL) async throws -> DeviceSnapshot {
        try await Task.detached(priority: .userInitiated) {
            guard let location = UInt32(snapshot.location, radix: 16), location != 0 else {
                throw WorkflowError.message("Invalid keyboard location.")
            }

            let lockIndicators = try NativeKeyboardBackend().writeLighting(
                baseline: snapshot.lightingData, location: location, candidate: candidate, output: output)
            return DeviceSnapshot(location: snapshot.location, lightingData: candidate, lockIndicators: lockIndicators)
        }.value
    }
}
