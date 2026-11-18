import AppKit
import SwiftUI

struct PageHeading: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.largeTitle.bold())
            Text(subtitle).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct StatusBadge: View {
    let title: String
    let symbol: String
    var tint: Color = .secondary
    var body: some View {
        Label(title, systemImage: symbol).font(.caption.weight(.medium)).foregroundStyle(tint).padding(.horizontal, 10)
            .padding(.vertical, 6).background(tint.opacity(0.09), in: Capsule())
    }
}

struct WorkspaceCard<Content: View>: View {
    private let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }

    var body: some View {
        content.padding(18).frame(maxWidth: .infinity, alignment: .leading).background(
            Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14)
        ).overlay { RoundedRectangle(cornerRadius: 14).strokeBorder(.primary.opacity(0.07)) }
    }
}

struct FeedbackBanner: View {
    let text: String
    var symbol = "info.circle.fill"
    var tint: Color = .secondary
    var body: some View {
        Label {
            Text(text).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: symbol).foregroundStyle(tint)
        }.font(.callout).padding(12).frame(maxWidth: .infinity, alignment: .leading).background(
            tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct OperationStatus: View {
    @ObservedObject var state: KeyboardState
    var body: some View {
        GroupBox("Keyboard status") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    if state.isBusy { ProgressView().controlSize(.small) }

                    Text(state.status)
                }

                if let error = state.error { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }

                if let warning = state.warning {
                    Text(warning).font(.caption).foregroundStyle(.orange).textSelection(.enabled)
                }

                if state.needsInputMonitoringAccess {
                    Button("Open Input Monitoring settings", action: KeyboardPermission.openSettings)
                }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
        }
    }
}
