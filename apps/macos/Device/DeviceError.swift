import C989Native
import Foundation

struct NativeDeviceError: LocalizedError {
    let code: Int32
    let message: String
    var errorDescription: String? { message }

    var requiresInspection: Bool {
        code == C989_UPLOAD_UNCERTAIN || code == C989_ACTIVATION_UNCERTAIN || code == C989_EVIDENCE_INCOMPLETE
    }

    static func check(_ code: Int32, message: [CChar]) throws {
        guard code != C989_OK else { return }

        throw Self(
            code: code,
            message: String(decoding: message.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self))
    }
}
