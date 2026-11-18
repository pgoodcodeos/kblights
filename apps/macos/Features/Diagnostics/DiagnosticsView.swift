import AppKit
import SwiftUI

struct DiagnosticsView: View {
    @ObservedObject var state: KeyboardState
    @AppStorage(DeviceEvidence.loggingPreference) private var savesLogs = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                PageHeading(
                    title: "Diagnostics", subtitle: "Current keyboard status and optional troubleshooting logs.")
                OperationStatus(state: state)
                if state.captures.isEmpty {
                    ContentUnavailableView(
                        "No lighting read yet", systemImage: "waveform.path.ecg",
                        description: Text("Refresh to read the current keyboard settings."))
                } else {
                    ForEach(state.captures) { capture in
                        WorkspaceCard {
                            VStack(alignment: .leading, spacing: 14) {
                                HStack {
                                    Label("Lighting capture", systemImage: "lightbulb.led").font(.headline)
                                    Spacer()
                                    StatusBadge(title: "Verified", symbol: "checkmark.seal.fill", tint: .green)
                                }

                                LabeledContent(
                                    "Read at", value: capture.date.formatted(date: .abbreviated, time: .shortened))
                                LabeledContent("USB location", value: capture.location)
                                LabeledContent(
                                    "Capture size",
                                    value: "\(capture.bytes.formatted()) bytes · \(capture.blocks) blocks")
                                LabeledContent("Checksum", value: capture.additiveChecksum).monospacedDigit()
                            }
                        }
                    }
                }

                WorkspaceCard {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("Diagnostic logging", systemImage: "folder").font(.headline)
                        Toggle("Save diagnostic logs", isOn: $savesLogs)
                        Text("Off by default. Enable before troubleshooting to save reads, writes, and USB exchanges.")
                            .foregroundStyle(.secondary)
                        Button("Show records in Finder") {
                            let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[
                                0
                            ].appendingPathComponent("KeyboardLights")
                            NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: root.path)
                        }
                    }
                }
            }.padding(28).frame(maxWidth: 1000, alignment: .leading).frame(maxWidth: .infinity)
        }
    }
}
