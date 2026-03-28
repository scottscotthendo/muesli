// Captures system audio via ScreenCaptureKit and streams raw PCM float32 to stdout.
// Usage: audio_tap [sample_rate] [channels]
// Defaults: 48000 Hz, 2 channels
// Outputs: interleaved float32 PCM to stdout
// Send SIGINT or SIGTERM to stop.

import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

let sampleRate: Int = CommandLine.arguments.count > 1 ? Int(CommandLine.arguments[1]) ?? 48000 : 48000
let channels: Int = CommandLine.arguments.count > 2 ? Int(CommandLine.arguments[2]) ?? 2 : 2

class AudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    let semaphore = DispatchSemaphore(value: 0)
    var running = true

    func start() {
        // Handle termination signals
        signal(SIGINT) { _ in exit(0) }
        signal(SIGTERM) { _ in exit(0) }

        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: false) { content, error in
            guard let content = content, let display = content.displays.first else {
                FileHandle.standardError.write("Error: No display found\n".data(using: .utf8)!)
                exit(1)
            }

            // Exclude our own process from capture
            let selfPID = ProcessInfo.processInfo.processIdentifier
            let excludedApps = content.applications.filter { $0.processID == selfPID }

            let filter = SCContentFilter(display: display, excludingApplications: excludedApps, exceptingWindows: [])

            let config = SCStreamConfiguration()
            config.capturesAudio = true
            config.sampleRate = sampleRate
            config.channelCount = channels
            config.excludesCurrentProcessAudio = true
            // Minimize video overhead
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1) // 1 fps minimum

            let stream = SCStream(filter: filter, configuration: config, delegate: self)
            self.stream = stream

            let queue = DispatchQueue(label: "audio_capture", qos: .userInteractive)

            do {
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
                stream.startCapture { error in
                    if let error = error {
                        FileHandle.standardError.write("Capture error: \(error.localizedDescription)\n".data(using: .utf8)!)
                        exit(1)
                    }
                    FileHandle.standardError.write("Audio capture started (\(sampleRate) Hz, \(channels) ch)\n".data(using: .utf8)!)
                }
            } catch {
                FileHandle.standardError.write("Stream setup error: \(error.localizedDescription)\n".data(using: .utf8)!)
                exit(1)
            }
        }

        // Block until terminated
        semaphore.wait()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }

        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(blockBuffer, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &dataPointer)

        guard status == kCMBlockBufferNoErr, let ptr = dataPointer, length > 0 else { return }

        let data = Data(bytes: ptr, count: length)
        FileHandle.standardOutput.write(data)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write("Stream stopped: \(error.localizedDescription)\n".data(using: .utf8)!)
        semaphore.signal()
    }
}

let capture = AudioCapture()
capture.start()
RunLoop.main.run()
