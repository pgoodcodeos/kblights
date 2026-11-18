import C989Native
import CryptoKit
import Darwin
import Foundation

enum ConfigurationChannel: Int {
    case assignments = 0
    case lighting = 1

    var blocks: Int { self == .assignments ? 10 : 18 }

    var readCommand: UInt8 { self == .assignments ? 0x10 : 0x12 }

    var writeCommand: UInt8 { self == .assignments ? 0x11 : 0x13 }
}

// This backend owns immutable in-memory inputs throughout an operation. Evidence
// files are output only, never an IPC channel or a source of reviewed bytes.
final class NativeKeyboardBackend {
    private static let operationMutex = NSLock()
    private let makeTransport: (UInt32) throws -> any KeyboardTransport
    private let makeEvidence: (URL) throws -> DeviceEvidence
    private let pause: (UInt32) -> Void
    private let lockPath: String

    init(
        makeTransport: @escaping (UInt32) throws -> any KeyboardTransport = {
            try MacKeyboardTransport(requiredLocation: $0)
        },
        makeEvidence: @escaping (URL) throws -> DeviceEvidence = {
            try DeviceEvidence(directory: $0, enabled: DeviceEvidence.loggingEnabled)
        }, pause: @escaping (UInt32) -> Void = NativeKeyboardBackend.sleep,
        lockPath: String = "/private/tmp/c989-baseline-trial-\(getuid()).lock"
    ) {
        self.makeTransport = makeTransport
        self.makeEvidence = makeEvidence
        self.pause = pause
        self.lockPath = lockPath
    }

    static func sleep(_ microseconds: UInt32) {
        var remaining = timespec(tv_sec: Int(microseconds / 1_000_000), tv_nsec: Int(microseconds % 1_000_000) * 1000)
        while true {
            var interrupted = timespec()
            if nanosleep(&remaining, &interrupted) == 0 || errno != EINTR { break }

            remaining = interrupted
        }
    }

    private func operation<T>(output: URL, body: (DeviceEvidence) throws -> T) throws -> T {
        guard Self.operationMutex.try() else {
            throw NativeDeviceError(code: Int32(C989_BUSY), message: "Keyboard is busy.")
        }

        defer { Self.operationMutex.unlock() }

        let fd = open(lockPath, O_RDWR | O_CREAT | O_NOFOLLOW, 0o600)
        guard fd >= 0 else {
            throw NativeDeviceError(code: Int32(C989_BUSY), message: "Keyboard operation lock unavailable.")
        }

        defer { Darwin.close(fd) }

        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            throw NativeDeviceError(code: Int32(C989_BUSY), message: "Another keyboard operation holds the lock.")
        }

        let evidence: DeviceEvidence
        do { evidence = try makeEvidence(output) } catch {
            // No transport has been created yet. A logging setup failure cannot
            // mean an upload reached the keyboard, even if it is a Cocoa error.
            throw NativeDeviceError(
                code: Int32(C989_STOPPED),
                message: "Could not prepare diagnostic logging: \(error.localizedDescription)")
        }

