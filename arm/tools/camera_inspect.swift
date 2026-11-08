// Native macOS USB camera inventory and bounded local snapshots. No network.
import AVFoundation
import AppKit
import CoreImage
import Foundation

final class FrameSink: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let done = DispatchSemaphore(value: 0)
    let output: URL
    let frameLimit: Int
    var count = 0
    var saved: [[String: Any]] = []
    var lastSaved = -Double.infinity
    var result: [String: Any] = [:]
    init(_ output: URL, frameLimit: Int) { self.output = output; self.frameLimit = frameLimit }
    func captureOutput(_ captureOutput: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        count += 1
        let received = ProcessInfo.processInfo.systemUptime
        guard count >= 5, saved.count < frameLimit, received - lastSaved >= 0.18,
              let pixel = CMSampleBufferGetImageBuffer(sample) else { return }
        let destination = frameLimit == 1 ? output : output.deletingPathExtension()
            .appendingPathExtension(String(format: "%03d.jpg", saved.count))
        do {
            try CIContext().writeJPEGRepresentation(of: CIImage(cvPixelBuffer: pixel), to: destination,
                colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!, options: [:])
            result = ["image": destination.path, "width": CVPixelBufferGetWidth(pixel),
                      "height": CVPixelBufferGetHeight(pixel), "frames_received": count,
                      "sample_pts_s": CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample)),
                      "receipt_uptime_s": received,
                      "timing_note": "AVFoundation sample PTS; sensor exposure timing unvalidated"]
            saved.append(result)
            if let metadata = try? JSONSerialization.data(withJSONObject: result) {
                try? metadata.write(to: output.deletingLastPathComponent().appendingPathComponent("capture-latest.json"), options: .atomic)
                if saved.count == 1 {
                    try? metadata.write(to: output.deletingLastPathComponent().appendingPathComponent("capture-ready.txt"), options: .atomic)
                }
            }
            lastSaved = received
            if saved.count == frameLimit { done.signal() }
        } catch { result = ["error": error.localizedDescription]; done.signal() }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let args = CommandLine.arguments
let frameLimit = args.count > 2 ? Int(args[2]) ?? 0 : 1
guard (1...900).contains(frameLimit) else { exit(2) }
let selection = args.count > 3 ? args[3] : "all"
let requestedSize = args.count > 4 ? args[4] : "640x480"
guard ["640x480", "1920x1080"].contains(requestedSize) else { exit(2) }
let dimensions = requestedSize.split(separator: "x").map { Int32($0)! }
let base = URL(fileURLWithPath: args.count > 1 ? args[1] : NSTemporaryDirectory() + "maker-camera")
try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
guard !FileManager.default.fileExists(atPath: base.appendingPathComponent("camera-report.json").path),
      !FileManager.default.fileExists(atPath: base.appendingPathComponent("capture-ready.txt").path)
else { exit(2) } // Preserve prior captures; each run gets a new directory.
let available = AVCaptureDevice.DiscoverySession(deviceTypes: [.external, .builtInWideAngleCamera], mediaType: .video,
    position: .unspecified).devices.filter {
        $0.localizedName == "KD-USB Cameras" || $0.localizedName == "MacBook Pro Camera"
    }
let devices = available.filter { selection == "all" || $0.uniqueID == selection }
var report: [String: Any] = ["when": ISO8601DateFormatter().string(from: Date()),
                           "authorization_before": AVCaptureDevice.authorizationStatus(for: .video).rawValue,
                           "requested_frames": frameLimit, "selection": selection,
                           "requested_size": requestedSize,
                           "usb_camera_count": available.filter { $0.localizedName == "KD-USB Cameras" }.count,
                           "available_devices": available.map { ["name": $0.localizedName, "unique_id": $0.uniqueID] }]
