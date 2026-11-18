import C989Native
import Darwin
import Foundation
import XCTest

@testable import KeyboardLights

// A protocol emulator, not an OS mock: the production Swift session and C writer
// must perform the real handshake, channel reads, checksum queries, and activation.
private final class EmulatedKeyboard: KeyboardTransport {
    let location: UInt32 = 0x123400
    var channels: [Data]
    var lockIndicators: UInt8 = 0x05
    var lockReads = 0
    var lockResult: DeviceTransfer?
    var calls = 0
    var failAt = 0
    var shortAt = 0
    var closed = false
    var sent: [(Bool, Data)] = []
    var corruptPostflight = false
    var cachedRead = false
    private var reply = Data(repeating: 0, count: 64)
    private var readChannel: Int?
    private var readOffset = 0
    private var writing: Int?
    private var uploaded = Data()
    private var activated = false

    init() {
        var lighting = Data(repeating: 0, count: 1152)
        for (index, tag) in [1, 2, 3, 4, 5, 6, 7, 128].enumerated() {
            lighting[index * 64] = UInt8(tag)
            lighting[index * 64 + 62] = 0xAA
            lighting[index * 64 + 63] = 0x55
        }

        lighting[0x440] = 1
        lighting[0x47E] = 0xAA
        lighting[0x47F] = 0x55
        channels = [
            Data(repeating: 0, count: 640), lighting, Data(repeating: 0x31, count: 3072),
            Data(repeating: 0x72, count: 64)
        ]
    }

    func close() { closed = true }

    func readLockIndicators() -> DeviceTransfer {
        calls += 1
        lockReads += 1
        if let lockResult { return lockResult }

        return DeviceTransfer(
            data: Data([lockIndicators]), length: calls == shortAt ? 0 : 1, success: calls != failAt, domain: "fake",
            code: calls == failAt ? -1 : 0)
    }

    func transfer(usb: Bool, input: Bool, data: Data) -> DeviceTransfer {
        calls += 1
        if !input { sent.append((usb, data)) }

        if calls == failAt {
            return DeviceTransfer(data: data, length: 64, success: false, domain: "injected", code: -1)
        }

        var output = data
        if input {
            if !usb, let channel = readChannel {
                if cachedRead && readOffset == 0 {
                    output = NativeKeyboardBackend.command(0x77)
                } else {
                    let offset = readOffset - (cachedRead ? 64 : 0)
                    let image = channels[channel]
                    output =
                        offset < image.count
                        ? image.subdata(in: offset..<(offset + 64)) : Data(repeating: 0xEE, count: 64)
                    if corruptPostflight && activated { output[0] ^= 1 }
                }

                readOffset += 64
            } else {
                output = reply
            }
        } else if !usb, let channel = writing, uploaded.count < [10, 18][channel] * 64 {
            uploaded.append(data)
        } else {
            reply = Data(repeating: 0, count: 64)
            reply[0] = 4
            reply[1] = data[1]
            reply[3] = 1
            if usb {
                if data[1] == 2 { setChecksum(0x5471_1688) }
            } else {
                switch data[1] {
                case 0x10, 0x12, 0x14, 0x16:
                    readChannel = Int((data[1] - 0x10) / 2)
                    readOffset = 0
                    writing = nil
                case 0x11, 0x13:
                    writing = data[1] == 0x11 ? 0 : 1
                    uploaded = Data()
                    readChannel = nil
                case 2:
                    let body = writing != nil ? uploaded : channels[readChannel ?? 1]
                    setChecksum(body.reduce(UInt32(0)) { $0 + UInt32($1) })
                    readChannel = nil
                case 0xF0:
                    if let channel = writing { channels[channel] = uploaded }

                    writing = nil
                    activated = true
                default: break
                }
            }
        }

        return DeviceTransfer(data: output, length: calls == shortAt ? 63 : 64, success: true, domain: "fake", code: 0)
    }