        return try body(evidence)
    }

    static func command(_ code: UInt8, blocks: Int = 0) -> Data {
        var data = Data(repeating: 0, count: 64)
        data[0] = 4
        data[1] = code
        data[8] = UInt8(blocks)
        return data
    }

    static func digest(_ data: Data) -> String { Data(SHA256.hash(data: data)).hex }

    private func packageAssignments(_ data: Data, location: UInt32, evidence: DeviceEvidence) throws {
        guard evidence.enabled else { return }

        let directory = evidence.directory.appendingPathComponent("state")
        guard mkdir(directory.path, 0o700) == 0 else { throw DeviceEvidence.failure("create state directory") }

        try evidence.save("state/channel-0.bin", data)
        try evidence.json(
            "state/manifest.json",
            [
                "format": "c989-native-assignment-state-v2", "vendor_id": "0C45", "product_id": "8006",
                "revision": "0138", "location": String(format: "%08X", location),
                "channels": [
                    [
                        "channel": 0, "blocks": ConfigurationChannel.assignments.blocks, "file": "channel-0.bin",
                        "sha256": Self.digest(data)
                    ]
                ]
            ])
        try evidence.syncDirectory(directory)
    }

    func capture(channel: ConfigurationChannel, output: URL) throws -> (
        data: Data, location: UInt32, lockIndicators: LockIndicators
    ) {
        try operation(output: output) { evidence in
            let transport = try makeTransport(0)
            defer { transport.close() }

            guard transport.location != 0 else { throw DeviceEvidence.failure("identify keyboard location") }

            let session = KeyboardSession(transport: transport, evidence: evidence, pause: pause)
            let image = try session.channel(channel, expected: nil, label: "capture")
            if channel == .lighting { try Self.validateLighting(image) }

            let lockIndicators = try session.readLockIndicators()
            if evidence.enabled {
                if channel == .lighting {
                    try evidence.save("channel-1.bin", image)
                    try evidence.json(
                        "capture.json",
                        [
                            "format": "c989-native-lighting-capture-v1", "vendor_id": "0C45", "product_id": "8006",
                            "revision": "0138", "location": String(format: "%08X", transport.location),
                            "sha256": Self.digest(image), "checksum_verified": true
                        ])
                } else {
                    try packageAssignments(image, location: transport.location, evidence: evidence)
                }

                try evidence.syncDirectory()
            }

            return (image, transport.location, lockIndicators)
        }
    }

    static func validateLighting(_ data: Data) throws {
        let valid = data.withUnsafeBytes { c989_lighting_validate($0.bindMemory(to: UInt8.self).baseAddress, $0.count) }

        guard valid != 0 else { throw DeviceEvidence.failure("validate captured lighting state") }
    }

    @discardableResult func writeLighting(baseline: Data, location: UInt32, candidate: Data, output: URL) throws
        -> LockIndicators
    {
        try NativeLightingPolicy.validate(baseline: baseline, candidate: candidate)
        return try write(
            baseline: Data(baseline), location: location, candidate: Data(candidate), channel: .lighting, output: output
        )
    }

    @discardableResult func writeAssignments(
        snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL
    ) throws -> LockIndicators {
        let snapshot = try snapshot.validated()
        let prepared = try NativeAssignmentPolicy.candidate(baseline: snapshot.channels[0], draft: draft)
        guard prepared == candidate else {
            throw NativeDeviceError(code: Int32(C989_STOPPED), message: "The candidate no longer matches its review.")
        }

        return try write(
            baseline: snapshot.channels[0], location: snapshot.location, candidate: prepared, channel: .assignments,
            output: output)
    }

    private func write(baseline: Data, location: UInt32, candidate: Data, channel: ConfigurationChannel, output: URL)
        throws -> LockIndicators
    {
        guard location != 0 else { throw DeviceEvidence.failure("validate keyboard location") }

        return try operation(output: output) { evidence in
            let candidateDigest = evidence.enabled ? Self.digest(candidate) : nil
            var result = C989WriteResult()
            result.last_attempted_block = -1
            var verified = false
            do {
                let assignment = channel == .assignments
                if let candidateDigest {
                    let target = channel.rawValue
                    try evidence.save("expected-channel-\(target).bin", baseline)

                    try evidence.save("candidate-channel-\(target).bin", candidate)
                    var stream = Self.command(channel.writeCommand, blocks: channel.blocks)
                    stream.append(candidate)
                    stream.append(Self.command(2))
                    stream.append(Self.command(0xF0))
                    try evidence.save("planned-write-set-reports.bin", stream)
                    if !assignment {
                        try evidence.json(
                            "plan.json",
                            [
                                "format": "c989-native-lighting-write-plan-v1",
                                "location": String(format: "%08X", location),
                                "expected_lighting_sha256": Self.digest(baseline), "candidate_sha256": candidateDigest,
                                "automatic_write_retries": 0
                            ])
                    }

                    try evidence.syncDirectory()
                }

                let transport = try makeTransport(location)
                defer { transport.close() }

                guard transport.location == location else { throw DeviceEvidence.failure("verify keyboard location") }

                let session = KeyboardSession(transport: transport, evidence: evidence, pause: pause)
                try evidence.journal("identity-verified")
                _ = try session.channel(channel, expected: baseline, label: "before")

                // Read ancillary state before upload; a failed lock read must never disguise a verified write.
                let lockIndicators = try session.readLockIndicators()
                evidence.phase = "write-session-handshake"
                try session.unlock()
                pause(15000)
                evidence.phase = assignment ? "assignment-upload-and-single-f0" : "lighting-upload-and-single-f0"
                try session.upload(candidate, assignment: assignment, result: &result)
                _ = try session.channel(channel, expected: candidate, label: "after")

                verified = true
                if let candidateDigest {
                    if assignment { try packageAssignments(candidate, location: location, evidence: evidence) }

                    try evidence.journal(
                        assignment
                            ? "write-activation-assignment-readback-passed"
                            : "write-activation-lighting-readback-passed")
                    var summary: [String: Any] = [
                        "format": assignment
                            ? "c989-native-assignment-write-result-v1" : "c989-native-lighting-write-result-v1",
                        "result": "passed", "candidate_sha256": candidateDigest,
                        "device_checksum": result.device_checksum, "location": String(format: "%08X", location),
                        "activation_attempted": result.activation_attempted,
                        "activation_host_completed": result.activation_host_completed, "automatic_write_retries": 0
                    ]
                    summary[assignment ? "assignment_preflight" : "lighting_preflight"] = "matched"
                    summary[assignment ? "assignment_postflight" : "lighting_postflight"] = "matched"
                    try evidence.json("verified-result.json", summary)
                    try evidence.syncDirectory()
                }

                return lockIndicators
            } catch {
                let code: Int32
                let event: String
                let message: String
                if verified {
                    code = Int32(C989_EVIDENCE_INCOMPLETE)
                    event = "STOP-verified-evidence-incomplete-no-retry"
                    message =
                        "Changes were applied and verified, but saving evidence failed. Further writes are blocked; inspect the saved evidence."
                } else if result.activation_attempted != 0 {
                    code = Int32(C989_ACTIVATION_UNCERTAIN)
                    event = "STOP-activation-or-postflight-ambiguous-no-retry"
                    message =
                        "Activation or verification did not complete. Do not retry, restore, reset, or power-cycle automatically."
                } else if result.stage >= 1 {
                    code = Int32(C989_UPLOAD_UNCERTAIN)
                    event = "STOP-inspect-upload-journal-no-retry"
                    message =
                        "The upload may be incomplete. Further writes are blocked; do not retry or restore automatically."
                } else {
                    code = Int32(C989_STOPPED)
                    event = "STOP-inspect-upload-journal-no-retry"
                    message =
                        "The write stopped before upload. Read the keyboard again before preparing another review."
                }

                try? evidence.journal(event)
                throw NativeDeviceError(code: code, message: message)
            }
        }
    }
}

