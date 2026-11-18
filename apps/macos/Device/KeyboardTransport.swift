import Darwin
import Foundation
import IOKit
import IOKit.hid
import IOUSBHost

struct DeviceTransfer {
    var data: Data
    var length: Int?
    var success: Bool
    var domain: String
    var code: Int64
    var message: String = ""
}

enum KeyboardReport {
    case feature
    case lockIndicators

    var type: String { self == .feature ? "feature" : "output" }

    var length: Int { self == .feature ? 64 : 1 }

    func request(input: Bool) -> IOUSBDeviceRequest {
        IOUSBDeviceRequest(
            bmRequestType: input ? 0xA1 : 0x21, bRequest: input ? 1 : 9, wValue: self == .feature ? 0x0300 : 0x0200,
            wIndex: 0, wLength: UInt16(length))
    }
}

// Synchronous, operation-local transport; created and used on the worker thread.
protocol KeyboardTransport: AnyObject {
    var location: UInt32 { get }

    func transfer(usb: Bool, input: Bool, data: Data) -> DeviceTransfer
    func readLockIndicators() -> DeviceTransfer
    func close()
}

final class MacKeyboardTransport: KeyboardTransport {
    let location: UInt32
    private let raw: IOUSBHostDevice
    private let hid: IOHIDDevice

    // Registry metadata only: no device handles, reports, or permission requests.
    static func isConnected() throws -> Bool {
        let services = try keyboardServices()
        defer { for service in services { IOObjectRelease(service) } }

        guard !services.isEmpty else { return false }

        guard services.count == 1 else { throw WorkflowError.message("Connect only one C989 keyboard to continue.") }

        let service = services[0]
        guard property(service, "bcdDevice") == 0x0138, (property(service, "locationID") ?? 0) != 0 else {
            throw WorkflowError.message("This C989 is not a supported legacy keyboard.")
        }

        return true
    }

    private static func keyboardServices() throws -> [io_service_t] {
        var iterator: io_iterator_t = 0
        guard
            IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOUSBHostDevice"), &iterator)
                == KERN_SUCCESS
        else { throw DeviceEvidence.failure("enumerate keyboards") }

        defer { IOObjectRelease(iterator) }

        var services: [io_service_t] = []
        while true {
            let service = IOIteratorNext(iterator)
            if service == 0 { break }

            if property(service, "idVendor") == 0x0C45 && property(service, "idProduct") == 0x8006 {
                services.append(service)
            } else {
                IOObjectRelease(service)
            }
        }

        return services
    }

    init(requiredLocation: UInt32) throws {
        let services = try Self.keyboardServices()
        defer { for service in services { IOObjectRelease(service) } }

        guard services.count == 1 else { throw DeviceEvidence.failure("identify exactly one legacy keyboard") }

        let service = services[0]
        let found = Self.property(service, "locationID")
        guard Self.property(service, "bcdDevice") == 0x0138, let found, found != 0,
            requiredLocation == 0 || found == requiredLocation
        else { throw DeviceEvidence.failure("verify keyboard revision and location") }

        location = found
        raw = try IOUSBHostDevice(__ioService: service, options: [], queue: nil, interestHandler: nil)
        do {
            // Close the manager before explicitly opening the retained device, as in
            // the original transport. Manager teardown must not close our live session.
            let device: IOHIDDevice = try {
                let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
                IOHIDManagerSetDeviceMatching(
                    manager, [kIOHIDVendorIDKey: 0x0C45, kIOHIDProductIDKey: 0x8006] as CFDictionary)
                guard IOHIDManagerOpen(manager, 0) == kIOReturnSuccess else {
                    throw DeviceEvidence.failure("open HID manager")
                }

                defer { IOHIDManagerClose(manager, 0) }

                let devices = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice> ?? []
                let matches = devices.filter {
                    Self.property($0, kIOHIDPrimaryUsagePageKey) == 1 && Self.property($0, kIOHIDPrimaryUsageKey) == 6
                        && Self.property($0, kIOHIDMaxFeatureReportSizeKey) == 64
                }

                guard matches.count == 1, let device = matches.first else {
                    throw DeviceEvidence.failure("identify exactly one HID keyboard")
                }

                return device
            }()
            guard Self.property(device, kIOHIDVersionNumberKey) == 0x0138,
                Self.property(device, kIOHIDLocationIDKey) == found, IOHIDDeviceOpen(device, 0) == kIOReturnSuccess
            else { throw DeviceEvidence.failure("verify and open HID keyboard") }

            hid = device
        } catch {
            raw.destroy()
            throw error
        }
    }

    private static func property(_ service: io_service_t, _ key: String) -> UInt32? {
        guard
            let value = IORegistryEntryCreateCFProperty(service, key as CFString, kCFAllocatorDefault, 0)?
                .takeRetainedValue(), CFGetTypeID(value) == CFNumberGetTypeID()
        else { return nil }

        return (value as? NSNumber)?.uint32Value
    }

    private static func property(_ device: IOHIDDevice, _ key: String) -> UInt32? {
        guard let value = IOHIDDeviceGetProperty(device, key as CFString), CFGetTypeID(value) == CFNumberGetTypeID()
        else { return nil }

        return (value as? NSNumber)?.uint32Value
    }

    func close() {
        IOHIDDeviceClose(hid, 0)
        raw.destroy()
    }

    func readLockIndicators() -> DeviceTransfer { transferUSB(.lockIndicators, input: true, data: Data([0])) }

    private func transferUSB(_ report: KeyboardReport, input: Bool, data: Data) -> DeviceTransfer {
        let buffer = NSMutableData(data: data)
        var length = 0
        let request = report.request(input: input)
        do {
            try raw.__send(request, data: buffer, bytesTransferred: &length, completionTimeout: 1)
            return DeviceTransfer(
                data: buffer as Data, length: length, success: true, domain: "IOUSBHost-BOOL", code: 0)
        } catch {
            let failure = error as NSError
            return DeviceTransfer(
                data: buffer as Data, length: length, success: false, domain: failure.domain, code: Int64(failure.code),
                message: failure.description)
        }
    }

    func transfer(usb: Bool, input: Bool, data: Data) -> DeviceTransfer {
        if usb { return transferUSB(.feature, input: input, data: data) }

        var buffer = Data(data)
        var length = 64
        let result: IOReturn
        if input {
            result = buffer.withUnsafeMutableBytes {
                IOHIDDeviceGetReport(
                    hid, kIOHIDReportTypeFeature, 0, $0.bindMemory(to: UInt8.self).baseAddress!, &length)
            }
        } else {
            result = buffer.withUnsafeBytes {
                IOHIDDeviceSetReport(hid, kIOHIDReportTypeFeature, 0, $0.bindMemory(to: UInt8.self).baseAddress!, 64)
            }
        }

        return DeviceTransfer(
            data: buffer, length: input ? length : nil, success: result == kIOReturnSuccess, domain: "IOReturn",
            code: Int64(result), message: String(format: "0x%08X", result))
    }
}
