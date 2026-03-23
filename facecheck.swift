import AVFoundation
import AppKit
import Vision

class FaceChecker: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private var captureSession: AVCaptureSession?
    private var faceDetected = false
    private var frameProcessed = false
    private let semaphore = DispatchSemaphore(value: 0)
    
    func checkForFace() -> Bool {
        // Request camera access
        let authStatus = AVCaptureDevice.authorizationStatus(for: .video)
        
        switch authStatus {
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                if granted {
                    self.startCapture()
                } else {
                    print("DENIED")
                    self.semaphore.signal()
                }
            }
        case .authorized:
            startCapture()
        case .denied, .restricted:
            print("DENIED")
            return false
        @unknown default:
            print("DENIED")
            return false
        }
        
        // Wait for result (max 5 seconds)
        _ = semaphore.wait(timeout: .now() + 5)
        return faceDetected
    }
    
    private func startCapture() {
        captureSession = AVCaptureSession()
        captureSession?.sessionPreset = .medium
        
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) 
              ?? AVCaptureDevice.default(for: .video) else {
            print("NO_CAMERA")
            semaphore.signal()
            return
        }
        
        do {
            let input = try AVCaptureDeviceInput(device: camera)
            if captureSession?.canAddInput(input) == true {
                captureSession?.addInput(input)
            }
        } catch {
            print("INPUT_ERROR")
            semaphore.signal()
            return
        }
        
        let output = AVCaptureVideoDataOutput()
        output.setSampleBufferDelegate(self, queue: DispatchQueue(label: "facecheck"))
        
        if captureSession?.canAddOutput(output) == true {
            captureSession?.addOutput(output)
        }
        
        captureSession?.startRunning()
    }
    
    private var frameCount = 0
    
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        frameCount += 1
        // Skip first 10 frames to let camera warm up
        guard frameCount > 10 else { return }
        guard !frameProcessed else { return }
        frameProcessed = true
        
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            semaphore.signal()
            return
        }
        
        let request = VNDetectFaceLandmarksRequest { [weak self] request, error in
            if let results = request.results as? [VNFaceObservation], !results.isEmpty {
                // Any face detected (even partial)
                self?.faceDetected = true
                print("FACE_DETECTED (\(results.count) face(s))")
            } else {
                print("NO_FACE")
            }
            
            self?.captureSession?.stopRunning()
            self?.semaphore.signal()
        }
        // Lower confidence threshold
        request.revision = VNDetectFaceLandmarksRequestRevision3
        
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:])
        try? handler.perform([request])
    }
}

// Main
let outputPath = "/tmp/facecheck_result.txt"
let checker = FaceChecker()
let result = checker.checkForFace()

// Write result to file
try? (result ? "PRESENT" : "AWAY").write(toFile: outputPath, atomically: true, encoding: .utf8)

exit(result ? 0 : 1)
