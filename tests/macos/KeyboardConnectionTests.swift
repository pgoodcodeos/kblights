import Foundation
import XCTest

@testable import KeyboardLights

final class KeyboardConnectionTests: XCTestCase {
    @MainActor func testMissingKeyboardStopsBeforePermissionAndDeviceOperations() async {
        let connection = KeyboardConnection(detect: { false })
        var operations = 0
        await connection.check { operations += 1 }

        XCTAssertFalse(connection.isConnected)
        XCTAssertEqual(connection.message, "Keyboard not connected")
        XCTAssertEqual(operations, 0)
    }

    @MainActor func testDetectionFailureDoesNotStartDeviceOperations() async {
        let connection = KeyboardConnection(detect: { throw WorkflowError.message("Unsupported keyboard") })
        var operations = 0
        await connection.check { operations += 1 }

        XCTAssertEqual(connection.state, .unavailable("Unsupported keyboard"))
        XCTAssertEqual(operations, 0)
    }

    @MainActor func testCancelledStartupDoesNotBeginDeviceOperations() async {
        let connection = KeyboardConnection(detect: { true })
        var operations = 0
        let startup = Task { await connection.check { operations += 1 } }

        startup.cancel()
        await startup.value
        XCTAssertEqual(operations, 0)
        XCTAssertEqual(connection.state, .unknown)
    }

    @MainActor func testCancellationDuringDetectionPreventsReadsAndAllowsLaterRefresh() async throws {
        let probe = BlockingDetection()
        defer { probe.release.signal() }

        let connection = KeyboardConnection(detect: { try probe.detect() })
        var operations = 0
        let startup = Task { await connection.check { operations += 1 } }

        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while !probe.hasStarted && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }

        XCTAssertTrue(probe.hasStarted)
        XCTAssertEqual(connection.state, .checking)
        startup.cancel()
        probe.release.signal()
        await startup.value
        XCTAssertEqual(operations, 0)
        XCTAssertEqual(connection.state, .unknown)
        probe.release.signal()
        await connection.check { operations += 1 }

        XCTAssertEqual(operations, 1)
        XCTAssertTrue(connection.isConnected)
    }

    @MainActor func testRefreshCannotReenterWhileItsReadIsInProgress() async {
        let connection = KeyboardConnection(detect: { true })
        var operations = 0
        await connection.check {
            await connection.check { operations += 100 }

            operations += 1
        }

        XCTAssertEqual(operations, 1)
        await connection.check { operations += 1 }

        XCTAssertEqual(operations, 2)
    }

    @MainActor func testConnectedKeyboardRunsOperationAfterDetection() async {
        let connection = KeyboardConnection(detect: { true })
        var operations = 0
        await connection.check {
            XCTAssertTrue(connection.isConnected)
            operations += 1
        }

        XCTAssertEqual(operations, 1)
    }
}
