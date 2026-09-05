import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else { print("usage: ocr <img>"); exit(1) }
guard let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("load fail\n".data(using:.utf8)!); exit(2)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["ja-JP", "en-US"]
req.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([req])
guard let obs = req.results else { exit(0) }
for o in obs {
    guard let c = o.topCandidates(1).first else { continue }
    let b = o.boundingBox
    // x, y(top-origin), w, h, text
    let y = 1.0 - b.origin.y - b.size.height
    print(String(format:"%.4f\t%.4f\t%.4f\t%.4f\t%@", b.origin.x, y, b.size.width, b.size.height, c.string))
}
