import Foundation
import Vision

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "ScreenshotOCR", code: 1, userInfo: [
            NSLocalizedDescriptionKey: "Pass exactly one screenshot file path."
        ])
    }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
    let results = request.results ?? []
    let lines = results.compactMap { $0.topCandidates(1).first?.string }
    let data = try JSONEncoder().encode(lines)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
} catch {
    let message = "Screenshot OCR failed: \(error)\n"
    FileHandle.standardError.write(Data(message.utf8))
    exit(1)
}
