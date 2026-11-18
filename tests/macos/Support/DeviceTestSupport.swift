import Foundation

func makeTestLightingImage() -> Data {
    var data = Data(repeating: 0, count: 1152)
    for (record, tag) in [UInt8(1), 2, 3, 4, 5, 6, 7, 0x80].enumerated() {
        let offset = record * 64
        data[offset] = tag
        data[offset + 62] = 0xAA
        data[offset + 63] = 0x55
    }

    data[0x440] = 1
    data[0x47E] = 0xAA
    data[0x47F] = 0x55
    return data
}