    private func setChecksum(_ value: UInt32) {
        for index in 0..<4 { reply[4 + index] = UInt8(truncatingIfNeeded: value >> (index * 8)) }
    }
}

final class NativeBackendTests: XCTestCase {
    private func temporary() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }

        return root
    }

    private func backend(
        _ keyboard: EmulatedKeyboard, root: URL, evidenceIO: EvidenceIO = EvidenceIO(),
        delays: ((UInt32) -> Void)? = nil, logging: Bool = true
    ) -> NativeKeyboardBackend {
        NativeKeyboardBackend(
            makeTransport: { required in
                XCTAssertTrue(required == 0 || required == keyboard.location)
                return keyboard
            }, makeEvidence: { try DeviceEvidence(directory: $0, enabled: logging, io: evidenceIO) },
            pause: delays ?? { _ in }, lockPath: root.appendingPathComponent("operation.lock").path)
    }

    private func candidate(_ keyboard: EmulatedKeyboard) -> Data {
        var image = keyboard.channels[1]
        image[0x441] = 0xFF
        return image
    }

    func testDisabledLoggingKeepsCapturesAndVerifiedWritesInMemory() throws {
        let root = try temporary()
        let keyboard = EmulatedKeyboard()
        let backend = backend(keyboard, root: root, logging: false)
        let capture = root.appendingPathComponent("missing-parent/capture")
        let captured = try backend.capture(channel: .assignments, output: capture)
        XCTAssertEqual(captured.data, keyboard.channels[0])
        XCTAssertFalse(FileManager.default.fileExists(atPath: capture.deletingLastPathComponent().path))

        let lighting = candidate(keyboard)
        let write = root.appendingPathComponent("missing-parent/write")
        try backend.writeLighting(
            baseline: keyboard.channels[1], location: captured.location, candidate: lighting, output: write)
        XCTAssertEqual(keyboard.channels[1], lighting)

        let snapshot = AssignmentSnapshot(location: keyboard.location, channels: [keyboard.channels[0]])
        var draft = KeyAssignmentDraft()
        try draft.set(KeyAction(kind: .disabled), for: 39)
        let assignments = try NativeAssignmentPolicy.candidate(baseline: snapshot.channels[0], draft: draft)
        try backend.writeAssignments(snapshot: snapshot, draft: draft, candidate: assignments, output: write)
        XCTAssertEqual(keyboard.channels[0], assignments)
        XCTAssertFalse(FileManager.default.fileExists(atPath: write.deletingLastPathComponent().path))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: root.path), ["operation.lock"])
    }

    func testCapturesOnlyRequestedChannelAndLocksWithoutConfigurationWrites() throws {
        for (cached, lightingOnly) in [(false, false), (true, false), (false, true), (true, true)] {
            let root = try temporary()
            let keyboard = EmulatedKeyboard()
            keyboard.cachedRead = cached
            let captured = try backend(keyboard, root: root).capture(
                channel: lightingOnly ? .lighting : .assignments, output: root.appendingPathComponent("capture"))
            let target = lightingOnly ? 1 : 0
            XCTAssertEqual(captured.data, keyboard.channels[target])
            XCTAssertEqual(captured.lockIndicators.rawValue, 0x05)
            XCTAssertTrue(captured.lockIndicators.numLock)
            XCTAssertFalse(captured.lockIndicators.capsLock)
            XCTAssertTrue(captured.lockIndicators.scrollLock)
            XCTAssertEqual(keyboard.lockReads, 1)
            let reads = keyboard.sent.filter { !$0.0 && [0x10, 0x12, 0x14, 0x16].contains($0.1[1]) }

            XCTAssertEqual(reads.map { $0.1[1] }, [lightingOnly ? 0x12 : 0x10])
            let lockFile = root.appendingPathComponent("capture/lock-indicators.bin")
            XCTAssertEqual(try Data(contentsOf: lockFile), Data([0x05]))
            let log = try String(contentsOf: root.appendingPathComponent("capture/exchanges.jsonl"), encoding: .utf8)
            let exchanges = try log.split(separator: "\n").map {
                try XCTUnwrap(JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any])
            }

            let attempt = try XCTUnwrap(
                exchanges.first { $0["report_type"] as? String == "output" && $0["event"] as? String == "attempt" })
            let request = try XCTUnwrap(attempt["usb_request"] as? [String: Int])
            XCTAssertEqual(request["bmRequestType"], 0xA1)
            XCTAssertEqual(request["bRequest"], 1)
            XCTAssertEqual(request["wValue"], 0x0200)
            XCTAssertEqual(request["wIndex"], 0)
            XCTAssertEqual(request["wLength"], 1)
            XCTAssertEqual(captured.location, keyboard.location)
            XCTAssertTrue(keyboard.closed)
            XCTAssertFalse(keyboard.sent.contains { [0x11, 0x13, 0xF0].contains($0.1[1]) })
            XCTAssertTrue(
                FileManager.default.fileExists(
                    atPath: root.appendingPathComponent(
                        lightingOnly ? "capture/capture.json" : "capture/state/manifest.json"
                    ).path))
        }
    }

    func testLockReadRejectsShortAndFailedTransfers() throws {
        for short in [false, true] {
            let probeRoot = try temporary()
            let probe = EmulatedKeyboard()
            _ = try backend(probe, root: probeRoot).capture(
                channel: .lighting, output: probeRoot.appendingPathComponent("capture"))
            let root = try temporary()
            let keyboard = EmulatedKeyboard()
            if short { keyboard.shortAt = probe.calls } else { keyboard.failAt = probe.calls }

            XCTAssertThrowsError(
                try backend(keyboard, root: root).capture(
                    channel: .lighting, output: root.appendingPathComponent("capture")))
            XCTAssertEqual(keyboard.lockReads, 1)
            XCTAssertTrue(keyboard.closed)
        }
    }

    func testInvalidLockResponsesStopBothWritersBeforeUpload() throws {
        let invalid: [(Data, Int?, Bool)] = [
            (Data([1]), 0, true), (Data([1]), nil, true), (Data([1]), -1, true), (Data([1]), 2, true),
            (Data(), 1, true), (Data([1, 2]), 1, true), (Data([1]), 1, false)
        ]
        for (assignment, logging) in [(false, false), (false, true), (true, false), (true, true)] {
            for (data, length, success) in invalid {
                let root = try temporary()
                let keyboard = EmulatedKeyboard()
                let original = keyboard.channels
                keyboard.lockResult = DeviceTransfer(
                    data: data, length: length, success: success, domain: "injected", code: 0)
                let writer = backend(keyboard, root: root, logging: logging)
                XCTAssertThrowsError(
                    try {
                        if assignment {
                            let snapshot = AssignmentSnapshot(location: keyboard.location, channels: [original[0]])
                            var draft = KeyAssignmentDraft()
                            try draft.set(KeyAction(kind: .keyboard, target: 4), for: 39)
                            let candidate = try NativeAssignmentPolicy.candidate(baseline: original[0], draft: draft)
                            try writer.writeAssignments(
                                snapshot: snapshot, draft: draft, candidate: candidate,
                                output: root.appendingPathComponent("write"))
                        } else {
                            try writer.writeLighting(
                                baseline: original[1], location: keyboard.location, candidate: candidate(keyboard),
                                output: root.appendingPathComponent("write"))
                        }
                    }()
                ) { error in
                    let native = error as? NativeDeviceError
                    XCTAssertEqual(native?.code, Int32(C989_STOPPED))
                    XCTAssertEqual(native?.requiresInspection, false)
                }

                XCTAssertEqual(keyboard.channels, original)
                XCTAssertEqual(keyboard.lockReads, 1)
                XCTAssertFalse(keyboard.sent.contains { !$0.0 && [0x11, 0x13, 0xF0].contains($0.1[1]) })
                XCTAssertTrue(keyboard.closed)
            }
        }
    }

    func testLockReadAcceptsSlicedBufferAndIsSampledBeforeUpload() throws {
        let root = try temporary()
        let keyboard = EmulatedKeyboard()
        keyboard.lockResult = DeviceTransfer(
            data: Data([0xFF, 0x82]).dropFirst(), length: 1, success: true, domain: "fake", code: 0)
        let locks = try backend(keyboard, root: root).writeLighting(
            baseline: keyboard.channels[1], location: keyboard.location, candidate: candidate(keyboard),
            output: root.appendingPathComponent("write"))
        XCTAssertEqual(locks.rawValue, 0x82)
        let log = try String(contentsOf: root.appendingPathComponent("write/exchanges.jsonl"), encoding: .utf8)
        let records = try log.split(separator: "\n").map {
            try XCTUnwrap(JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any])
        }

        let lockIndex = try XCTUnwrap(records.firstIndex { $0["report_type"] as? String == "output" })
        let uploadIndex = try XCTUnwrap(
            records.firstIndex { $0["phase"] as? String == "lighting-upload-and-single-f0" })
        XCTAssertLessThan(lockIndex, uploadIndex)
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("write/lock-indicators.bin")), Data([0x82]))
    }

    func testLightingUsesSharedStreamAndTimingAndVerifiesBeforeSuccess() throws {
        let root = try temporary()
        let keyboard = EmulatedKeyboard()
        let before = keyboard.channels[1]
        let after = candidate(keyboard)
        var delays: [UInt32] = []
        try backend(keyboard, root: root, delays: { delays.append($0) }).writeLighting(
            baseline: before, location: keyboard.location, candidate: after,
            output: root.appendingPathComponent("write"))
        XCTAssertEqual(keyboard.channels[1], after)
        XCTAssertFalse(keyboard.sent.contains { !$0.0 && [0x10, 0x11, 0x14, 0x16].contains($0.1[1]) })
        XCTAssertEqual(keyboard.sent.filter { !$0.0 && $0.1 == NativeKeyboardBackend.command(0xF0) }.count, 1)
        XCTAssertEqual(delays.filter { $0 == 105000 }.count, 1)
        XCTAssertTrue(delays.allSatisfy { $0 == 15000 || $0 == 105000 })
        let planned = try Data(contentsOf: root.appendingPathComponent("write/planned-write-set-reports.bin"))
        let reports = keyboard.sent.filter { !$0.0 }.map(\.1)
        let start = try XCTUnwrap(reports.firstIndex(of: NativeKeyboardBackend.command(0x13, blocks: 18)))
        XCTAssertEqual(reports[start..<(start + 21)].reduce(into: Data()) { $0.append($1) }, planned)
        XCTAssertTrue(keyboard.closed)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: root.appendingPathComponent("write/verified-result.json").path))
    }

    func testAssignmentsUseSharedPolicyAndPreserveOtherChannels() throws {
        let root = try temporary()
        let keyboard = EmulatedKeyboard()
        let original = keyboard.channels
        let snapshot = AssignmentSnapshot(location: keyboard.location, channels: [original[0]])
        var draft = KeyAssignmentDraft()
        try draft.set(KeyAction(kind: .keyboard, target: 4), for: 39)
        let after = try NativeAssignmentPolicy.candidate(baseline: original[0], draft: draft)
        var delays: [UInt32] = []
        try backend(keyboard, root: root, delays: { delays.append($0) }).writeAssignments(
            snapshot: snapshot, draft: draft, candidate: after, output: root.appendingPathComponent("write"))
        XCTAssertEqual(keyboard.channels[0], after)
        XCTAssertFalse(keyboard.sent.contains { !$0.0 && [0x12, 0x13, 0x14, 0x16].contains($0.1[1]) })
        XCTAssertEqual(Array(keyboard.channels.dropFirst()), Array(original.dropFirst()))
        XCTAssertEqual(delays.filter { $0 == 70000 }.count, 1)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: root.appendingPathComponent("write/state/manifest.json").path))
    }

    func testEveryTransportFailureStopsWithoutRetryAndClassifiesUncertainty() throws {
        for assignment in [false, true] {
            func perform(_ keyboard: EmulatedKeyboard, root: URL) throws {
                let writer = backend(keyboard, root: root)
                if assignment {
                    let snapshot = AssignmentSnapshot(location: keyboard.location, channels: [keyboard.channels[0]])
                    var draft = KeyAssignmentDraft()
                    try draft.set(KeyAction(kind: .keyboard, target: 4), for: 39)
                    let prepared = try NativeAssignmentPolicy.candidate(baseline: snapshot.channels[0], draft: draft)
                    try writer.writeAssignments(
                        snapshot: snapshot, draft: draft, candidate: prepared,
                        output: root.appendingPathComponent("write"))
                } else {
                    try writer.writeLighting(
                        baseline: keyboard.channels[1], location: keyboard.location, candidate: candidate(keyboard),
                        output: root.appendingPathComponent("write"))
                }
            }

            let probeRoot = try temporary()
            let probe = EmulatedKeyboard()
            try perform(probe, root: probeRoot)
            for point in 1...probe.calls {
                let root = try temporary()
                let keyboard = EmulatedKeyboard()
                keyboard.failAt = point
                XCTAssertThrowsError(try perform(keyboard, root: root)) { error in
                    let native = error as? NativeDeviceError
                    XCTAssertNotNil(native)
                    let activation = keyboard.sent.contains { !$0.0 && $0.1[1] == 0xF0 }

                    let upload = keyboard.sent.contains { !$0.0 && $0.1[1] == (assignment ? 0x11 : 0x13) }

                    XCTAssertEqual(
                        native?.code,
                        Int32(activation ? C989_ACTIVATION_UNCERTAIN : upload ? C989_UPLOAD_UNCERTAIN : C989_STOPPED))
                }

                XCTAssertEqual(keyboard.calls, point)
                XCTAssertTrue(keyboard.closed)
                XCTAssertLessThanOrEqual(keyboard.sent.filter { !$0.0 && $0.1[1] == 0xF0 }.count, 1)
            }
        }
    }

    func testShortInputAndPostflightMismatchNeverReportSuccess() throws {
        for corrupt in [false, true] {
            let root = try temporary()
            let keyboard = EmulatedKeyboard()
            keyboard.shortAt = corrupt ? 0 : 2
            keyboard.corruptPostflight = corrupt
            XCTAssertThrowsError(
                try backend(keyboard, root: root).writeLighting(
                    baseline: keyboard.channels[1], location: keyboard.location, candidate: candidate(keyboard),
                    output: root.appendingPathComponent("write"))
            ) {
                XCTAssertEqual(
                    ($0 as? NativeDeviceError)?.code, Int32(corrupt ? C989_ACTIVATION_UNCERTAIN : C989_STOPPED))
            }

            XCTAssertFalse(
                FileManager.default.fileExists(atPath: root.appendingPathComponent("write/verified-result.json").path))
        }
    }

    func testEvidenceFailureAfterVerificationBlocksFurtherWrites() throws {
        let root = try temporary()
        let keyboard = EmulatedKeyboard()
        var io = EvidenceIO()
        io.write = { fd, bytes, count in
            let data = Data(bytes: bytes, count: count)
            if String(decoding: data, as: UTF8.self).contains("write-activation-lighting-readback-passed") {
                errno = EIO
                return -1
            }

            return Darwin.write(fd, bytes, count)
        }

        XCTAssertThrowsError(
            try backend(keyboard, root: root, evidenceIO: io).writeLighting(
                baseline: keyboard.channels[1], location: keyboard.location, candidate: candidate(keyboard),
                output: root.appendingPathComponent("write"))
        ) {
            XCTAssertEqual(($0 as? NativeDeviceError)?.code, Int32(C989_EVIDENCE_INCOMPLETE))
            XCTAssertTrue(($0 as? NativeDeviceError)?.requiresInspection == true)
        }
    }

    func testLogParentCreationFailureStopsBeforeDeviceAccessWithoutUncertainty() throws {
        let root = try temporary()
        let parent = root.appendingPathComponent("not-a-directory")
        try Data().write(to: parent)
        let keyboard = EmulatedKeyboard()
        var opens = 0
        let backend = NativeKeyboardBackend(
            makeTransport: { _ in
                opens += 1
                return keyboard
            }, makeEvidence: { try DeviceEvidence(directory: $0, enabled: true) }, pause: { _ in },
            lockPath: root.appendingPathComponent("operation.lock").path)
        XCTAssertThrowsError(
            try backend.writeLighting(
                baseline: keyboard.channels[1], location: keyboard.location, candidate: candidate(keyboard),
                output: parent.appendingPathComponent("write"))
        ) {
            XCTAssertEqual(($0 as? NativeDeviceError)?.code, Int32(C989_STOPPED))
            XCTAssertEqual(($0 as? NativeDeviceError)?.requiresInspection, false)
        }

        XCTAssertEqual(opens, 0)
        XCTAssertEqual(keyboard.calls, 0)
    }

    func testInvalidReviewAndOccupiedOperationLockNeverOpenTransport() throws {
        let root = try temporary()
        var opens = 0
        let lockPath = root.appendingPathComponent("operation.lock").path
        let backend = NativeKeyboardBackend(
            makeTransport: { _ in
                opens += 1
                return EmulatedKeyboard()
            }, pause: { _ in }, lockPath: lockPath)
        let keyboard = EmulatedKeyboard()
        XCTAssertThrowsError(
            try backend.writeLighting(
                baseline: keyboard.channels[1], location: keyboard.location, candidate: keyboard.channels[1],
                output: root.appendingPathComponent("invalid")))
        let fd = open(lockPath, O_RDWR | O_CREAT, 0o600)
        defer { Darwin.close(fd) }

        XCTAssertEqual(flock(fd, LOCK_EX | LOCK_NB), 0)
        XCTAssertThrowsError(try backend.capture(channel: .lighting, output: root.appendingPathComponent("busy"))) {
            XCTAssertEqual(($0 as? NativeDeviceError)?.code, Int32(C989_BUSY))
        }

        XCTAssertEqual(opens, 0)
    }

    func testMismatchedReviewAndEvidenceSetupFailureNeverOpenHardware() throws {
        let root = try temporary()
        let keyboard = EmulatedKeyboard()
        var opens = 0
        let writer = NativeKeyboardBackend(
            makeTransport: { _ in
                opens += 1
                return keyboard
            }, makeEvidence: { try DeviceEvidence(directory: $0, enabled: true) }, pause: { _ in },
            lockPath: root.appendingPathComponent("operation.lock").path)
        var draft = KeyAssignmentDraft()
        try draft.set(KeyAction(kind: .keyboard, target: 4), for: 39)
        XCTAssertThrowsError(
            try writer.writeAssignments(
                snapshot: AssignmentSnapshot(location: keyboard.location, channels: [keyboard.channels[0]]),
                draft: draft, candidate: Data(repeating: 0, count: 640),
                output: root.appendingPathComponent("bad-review")))
        XCTAssertThrowsError(try writer.capture(channel: .lighting, output: root))
        XCTAssertEqual(opens, 0)
        let mismatch = NativeKeyboardBackend(
            makeTransport: { _ in keyboard }, pause: { _ in },
            lockPath: root.appendingPathComponent("operation.lock").path)
        XCTAssertThrowsError(
            try mismatch.writeLighting(
                baseline: keyboard.channels[1], location: 1, candidate: candidate(keyboard),
                output: root.appendingPathComponent("wrong-device")))
        XCTAssertEqual(keyboard.calls, 0)
        XCTAssertTrue(keyboard.closed)
    }

}
