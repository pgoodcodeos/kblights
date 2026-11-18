import XCTest

@testable import KeyboardLights

final class KeyboardPermissionTests: XCTestCase {
    @MainActor func testExistingApprovalSkipsPermissionRequest() {
        let state = SessionPermissionState(access: .granted)
        let permission = KeyboardPermission(check: { state.access }, request: { state.requests += 1 })
        permission.requestIfNeeded()
        XCTAssertTrue(permission.isGranted)
        XCTAssertEqual(state.requests, 0)
    }

    @MainActor func testPendingRequestDoesNotRepeatWhenReturningFromSettings() {
        let state = SessionPermissionState(access: .unknown)
        let permission = KeyboardPermission(check: { state.access }, request: { state.requests += 1 })
        permission.requestIfNeeded()
        permission.requestIfNeeded()
        XCTAssertFalse(permission.isGranted)
        XCTAssertEqual(state.requests, 1)
        state.access = .granted
        permission.refresh()
        XCTAssertTrue(permission.isGranted)
    }

    @MainActor func testDeniedAccessRequestsRegistrationOnceAndDetectsRevocation() {
        let state = SessionPermissionState(access: .denied)
        let permission = KeyboardPermission(check: { state.access }, request: { state.requests += 1 })
        permission.requestIfNeeded()
        permission.requestIfNeeded()
        XCTAssertEqual(state.requests, 1)
        state.access = .granted
        permission.refresh()
        XCTAssertTrue(permission.isGranted)
        state.access = .denied
        permission.refresh()
        XCTAssertFalse(permission.isGranted)
    }

    @MainActor func testSetupRetriesRegistrationBeforeOpeningSettingsForAnUnlistedDeniedApp() {
        let state = SessionPermissionState(access: .denied)
        var events: [String] = []
        let permission = KeyboardPermission(
            check: { state.access }, request: { events.append("request") }, showSettings: { events.append("settings") },
            showApplication: { events.append("finder") })
        permission.requestIfNeeded()
        permission.enableInputMonitoring()
        XCTAssertEqual(events, ["request", "request", "settings"])
        permission.refresh()
        XCTAssertEqual(events, ["request", "request", "settings"])
        permission.revealApplication()
        XCTAssertEqual(events.last, "finder")
        state.access = .granted
        permission.enableInputMonitoring()
        XCTAssertTrue(permission.isGranted)
        XCTAssertEqual(events, ["request", "request", "settings", "finder"])
    }

    @MainActor func testSetupRecognizesImmediateApprovalWithoutOpeningSettings() {
        let state = SessionPermissionState(access: .denied)
        var settingsOpened = false
        let permission = KeyboardPermission(
            check: { state.access }, request: { state.access = .granted }, showSettings: { settingsOpened = true })
        permission.enableInputMonitoring()
        XCTAssertTrue(permission.isGranted)
        XCTAssertFalse(settingsOpened)
    }

    @MainActor func testImmediateApprovalIsRecognizedWithoutAnotherActivation() {
        let state = SessionPermissionState(access: .unknown)
        let permission = KeyboardPermission(check: { state.access }, request: { state.access = .granted })
        permission.requestIfNeeded()
        XCTAssertTrue(permission.isGranted)
    }
}
