import XCTest

private enum TestWaitError: Error { case timedOut }

@MainActor func waitForCondition(
    file: StaticString = #filePath, line: UInt = #line, _ condition: @MainActor () async throws -> Bool
) async throws {
    let deadline = ContinuousClock.now.advanced(by: .seconds(5))
    while !(try await condition()) {
        guard ContinuousClock.now < deadline else {
            XCTFail("Timed out waiting for the expected state", file: file, line: line)
            throw TestWaitError.timedOut
        }

        try await Task.sleep(for: .milliseconds(10))
    }
}
