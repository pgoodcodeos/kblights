import SwiftUI

struct KeyboardPermissionView: View {
    @ObservedObject var permission: KeyboardPermission

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Allow keyboard access", systemImage: "keyboard").font(.title2.bold())
            Text("KeyboardLights needs Input Monitoring to read and update your keyboard’s settings.")
            Text("Turn on KeyboardLights in System Settings → Privacy & Security → Input Monitoring.").foregroundStyle(
                .secondary)
            Button("Open Input Monitoring", action: permission.enableInputMonitoring).buttonStyle(.borderedProminent)
            Divider()
            Text("App missing from the list? Use + in Input Monitoring to add the app shown in Finder.").font(.callout)
                .foregroundStyle(.secondary)
            Button("Show app in Finder", action: permission.revealApplication)
            Text("Choose Quit & Reopen if macOS asks. Otherwise, return here and setup continues automatically.").font(
                .callout
            ).foregroundStyle(.secondary)
        }.padding(28).frame(maxWidth: 600, alignment: .leading).frame(
            maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading
        ).accessibilityIdentifier("keyboard-permission-setup")
    }
}
