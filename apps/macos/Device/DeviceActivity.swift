import Combine
import Foundation

@MainActor final class KeyboardDeviceActivity: ObservableObject {
    static let shared = KeyboardDeviceActivity()
    @Published private(set) var isBusy = false
    @Published private(set) var isQuitting = false
    private var operationBusy = false
    private var reservation: UUID?
    @Published private(set) var inspectionEvidence: URL?
    @Published private(set) var requiresInspection = false

    func blockWrites(evidence: URL?) {
        // Preserve the first unresolved transaction across windows and workflows.
        guard !requiresInspection else { return }

        inspectionEvidence = evidence
        requiresInspection = true
    }

    func checkWriteAllowed() throws {
        guard requiresInspection else { return }

        let records = inspectionEvidence.map { " Records: " + $0.path } ?? ""
        throw WorkflowError.message("A previous keyboard write needs inspection. Further writes are blocked." + records)
    }

    func prepareToQuit() { isQuitting = true }

    func cancelQuit() { isQuitting = false }

    func reserve() throws -> UUID {
        guard !isQuitting else { throw WorkflowError.message("KeyboardLights is quitting.") }

        guard !isBusy else { throw WorkflowError.message("Another keyboard operation is underway.") }

        try checkWriteAllowed()
        let owner = UUID()
        reservation = owner
        isBusy = true
        return owner
    }

    func release(_ owner: UUID) {
        guard reservation == owner, !operationBusy else { return }

        reservation = nil
        isBusy = false
    }

    func begin(owner: UUID? = nil) throws {
        // Let an existing reserved update finish, but do not start fresh work while quitting.
        guard !isQuitting || (owner != nil && reservation == owner) else {
            throw WorkflowError.message("KeyboardLights is quitting.")
        }

        guard !operationBusy, reservation == owner else {
            throw WorkflowError.message("Another keyboard operation is underway.")
        }

        operationBusy = true
        isBusy = true
    }

    func finish() {
        operationBusy = false
        isBusy = reservation != nil
    }
}