if devices.isEmpty { report["error"] = "No matching camera connected" }
if selection == "all" && available.filter({ $0.localizedName == "KD-USB Cameras" }).count != 3 {
    report["error"] = "Expected three robot USB cameras; inspect available_devices for missing sources"
}
var requested = false
var granted = AVCaptureDevice.authorizationStatus(for: .video) == .authorized
if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
    AVCaptureDevice.requestAccess(for: .video) { value in
        DispatchQueue.main.async { granted = value; requested = true }
    }
    let until = Date().addingTimeInterval(40)
    while !requested && Date() < until { RunLoop.current.run(until: Date().addingTimeInterval(0.1)) }
}
report["access_granted"] = granted
var entries: [[String: Any]] = []
for device in devices {
    var entry: [String: Any] = ["unique_id": device.uniqueID, "name": device.localizedName,
                               "in_use": device.isInUseByAnotherApplication]
    entry["formats"] = device.formats.map { format -> [String: Any] in
        let d = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
        return ["width": d.width, "height": d.height,
                "pixel_format": CMFormatDescriptionGetMediaSubType(format.formatDescription),
                "fps_ranges": format.videoSupportedFrameRateRanges.map {
                    ["min": $0.minFrameRate, "max": $0.maxFrameRate] }]
    }
    if granted && !device.isInUseByAnotherApplication {
        let session = AVCaptureSession()
        let sink = FrameSink(base.appendingPathComponent("camera-\(device.uniqueID).jpg"), frameLimit: frameLimit)
        let queue = DispatchQueue(label: "maker.camera.frames")
        do {
            session.beginConfiguration()
            session.sessionPreset = requestedSize == "640x480" ? .vga640x480 : .high
            let input = try AVCaptureDeviceInput(device: device)
            guard session.canAddInput(input) else { throw NSError(domain: "Camera", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Cannot add camera input"]) }
            session.addInput(input)
            let format = device.formats.first(where: {
                let size = CMVideoFormatDescriptionGetDimensions($0.formatDescription)
                return size.width == dimensions[0] && size.height == dimensions[1] &&
                    CMFormatDescriptionGetMediaSubType($0.formatDescription) == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange &&
                    $0.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= 5 && $0.maxFrameRate >= 5 }
            })
            if let format = format {
                try device.lockForConfiguration()
                device.activeFormat = format
                device.activeVideoMinFrameDuration = CMTime(value: 1, timescale: 5)
                device.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: 5)
                device.unlockForConfiguration()
            } else if device.localizedName == "KD-USB Cameras" || requestedSize != "640x480" {
                throw NSError(domain: "Camera", code: 2,
                    userInfo: [NSLocalizedDescriptionKey: "No \(requestedSize) NV12 5fps format"])
            }
            let output = AVCaptureVideoDataOutput()
            output.alwaysDiscardsLateVideoFrames = true
            output.setSampleBufferDelegate(sink, queue: queue)
            guard session.canAddOutput(output) else { throw NSError(domain: "Camera", code: 3,
                userInfo: [NSLocalizedDescriptionKey: "Cannot add video output"]) }
            session.addOutput(output)
            session.commitConfiguration()
            session.startRunning()
            let completed = sink.done.wait(timeout: .now() + Double(frameLimit) / 4 + 10) == .success
            session.stopRunning()
            queue.sync {
                entry["capture"] = completed ? sink.result : ["error": "Capture deadline exceeded", "frames_received": sink.count]
                if completed, let width = sink.result["width"] as? Int,
                   let height = sink.result["height"] as? Int,
                   (width != Int(dimensions[0]) || height != Int(dimensions[1])) {
                    entry["capture"] = ["error": "Captured dimensions differ from requested format",
                                        "actual_width": width, "actual_height": height]
                }
                entry["saved_frames"] = sink.saved
            }
        } catch {
            session.commitConfiguration()
            session.stopRunning()
            entry["capture"] = ["error": error.localizedDescription]
        }
    }
    entries.append(entry)
}
report["cameras"] = entries
let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
try data.write(to: base.appendingPathComponent("camera-report.json"), options: .atomic)
