import C989Native
import Darwin
import Foundation

// Injectable system calls keep short writes, EINTR, and persistence failures testable.
struct EvidenceIO {
    var write: (Int32, UnsafeRawPointer, Int) -> Int = { Darwin.write($0, $1, $2) }

    var sync: (Int32) -> Int32 = { fsync($0) }

    var close: (Int32) -> Int32 = { Darwin.close($0) }
}

final class DeviceEvidence {
    static let loggingPreference = "diagnosticLoggingEnabled"
    static var loggingEnabled: Bool { UserDefaults.standard.bool(forKey: loggingPreference) }

    let directory: URL
    let enabled: Bool
    private let io: EvidenceIO
    private var journalFD: Int32 = -1
    private var exchangeFD: Int32 = -1
    private var exchangeFailed = false
    private var sequence: UInt64 = 0
    var phase = "initialization"

    init(directory: URL, enabled: Bool = false, io: EvidenceIO = EvidenceIO()) throws {
        self.directory = directory
        self.io = io
        self.enabled = enabled
        guard enabled else { return }

        try FileManager.default.createDirectory(
            at: directory.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard mkdir(directory.path, 0o700) == 0 else { throw Self.failure("create evidence directory") }

        do {
            journalFD = try create("journal.txt")
            exchangeFD = try create("exchanges.jsonl")
            try syncDirectory(directory)
            try syncDirectory(directory.deletingLastPathComponent())
        } catch {
            if journalFD >= 0 {
                _ = io.close(journalFD)
                journalFD = -1
            }

            if exchangeFD >= 0 {
                _ = io.close(exchangeFD)
                exchangeFD = -1
            }

            throw error
        }
    }

    deinit {
        if journalFD >= 0 { _ = io.close(journalFD) }

        if exchangeFD >= 0 { _ = io.close(exchangeFD) }
    }

    static func failure(_ action: String) -> NativeDeviceError {
        NativeDeviceError(code: Int32(C989_STOPPED), message: "Could not \(action).")
    }

    private func create(_ name: String) throws -> Int32 {
        let fd = open(directory.appendingPathComponent(name).path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw Self.failure("create \(name)") }

        return fd
    }

    private func append(_ data: Data, to fd: Int32) throws {
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = io.write(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                if count < 0 && errno == EINTR { continue }

                guard count > 0, count <= bytes.count - offset else { throw Self.failure("write evidence") }

                offset += count
            }
        }

        guard io.sync(fd) == 0 else { throw Self.failure("sync evidence") }
    }

    func save(_ name: String, _ data: Data) throws {
        guard enabled else { return }

        let fd = try create(name)
        do { try append(data, to: fd) } catch {
            _ = io.close(fd)
            throw error
        }

        guard io.close(fd) == 0 else { throw Self.failure("close \(name)") }
    }

