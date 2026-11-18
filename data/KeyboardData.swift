import Foundation

public enum KeyboardResources {
    public static func data(named name: String) throws -> Data {
        let bundle: Bundle
        if Bundle.main.bundleURL.pathExtension == "app" {
            guard let resources = Bundle.main.resourceURL,
                let packaged = Bundle(url: resources.appendingPathComponent("KeyboardLights_KeyboardData.bundle"))
            else { throw CocoaError(.fileNoSuchFile) }

            bundle = packaged
        } else {
            bundle = .module
        }

        guard let url = bundle.url(forResource: name, withExtension: nil, subdirectory: "c989") else {
            throw CocoaError(.fileNoSuchFile)
        }

        return try Data(contentsOf: url)
    }
}
