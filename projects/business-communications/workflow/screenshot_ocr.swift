import Foundation
import Vision

struct LineObservation: Encodable {
    let text: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

do {
    let arguments = Array(CommandLine.arguments.dropFirst())
    let withLayout = arguments.count == 2 && arguments.first == "--layout"
    guard arguments.count == 1 || withLayout, let path = arguments.last else {
        throw NSError(domain: "ScreenshotOCR", code: 1, userInfo: [
            NSLocalizedDescriptionKey: "Pass a screenshot file path, optionally preceded by --layout."
        ])
    }
    let url = URL(fileURLWithPath: path)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
    let results = request.results ?? []
    let lines: [LineObservation] = results.compactMap { result in
        guard let text = result.topCandidates(1).first?.string else { return nil }
        let box = result.boundingBox
        return LineObservation(text: text, x: box.minX, y: 1 - box.maxY,
                               width: box.width, height: box.height)
    }
    let encoder = JSONEncoder()
    let data = withLayout ? try encoder.encode(lines) : try encoder.encode(lines.map(\.text))
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
} catch {
    let message = "Screenshot OCR failed: \(error)\n"
    FileHandle.standardError.write(Data(message.utf8))
    exit(1)
}
