import Combine
import Foundation

@MainActor final class KeyboardAppSession: ObservableObject {
    let connection: KeyboardConnection
    let permission: KeyboardPermission
    let state: KeyboardState
    let assignmentWorkflow: AssignmentWorkflow
    let activity: KeyboardDeviceActivity
    private enum InitialLoadStage { case lighting, assignments, complete }

    private var initialLoadStage = InitialLoadStage.lighting
    private var initialLoadPending = false
    private var observations = Set<AnyCancellable>()

    init(
        activity: KeyboardDeviceActivity = .shared, connection: KeyboardConnection? = nil,
        permission: KeyboardPermission? = nil, state: KeyboardState? = nil,
        assignmentWorkflow: AssignmentWorkflow? = nil
    ) {
        self.activity = activity
        self.connection = connection ?? KeyboardConnection()
        self.permission = permission ?? KeyboardPermission()
        self.state = state ?? KeyboardState(activity: activity, preferences: .standard)
        self.assignmentWorkflow = assignmentWorkflow ?? AssignmentWorkflow(activity: activity)

        Publishers.Merge3(
            self.permission.$access.map { $0 == .granted }.removeDuplicates().dropFirst().filter { $0 }.map { _ in () },
            activity.$isBusy.removeDuplicates().dropFirst().filter { !$0 }.map { _ in () },
            activity.$isQuitting.removeDuplicates().dropFirst().filter { !$0 }.map { _ in () }
        ).sink { [weak self] _ in Task { @MainActor [weak self] in await self?.resumeInitialLoad() } }.store(
            in: &observations)
        self.state.$snapshot.dropFirst().sink { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }

                self.assignmentWorkflow.invalidateIfKeyboardChanged(self.state.snapshot)
            }
        }.store(in: &observations)
    }

    func start() async {
        initialLoadPending = initialLoadStage != .complete
        await checkConnection {
            self.permission.requestIfNeeded()
            await self.loadInitialState()
        }
    }

    func applicationBecameActive() { permission.refresh() }

    func refresh(assignments: Bool, discardingLightingEdits: Bool = false) async {
        if discardingLightingEdits { state.confirmsLightingDiscard = false }

        initialLoadPending = initialLoadStage != .complete
        await checkConnection {
            self.permission.requestIfNeeded()
            guard self.permission.isGranted else { return }

            if self.initialLoadStage == .lighting {
                await self.loadInitialState()
            } else if assignments {
                self.initialLoadPending = false
                self.initialLoadStage = .complete
                await self.assignmentWorkflow.read()
            } else {
                await self.state.read(discardingEdits: discardingLightingEdits)
            }
        }
    }

    func loadAssignmentsIfNeeded() async {
        guard connection.isConnected, permission.isGranted, assignmentWorkflow.snapshot == nil, !activity.isBusy else {
            return
        }

        await checkConnection { await self.assignmentWorkflow.readIfNeeded() }
    }

    private func checkConnection(then operation: @MainActor () async -> Void) async {
        guard !activity.isQuitting else { return }

        await connection.check {
            // Detection runs off the main thread; quitting can begin while it is pending.
            guard !self.activity.isQuitting else { return }

            await operation()
        }

        switch connection.state {
        case .disconnected, .unavailable:
            initialLoadPending = false
            assignmentWorkflow.invalidateIfKeyboardChanged(nil)
        default: break
        }
    }

    private func resumeInitialLoad() async {
        guard initialLoadPending, connection.isConnected, permission.isGranted, !activity.isBusy, !activity.isQuitting
        else { return }

        // Permission approval or another window's operation may arrive well after the original detection.
        await checkConnection { await self.loadInitialState() }
    }

    private func loadInitialState() async {
        guard connection.isConnected, permission.isGranted, initialLoadStage != .complete, !activity.isBusy,
            !activity.isQuitting
        else { return }

        initialLoadPending = false
        defer { if Task.isCancelled { initialLoadStage = .lighting } }

        if initialLoadStage == .lighting {
            // A failed read stays complete until an explicit refresh; never retry it in the background.
            initialLoadStage = .complete
            await state.read()
            guard !Task.isCancelled, state.hasSnapshot else { return }

            initialLoadStage = .assignments
        }

        guard !activity.isQuitting else {
            initialLoadPending = true
            return
        }

        initialLoadStage = .complete
        await assignmentWorkflow.read()
    }
}
