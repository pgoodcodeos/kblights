import Foundation

@testable import KeyboardLights

final class BlockingDetection: @unchecked Sendable {
    private let lock = NSLock()
    private var started = false
    let release = DispatchSemaphore(value: 0)

    var hasStarted: Bool {
        lock.lock()
        defer { lock.unlock() }

        return started
    }

    func detect() throws -> Bool {
        lock.lock()
        started = true
        lock.unlock()
        guard release.wait(timeout: .now() + 5) == .success else {
            throw WorkflowError.message("Detection test timed out")
        }

        return true
    }
}

final class SessionProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var connected: Bool

    init(connected: Bool) { self.connected = connected }

    func setConnected(_ connected: Bool) {
        lock.lock()
        defer { lock.unlock() }

        self.connected = connected
    }

    func detect() -> Bool {
        lock.lock()
        defer { lock.unlock() }

        return connected
    }
}

actor SessionReadLog {
    private(set) var events: [String] = []
    func append(_ event: String) { events.append(event) }
}

actor SessionReadGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var released = false

    func wait() async {
        guard !released else { return }

        await withCheckedContinuation { continuation = $0 }
    }

    func reset() {
        precondition(continuation == nil)
        released = false
    }

    func release() {
        released = true
        continuation?.resume()
        continuation = nil
    }
}

actor SessionLightingService: LightingDeviceService {
    let log: SessionReadLog
    let gate: SessionReadGate?
    let captureError: WorkflowError?

    init(log: SessionReadLog, gate: SessionReadGate?, captureError: WorkflowError? = nil) {
        self.log = log
        self.gate = gate
        self.captureError = captureError
    }

    func capture(output: URL) async throws -> DeviceSnapshot {
        await log.append("lighting")
        if let gate { await gate.wait() }

        try Task.checkCancellation()
        if let captureError { throw captureError }

        return DeviceSnapshot(location: "01140000", lightingData: makeTestLightingImage())
    }

    func write(snapshot: DeviceSnapshot, candidate: Data, output: URL) async throws -> DeviceSnapshot {
        throw WorkflowError.message("Fixture sessions must never write to a keyboard")
    }
}

actor SessionAssignmentService: AssignmentDeviceService {
    let log: SessionReadLog
    let gate: SessionReadGate?
    init(log: SessionReadLog, gate: SessionReadGate?) {
        self.log = log
        self.gate = gate
    }

    func capture(output: URL) async throws -> AssignmentSnapshot {
        await log.append("assignments")
        if let gate { await gate.wait() }

        return AssignmentSnapshot(location: 0x0114_0000, channels: [Data(repeating: 0, count: 640)])
    }

    func write(snapshot: AssignmentSnapshot, draft: KeyAssignmentDraft, candidate: Data, output: URL) async throws
        -> AssignmentSnapshot
    { throw WorkflowError.message("Fixture sessions must never write to a keyboard") }
}

@MainActor final class SessionPermissionState {
    var access: KeyboardPermission.Access
    var requests = 0
    var settingsOpened = 0
    var applicationRevealed = 0
    init(access: KeyboardPermission.Access) { self.access = access }
}

@MainActor final class SessionFixture {
    let session: KeyboardAppSession
    let probe: SessionProbe
    let permission: SessionPermissionState
    let log = SessionReadLog()

    init(
        root: URL, connected: Bool = true, access: KeyboardPermission.Access = .granted,
        activity: KeyboardDeviceActivity? = nil, lightingGate: SessionReadGate? = nil,
        connection: KeyboardConnection? = nil, assignmentGate: SessionReadGate? = nil,
        lightingError: WorkflowError? = nil
    ) {
        let probe = SessionProbe(connected: connected)
        let permission = SessionPermissionState(access: access)
        let activity = activity ?? KeyboardDeviceActivity()
        let access = KeyboardPermission(
            check: { permission.access }, request: { permission.requests += 1 },
            showSettings: { permission.settingsOpened += 1 }, showApplication: { permission.applicationRevealed += 1 })
        self.probe = probe
        self.permission = permission
        session = KeyboardAppSession(
            activity: activity, connection: connection ?? KeyboardConnection(detect: { probe.detect() }),
            permission: access,
            state: KeyboardState(
                activity: activity,
                service: SessionLightingService(log: log, gate: lightingGate, captureError: lightingError),
                root: root.appendingPathComponent("lighting"), checkAccess: { access.isGranted }),
            assignmentWorkflow: AssignmentWorkflow(
                service: SessionAssignmentService(log: log, gate: assignmentGate),
                root: root.appendingPathComponent("assignments"), activity: activity, checkAccess: { access.isGranted })
        )
    }
}
