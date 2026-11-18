import Darwin
import Foundation
import XCTest

@testable import KeyboardLights

final class DeviceEvidenceTests: XCTestCase {
    private func temporary() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }

        return root
    }

    func testLoggingIsDisabledByDefaultAndTransfersAreStillValidated() throws {
        let root = try temporary()
        let output = root.appendingPathComponent("missing-parent/evidence")
        var io = EvidenceIO()
        io.write = { _, _, _ in
            XCTFail("Logging is disabled")
            return -1
        }

        io.sync = { _ in
            XCTFail("Logging is disabled")
            return -1
        }

        let evidence = try DeviceEvidence(directory: output, io: io)
        XCTAssertFalse(evidence.enabled)
        try evidence.save("image.bin", Data([1]))
        try evidence.json("result.json", ["result": "passed"])
        try evidence.syncDirectory()
        try evidence.journal("applied")
        let payload = Data(repeating: 0, count: 64)
        XCTAssertEqual(
            try evidence.transfer("IOHID", input: true, data: payload) {
                DeviceTransfer(data: payload, length: 64, success: true, domain: "test", code: 0)
            }, payload)
        XCTAssertThrowsError(
            try evidence.transfer("IOHID", input: true, data: payload) {
                DeviceTransfer(data: payload, length: 17, success: true, domain: "test", code: 0)
            })
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: root.path).isEmpty)
    }

    func testHexEncodingHandlesEveryByteAndDataSlices() {
        let bytes = Data((0..<256).map { UInt8($0) })

        // Compare with the previous formatter to pin the existing evidence format.
        let expected = (0..<256).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(bytes.hex, expected)
        XCTAssertEqual(Data().hex, "")
        XCTAssertEqual(bytes[127..<131].hex, "7f808182")
    }

    func testExclusiveDurableFilesHandleInterruptedAndShortWrites() throws {
        let root = try temporary()
        var calls = 0
        var syncs = 0
        var io = EvidenceIO()
        io.write = { fd, bytes, count in
            calls += 1
            if calls == 1 {
                errno = EINTR
                return -1
            }

            return Darwin.write(fd, bytes, min(count, 16))
        }

        io.sync = { fd in
            syncs += 1
            return fsync(fd)
        }

        let evidence = try DeviceEvidence(directory: root.appendingPathComponent("evidence"), enabled: true, io: io)
        let initialSyncs = syncs
        let bytes = Data((0..<256).map { UInt8($0) })
        try evidence.save("bytes.bin", bytes)
        XCTAssertEqual(calls, 17)
        XCTAssertEqual(syncs, initialSyncs + 1)
        let target = evidence.directory.appendingPathComponent("bytes.bin")
        XCTAssertEqual(try Data(contentsOf: target), bytes)
        XCTAssertThrowsError(try evidence.save("bytes.bin", Data([1])))
        let link = evidence.directory.appendingPathComponent("link.bin")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        XCTAssertThrowsError(try evidence.save("link.bin", Data([2])))
        XCTAssertEqual(try Data(contentsOf: target), bytes)
    }

    func testWriteSyncAndCloseFailuresAreNotReportedAsSuccess() throws {
        for scenario in 0..<3 {
            let root = try temporary()
            var enabled = false
            var io = EvidenceIO()
            io.write = { fd, bytes, count in
                if enabled && scenario == 0 {
                    errno = EIO
                    return -1
                }

                return Darwin.write(fd, bytes, count)
            }

            io.sync = { fd in enabled && scenario == 1 ? -1 : fsync(fd) }

            io.close = { fd in
                let result = Darwin.close(fd)
                return enabled && scenario == 2 ? -1 : result
            }

            let evidence = try DeviceEvidence(directory: root.appendingPathComponent("evidence"), enabled: true, io: io)
            enabled = true
            XCTAssertThrowsError(try evidence.save("failure.bin", Data([1, 2, 3])))
        }
    }

    func testExchangeRecordsFullBuffersLengthsErrorsAndTimestamps() throws {
        let root = try temporary()
        let evidence = try DeviceEvidence(directory: root.appendingPathComponent("evidence"), enabled: true)
        let payload = Data((0..<64).map { UInt8($0 * 3 + 1) })
        let zero = Data(repeating: 0, count: 64)
        let scenarios: [(String, Bool, Int?, Bool)] = [
            ("IOHID", true, 64, true), ("IOHID", true, 17, true), ("IOHID", true, 64, false),
            ("IOHID", false, nil, false), ("IOUSBHost", true, 7, true), ("IOUSBHost", true, 7, false)
        ]
        for (transport, input, length, success) in scenarios {
            let call = {
                DeviceTransfer(
                    data: payload, length: length, success: success, domain: "InjectedUSB", code: success ? 0 : 123)
            }

            if success && length == 64 {
                XCTAssertEqual(try evidence.transfer(transport, input: input, data: zero, call: call), payload)
            } else {
                XCTAssertThrowsError(
                    try evidence.transfer(transport, input: input, data: input ? zero : payload, call: call))
            }
        }

        let text = try String(contentsOf: evidence.directory.appendingPathComponent("exchanges.jsonl"), encoding: .utf8)
        let records = try text.split(separator: "\n").map {
            try XCTUnwrap(JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any])
        }

        XCTAssertEqual(records.count, 12)
        for index in scenarios.indices {
            let attempt = records[index * 2]
            let completion = records[index * 2 + 1]
            XCTAssertEqual(attempt["sequence"] as? Int, index + 1)
            XCTAssertEqual(attempt["sequence"] as? Int, completion["sequence"] as? Int)
            XCTAssertEqual(completion["buffer_hex"] as? String, payload.hex)
            XCTAssertGreaterThanOrEqual(
                (completion["call_end_monotonic_ns"] as? UInt64) ?? 0,
                (completion["call_start_monotonic_ns"] as? UInt64) ?? 1)
        }

        XCTAssertEqual(records[1]["received_hex"] as? String, payload.hex)
        XCTAssertEqual(records[3]["received_hex"] as? String, Data(payload.prefix(17)).hex)
        XCTAssertTrue(records[5]["received_hex"] is NSNull)
        XCTAssertTrue(records[7]["reported_length"] is NSNull)
        XCTAssertEqual(records[9]["received_hex"] as? String, Data(payload.prefix(7)).hex)
        XCTAssertTrue(records[11]["received_hex"] is NSNull)
        XCTAssertEqual(records[11]["status_code"] as? Int, 123)
        XCTAssertNotNil(records[8]["usb_request"])
    }

    func testExchangeLogFailurePermanentlyPreventsFurtherOSCalls() throws {
        for failCompletion in [false, true] {
            let root = try temporary()
            var fail = false
            var calls = 0
            var io = EvidenceIO()
            io.sync = { fd in fail ? -1 : fsync(fd) }

            let evidence = try DeviceEvidence(directory: root.appendingPathComponent("evidence"), enabled: true, io: io)
            let data = Data(repeating: 0, count: 64)
            fail = !failCompletion
            let call = { () -> DeviceTransfer in
                calls += 1
                fail = true
                return DeviceTransfer(data: data, length: 64, success: true, domain: "fake", code: 0)
            }

            XCTAssertThrowsError(try evidence.transfer("IOHID", input: true, data: data, call: call))
            fail = false
            XCTAssertThrowsError(try evidence.transfer("IOHID", input: true, data: data, call: call))
            XCTAssertEqual(calls, failCompletion ? 1 : 0)
        }
    }

    func testReportShapesAreValidatedBeforeCallingTransport() throws {
        let root = try temporary()
        let evidence = try DeviceEvidence(directory: root.appendingPathComponent("evidence"), enabled: true)
        var calls = 0
        let invalid: [(String, Bool, Data, KeyboardReport)] = [
            ("IOUSBHost", true, Data([0]), .feature),
            ("IOUSBHost", true, Data(repeating: 0, count: 64), .lockIndicators),
            ("IOUSBHost", false, Data([0]), .lockIndicators), ("IOHID", true, Data([0]), .lockIndicators)
        ]
        for (transport, input, data, report) in invalid {
            XCTAssertThrowsError(
                try evidence.transfer(transport, input: input, data: data, report: report) {
                    calls += 1
                    return DeviceTransfer(data: data, length: data.count, success: true, domain: "fake", code: 0)
                })
        }

        XCTAssertEqual(calls, 0)
    }

    func testProductionUSBRequestUsesOutputReportWithoutSetRequest() {
        let request = KeyboardReport.lockIndicators.request(input: true)
        XCTAssertEqual(request.bmRequestType, 0xA1)
        XCTAssertEqual(request.bRequest, 0x01)
        XCTAssertEqual(request.wValue, 0x0200)
        XCTAssertEqual(request.wIndex, 0)
        XCTAssertEqual(request.wLength, 1)
        for input in [false, true] {
            let feature = KeyboardReport.feature.request(input: input)
            XCTAssertEqual(feature.bmRequestType, input ? 0xA1 : 0x21)
            XCTAssertEqual(feature.bRequest, input ? 1 : 9)
            XCTAssertEqual(feature.wValue, 0x0300)
            XCTAssertEqual(feature.wLength, 64)
        }
    }

}
