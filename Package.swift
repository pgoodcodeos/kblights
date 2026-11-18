// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "KeyboardLights", platforms: [.macOS(.v14)],
    products: [.executable(name: "KeyboardLights", targets: ["KeyboardLights"])],
    targets: [
        .target(name: "C989Native", path: "core", publicHeadersPath: "include"),
        .target(name: "KeyboardData", path: "data", resources: [.copy("c989")]),
        .executableTarget(
            name: "KeyboardLights", dependencies: ["C989Native", "KeyboardData"], path: "apps/macos",
            exclude: ["Resources"], linkerSettings: [.linkedFramework("IOKit"), .linkedFramework("IOUSBHost")]),
        .testTarget(name: "KeyboardLightsTests", dependencies: ["KeyboardLights", "KeyboardData"], path: "tests/macos")
    ], cLanguageStandard: .c11)