    func json(_ name: String, _ value: [String: Any]) throws {
        guard enabled else { return }

        try save(name, JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]))
    }

    func syncDirectory(_ url: URL? = nil) throws {
        guard enabled else { return }

        let fd = open((url ?? directory).path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard fd >= 0 else { throw Self.failure("open evidence directory") }

        let result = io.sync(fd)
        let closed = io.close(fd)
        guard result == 0, closed == 0 else { throw Self.failure("sync evidence directory") }
    }

    func journal(_ event: String, block: Int32 = -1) throws {
        guard enabled else { return }

        try append(Data("\(Int64(time(nil))) \(event) block=\(block)\n".utf8), to: journalFD)
    }

    private func record(
        _ event: String, sequence: UInt64, transport: String, input: Bool, data: Data, report: KeyboardReport
    ) -> [String: Any] {
        [
            "format": "c989-exchange-v1", "event": event, "sequence": sequence, "phase": phase, "transport": transport,
            "direction": input ? "in" : "out", "report_type": report.type, "report_id": 0,
            "requested_length": report.length, "wall_time_ns": Self.nanoseconds(CLOCK_REALTIME),
            "monotonic_ns": Self.nanoseconds(CLOCK_MONOTONIC), "buffer_length": data.count, "buffer_hex": data.hex
        ]
    }

    private func exchange(_ record: [String: Any]) throws {
        guard !exchangeFailed else { throw Self.failure("continue after exchange log failure") }

        do {
            var line = try JSONSerialization.data(withJSONObject: record, options: .sortedKeys)
            line.append(10)
            try append(line, to: exchangeFD)
        } catch {
            exchangeFailed = true
            throw error
        }
    }

    // When logging is enabled, record the attempt before the OS call and stop
    // subsequent transfers if a completion record cannot be saved.
    func transfer(
        _ transport: String, input: Bool, data: Data, report: KeyboardReport = .feature, call: () -> DeviceTransfer
    ) throws -> Data {
        guard data.count == report.length, report != .lockIndicators || (input && transport == "IOUSBHost") else {
            throw Self.failure("validate report type, direction, and size")
        }

        guard enabled else { return try validated(call(), transport: transport, input: input, report: report) }

        sequence += 1
        var attempt = record(
            "attempt", sequence: sequence, transport: transport, input: input, data: data, report: report)
        attempt["buffer_meaning"] = input ? "initialized-input-buffer-not-received-data" : "full-submitted-payload"
        if transport == "IOUSBHost" {
            let request = report.request(input: input)
            attempt["usb_request"] = [
                "bmRequestType": Int(request.bmRequestType), "bRequest": Int(request.bRequest),
                "wValue": Int(request.wValue), "wIndex": Int(request.wIndex), "wLength": Int(request.wLength),
                "timeout_seconds": 1
            ]
        }

        try exchange(attempt)
        let start = Self.nanoseconds(CLOCK_MONOTONIC)
        let result = call()
        let end = Self.nanoseconds(CLOCK_MONOTONIC)
        var completion = record(
            "completion", sequence: sequence, transport: transport, input: input, data: result.data, report: report)
        completion["call_start_monotonic_ns"] = start
        completion["call_end_monotonic_ns"] = end
        completion["call_duration_ns"] = end - start
        completion["reported_length"] = result.length.map { $0 as Any } ?? NSNull()
        completion["api_success"] = result.success
        completion["status_domain"] = result.domain
        completion["status_code"] = result.code
        completion["status_message"] = result.message
        completion["buffer_meaning"] = input ? "full-post-call-buffer" : "full-submitted-payload"
        if input, result.success, let length = result.length, length >= 0, length <= result.data.count {
            completion["received_length_valid"] = true
            completion["received_hex"] =
                length == result.data.count ? completion["buffer_hex"] : Data(result.data.prefix(length)).hex
        } else {
            completion["received_length_valid"] = false
            completion["received_hex"] = NSNull()
        }

        try exchange(completion)
        return try validated(result, transport: transport, input: input, report: report)
    }

    private func validated(_ result: DeviceTransfer, transport: String, input: Bool, report: KeyboardReport) throws
        -> Data
    {
        guard result.success, (!input && transport == "IOHID") || result.length == report.length,
            result.data.count == report.length
        else { throw Self.failure("complete a \(report.length)-byte \(transport) transfer") }

        return result.data
    }

    static func nanoseconds(_ clock: clockid_t) -> UInt64 {
        var value = timespec()
        clock_gettime(clock, &value)
        return UInt64(value.tv_sec) * 1_000_000_000 + UInt64(value.tv_nsec)
    }
}

private let hexDigits = Array("0123456789abcdef".utf8)

extension Data {
    var hex: String {
        var encoded: [UInt8] = []
        encoded.reserveCapacity(count * 2)
        for byte in self {
            encoded.append(hexDigits[Int(byte >> 4)])
            encoded.append(hexDigits[Int(byte & 0x0F)])
        }

        return String(decoding: encoded, as: UTF8.self)
    }
}
