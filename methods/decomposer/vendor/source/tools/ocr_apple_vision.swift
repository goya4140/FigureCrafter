#!/usr/bin/env swift

import AppKit
import Foundation
import Vision

struct OCRItem: Codable {
    let text: String
    let confidence: Float
    let x: Int
    let y: Int
    let w: Int
    let h: Int
}

struct OCRResult: Codable {
    let schema_version: String
    let backend: String
    let width: Int
    let height: Int
    let items: [OCRItem]
}

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: ocr_apple_vision.swift <image>\n", stderr)
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let image = NSImage(contentsOf: imageURL) else {
    fputs("Cannot load image: \(imageURL.path)\n", stderr)
    exit(1)
}
var proposedRect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &proposedRect, context: nil, hints: nil) else {
    fputs("Cannot create CGImage from: \(imageURL.path)\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.minimumTextHeight = 0.012
let supported = try request.supportedRecognitionLanguages()
let preferred = ["en-US", "zh-Hans", "zh-Hant"].filter { supported.contains($0) }
if !preferred.isEmpty {
    request.recognitionLanguages = preferred
}
try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
let width = cgImage.width
let height = cgImage.height
let observations = request.results ?? []
var items: [OCRItem] = []
for observation in observations {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    let x = max(0, Int((box.minX * CGFloat(width)).rounded()))
    let y = max(0, Int(((1 - box.maxY) * CGFloat(height)).rounded()))
    let w = max(1, Int((box.width * CGFloat(width)).rounded()))
    let h = max(1, Int((box.height * CGFloat(height)).rounded()))
    items.append(OCRItem(text: candidate.string, confidence: candidate.confidence, x: x, y: y, w: w, h: h))
}
items.sort { left, right in
    if abs(left.y - right.y) <= 4 { return left.x < right.x }
    return left.y < right.y
}
let result = OCRResult(schema_version: "1.0", backend: "apple-vision", width: width, height: height, items: items)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
let data = try encoder.encode(result)
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