private final class KeyboardSession {
    let transport: any KeyboardTransport
    let evidence: DeviceEvidence
    private let pause: (UInt32) -> Void
    private var callbackError: Error?

    init(transport: any KeyboardTransport, evidence: DeviceEvidence, pause: @escaping (UInt32) -> Void) {
        self.transport = transport
        self.evidence = evidence
        self.pause = pause
    }

    func readLockIndicators() throws -> LockIndicators {
        evidence.phase = "lock-indicator-readback"
        let data = try evidence.transfer("IOUSBHost", input: true, data: Data([0]), report: .lockIndicators) {
            transport.readLockIndicators()
        }

        pause(15000)
        try evidence.save("lock-indicators.bin", data)
        return LockIndicators(rawValue: data[data.startIndex])
    }

    func transfer(usb: Bool, input: Bool, data: Data = Data(repeating: 0, count: 64)) throws -> Data {
        guard data.count == 64 else { throw DeviceEvidence.failure("validate report size") }

        return try evidence.transfer(usb ? "IOUSBHost" : "IOHID", input: input, data: data) {
            transport.transfer(usb: usb, input: input, data: data)
        }
    }

    private func checksum(usb: Bool) throws -> UInt32 {
        for attempt in 1...5 {
            _ = try transfer(usb: usb, input: false, data: NativeKeyboardBackend.command(2))
            pause(15000)
            let reply = try transfer(usb: usb, input: true)
            if reply[0] == 4 && reply[1] == 2 && reply[3] == 1 {
                return UInt32(reply[4]) | UInt32(reply[5]) << 8 | UInt32(reply[6]) << 16 | UInt32(reply[7]) << 24
            }

            if attempt < 5 { pause(15000) }
        }

        throw DeviceEvidence.failure("verify read checksum response")
    }

    func unlock() throws {
        var acknowledged = false
        for attempt in 1...5 {
            _ = try transfer(usb: true, input: false, data: NativeKeyboardBackend.command(0xAB))
            pause(15000)
            let reply = try transfer(usb: true, input: true)
            if reply[0] == 4 && reply[1] == 0xAB && reply[3] == 1 {
                acknowledged = true
                break
            }

            if attempt < 5 { pause(15000) }
        }

        guard acknowledged else { throw DeviceEvidence.failure("establish read session") }

        pause(15000)
        _ = try transfer(usb: true, input: false, data: Data(repeating: 0, count: 64))
        pause(15000)
        guard try checksum(usb: true) == 0x5471_1688 else {
            throw DeviceEvidence.failure("verify read session challenge")
        }
    }

    func channel(_ channel: ConfigurationChannel, expected: Data?, label: String) throws -> Data {
        evidence.phase = "\(label)-channel-\(channel.rawValue)"
        try unlock()
        pause(15000)
        _ = try transfer(
            usb: false, input: false, data: NativeKeyboardBackend.command(channel.readCommand, blocks: channel.blocks))
        pause(15000)
        let size = channel.blocks * 64
        var reports = Data()
        reports.reserveCapacity(size + 64)
        for _ in 0..<(channel.blocks + (expected == nil ? 1 : 0)) {
            reports.append(try transfer(usb: false, input: true))
            pause(15000)
        }

        if let expected, reports != expected {
            reports.append(try transfer(usb: false, input: true))
            pause(15000)
        }

        let file = "\(label)-channel-\(channel.rawValue)-reports.bin"
        try evidence.save(file, reports)
        let sum = try checksum(usb: false)
        pause(15000)
        let offset: Int32 = reports.withUnsafeBytes { buffer in
            if let expected {
                return expected.withUnsafeBytes {
                    c989_read_window(
                        buffer.bindMemory(to: UInt8.self).baseAddress, buffer.count, size,
                        $0.bindMemory(to: UInt8.self).baseAddress, sum)
                }
            }

            return c989_read_window(buffer.bindMemory(to: UInt8.self).baseAddress, buffer.count, size, nil, sum)
        }

        if evidence.enabled {
            try evidence.journal(String(format: "%@-checksum-%08X-%@", file, sum, offset < 0 ? "MISMATCH" : "matched"))
        }

        guard offset >= 0 else { throw DeviceEvidence.failure("match checksum-verified channel \(channel.rawValue)") }

        return expected ?? reports.subdata(in: Int(offset)..<(Int(offset) + size))
    }

    // C callbacks cannot throw. Latch the first Swift failure and return the exact
    // failure sentinel expected by the shared transaction, which stops immediately.
    private func callback(_ body: () throws -> Int32) -> Int32 {
        guard callbackError == nil else { return -1 }

        do { return try body() } catch {
            callbackError = error
            return -1
        }
    }

    func upload(_ data: Data, assignment: Bool, result: inout C989WriteResult) throws {
        let context = Unmanaged.passUnretained(self).toOpaque()
        var io = C989WriteIO(
            context: context,
            send: { context, bytes, size in
                let session = Unmanaged<KeyboardSession>.fromOpaque(context!).takeUnretainedValue()

                // send/journal use zero for failure, receive uses a negative byte count.
                let value = session.callback {
                    _ = try session.transfer(usb: false, input: false, data: Data(bytes: bytes!, count: size))
                    return 1
                }

                return value < 0 ? 0 : value
            },
            receive: { context, bytes, size in
                let session = Unmanaged<KeyboardSession>.fromOpaque(context!).takeUnretainedValue()
                return session.callback {
                    guard size == 64 else { throw DeviceEvidence.failure("validate receive size") }

                    let data = try session.transfer(usb: false, input: true)
                    data.copyBytes(to: bytes!, count: size)
                    return Int32(size)
                }
            },
            pause: { context, delay in
                Unmanaged<KeyboardSession>.fromOpaque(context!).takeUnretainedValue().pause(delay)
            },
            journal: { context, event, block in
                let session = Unmanaged<KeyboardSession>.fromOpaque(context!).takeUnretainedValue()
                let value = session.callback {
                    try session.evidence.journal(String(cString: event!), block: block)
                    return 1
                }

                return value < 0 ? 0 : value
            })

        // The C context is deliberately unretained. Keep its Swift owner alive
        // through every callback, including when this method is inlined/optimized.
        let success = withExtendedLifetime(self) {
            data.withUnsafeBytes {
                assignment
                    ? c989_assignments_upload(&io, $0.bindMemory(to: UInt8.self).baseAddress, $0.count, &result)
                    : c989_lighting_upload(&io, $0.bindMemory(to: UInt8.self).baseAddress, $0.count, &result)
            }
        }

        guard success != 0 else { throw callbackError ?? DeviceEvidence.failure("complete shared upload transaction") }
    }
}
