// subject_lift.swift
//
// Apple Vision（VNGenerateForegroundInstanceMaskRequest、macOS 14+専用）による
// 被写体切り抜き。extract.py（Python・rembg/RVM系）と同じ「固定形式のファイル一式」
// 契約（subject-rgba.png / alpha.png / mask.png / preview.png / metadata.json）を
// このツール単体で出力する。spotlight-jobsのmacOSランナー（extract-apple段）から
// ビルド・実行される想定で、単体で完結させるためFoundation/Vision/CoreImageのみに
// 依存する（AppKit・ImageIOは使わない：画像の読み込みはCIImage(contentsOf:)、
// PNG書き出しはCIContext.writePNGRepresentation(...)で完結する）。
// CoreGraphics/CoreVideoはCGRect/CGColorSpace/CVPixelBuffer等の型のためにやむを得ず
// importするが、これらはAppKit/ImageIOと違いmacOS標準の軽量な基盤フレームワークで
// あり追加の依存関係にはならない。
//
// ビルド: swiftc -O subject_lift.swift -o subject_lift
// 使い方: ./subject_lift <入力画像> <出力ディレクトリ>
// 終了コード:
//   0 = 成功
//   1 = 引数が不正
//   2 = 被写体が検出できなかった（VNGenerateForegroundInstanceMaskRequestの結果が
//       空、またはallInstancesが空）
//   3 = macOSのバージョンが古い（VNGenerateForegroundInstanceMaskRequestはmacOS 14.0+）
//   4 = 入力画像を読み込めない
//   5 = Visionへのリクエスト、またはマスク生成そのものが失敗した

import Foundation
import Vision
import CoreImage
import CoreGraphics
import CoreVideo
import CoreML  // request.supportedComputeStageDevices の戻り値型（[VNComputeStage: [MLComputeDevice]]）のため

func fail(_ code: Int32, _ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    fail(1, "使い方: subject_lift <入力画像> <出力ディレクトリ>")
}
let inputPath = arguments[1]
let outDir = arguments[2]

guard #available(macOS 14.0, *) else {
    fail(3, "Apple Vision（VNGenerateForegroundInstanceMaskRequest）にはmacOS 14.0以降が必要です")
}

let startTime = Date()

let inputURL = URL(fileURLWithPath: inputPath)
// このツールの入力は常にffmpeg/PILが動画フレームから書き出すJPEG（frame.jpg／
// clip_%04d.jpg）で、EXIFの回転タグを持たないため、CIImage(contentsOf:)の既定の
// 読み込みでそのままの向きになる（明示的なoriented()補正は行わない）。
guard let sourceImage = CIImage(contentsOf: inputURL) else {
    fail(4, "入力画像を読み込めません: \(inputPath)")
}

let handler = VNImageRequestHandler(ciImage: sourceImage, options: [:])
let request = VNGenerateForegroundInstanceMaskRequest()

// 初回の実機（GitHub-hosted macOSランナー）検証で、Visionが要求するコンピュートデバイス
// （GPU/ANE）がそのまま使えるかどうかをログに残しておく（うまく走らなかった場合の
// 切り分け用。ANEはmacOSランナーの仮想化環境では利用できない可能性があるとの報告があるため）。
if let devices = try? request.supportedComputeStageDevices {
    FileHandle.standardError.write("Vision compute devices: \(devices)\n".data(using: .utf8)!)
}

do {
    try handler.perform([request])
} catch {
    fail(5, "Vision へのリクエストが失敗しました: \(error.localizedDescription)")
}

guard let observation = request.results?.first, !observation.allInstances.isEmpty else {
    // 被写体を検出できなかった場合。resultsが空/nil、allInstancesが空のいずれも
    // 「検出0件」として扱う（Appleは空配列時の挙動を明文化していないため両方をガードする）。
    exit(2)
}

let maskPixelBuffer: CVPixelBuffer
do {
    // 全インスタンス（allInstances）を1つに合成したソフトマスクを、元画像と同じ解像度で取得する。
    maskPixelBuffer = try observation.generateScaledMaskForImage(forInstances: observation.allInstances, from: handler)
} catch {
    fail(5, "マスク生成が失敗しました: \(error.localizedDescription)")
}

let width = CVPixelBufferGetWidth(maskPixelBuffer)
let height = CVPixelBufferGetHeight(maskPixelBuffer)

CVPixelBufferLockBaseAddress(maskPixelBuffer, .readOnly)
guard let maskBase = CVPixelBufferGetBaseAddress(maskPixelBuffer) else {
    CVPixelBufferUnlockBaseAddress(maskPixelBuffer, .readOnly)
    fail(5, "マスクのピクセルバッファを読み取れません")
}
let maskBytesPerRow = CVPixelBufferGetBytesPerRow(maskPixelBuffer)
let maskFormat = CVPixelBufferGetPixelFormatType(maskPixelBuffer)

// マスクの画素形式はAppleが公式文書化していない（低解像度版はOneComponent32Floatと文書化
// されているのみ）。実測でOneComponent8だったケースの報告もあるため両方に対応する。
var alphaBytes = [UInt8](repeating: 0, count: width * height)
alphaBytes.withUnsafeMutableBufferPointer { dstBuf in
    let dst = dstBuf.baseAddress!
    if maskFormat == kCVPixelFormatType_OneComponent8 {
        for y in 0..<height {
            memcpy(dst.advanced(by: y * width), maskBase.advanced(by: y * maskBytesPerRow), width)
        }
    } else {
        for y in 0..<height {
            let srcRow = maskBase.advanced(by: y * maskBytesPerRow).assumingMemoryBound(to: Float32.self)
            for x in 0..<width {
                let scaled = (srcRow[x] * 255.0).rounded()
                dst[y * width + x] = UInt8(min(max(scaled, 0.0), 255.0))
            }
        }
    }
}
CVPixelBufferUnlockBaseAddress(maskPixelBuffer, .readOnly)

// 被写体のRGBは、Vision側の推定結果ではなく元画像そのものをそのまま使う（extract.py側の
// 他モデルと同じ考え方：モデルが予測した前景色ではなく、実際のフレームの画素を使う）。
let ciContext = CIContext()
let rgbColorSpace = CGColorSpaceCreateDeviceRGB()
var originalRGBA = [UInt8](repeating: 0, count: width * height * 4)
originalRGBA.withUnsafeMutableBytes { rawBuf in
    ciContext.render(sourceImage, toBitmap: rawBuf.baseAddress!, rowBytes: width * 4,
                      bounds: CGRect(x: 0, y: 0, width: width, height: height),
                      format: .RGBA8, colorSpace: rgbColorSpace)
}

// subject-rgba.png：元画像のRGB + 上で得たアルファ（ストレートアルファ、前景以外は透明）
var subjectRGBA = [UInt8](repeating: 0, count: width * height * 4)
// mask.png：アルファ>=128の2値マスク
var maskBinary = [UInt8](repeating: 0, count: width * height)
for i in 0..<(width * height) {
    subjectRGBA[i * 4 + 0] = originalRGBA[i * 4 + 0]
    subjectRGBA[i * 4 + 1] = originalRGBA[i * 4 + 1]
    subjectRGBA[i * 4 + 2] = originalRGBA[i * 4 + 2]
    subjectRGBA[i * 4 + 3] = alphaBytes[i]
    maskBinary[i] = alphaBytes[i] >= 128 ? 255 : 0
}

// preview.png：extract.py の make_checker/build_preview と同じチェッカー柄（16px角、
// light=235/dark=205）に合成する確認用プレビュー。CIFormatに3チャンネルのRGB専用形式が
// 無いため、アルファ常時255のRGBA8として書き出す（見た目はextract.py側と同一）。
func checkerValue(_ x: Int, _ y: Int) -> Float {
    let size = 16
    let xBlock = (x / size) % 2
    let yBlock = (y / size) % 2
    return xBlock == yBlock ? 235.0 : 205.0
}
var previewRGBA = [UInt8](repeating: 255, count: width * height * 4)
for y in 0..<height {
    for x in 0..<width {
        let i = y * width + x
        let a = Float(alphaBytes[i]) / 255.0
        let checker = checkerValue(x, y)
        let r = Float(originalRGBA[i * 4 + 0])
        let g = Float(originalRGBA[i * 4 + 1])
        let b = Float(originalRGBA[i * 4 + 2])
        previewRGBA[i * 4 + 0] = UInt8(min(max(checker * (1 - a) + r * a, 0), 255).rounded())
        previewRGBA[i * 4 + 1] = UInt8(min(max(checker * (1 - a) + g * a, 0), 255).rounded())
        previewRGBA[i * 4 + 2] = UInt8(min(max(checker * (1 - a) + b * a, 0), 255).rounded())
        previewRGBA[i * 4 + 3] = 255
    }
}

try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

func writePNG(_ bytes: [UInt8], bytesPerPixel: Int, format: CIFormat, colorSpace: CGColorSpace, name: String) {
    let data = Data(bytes)
    // CIImage(bitmapData:bytesPerRow:size:format:colorSpace:) はこの版のSDKでは
    // 非failable（CIImage?ではなくCIImageを返す）。実機ビルドで判明した
    // （事前のAPI調査では失敗判定つきと想定していたが、実際のシグネチャと異なった）。
    let image = CIImage(bitmapData: data, bytesPerRow: width * bytesPerPixel,
                         size: CGSize(width: width, height: height), format: format, colorSpace: colorSpace)
    let url = URL(fileURLWithPath: outDir).appendingPathComponent(name)
    do {
        try ciContext.writePNGRepresentation(of: image, to: url, format: format, colorSpace: colorSpace, options: [:])
    } catch {
        fail(5, "\(name) の書き出しに失敗しました: \(error.localizedDescription)")
    }
}

let grayColorSpace = CGColorSpaceCreateDeviceGray()
writePNG(subjectRGBA, bytesPerPixel: 4, format: .RGBA8, colorSpace: rgbColorSpace, name: "subject-rgba.png")
writePNG(alphaBytes, bytesPerPixel: 1, format: .L8, colorSpace: grayColorSpace, name: "alpha.png")
writePNG(maskBinary, bytesPerPixel: 1, format: .L8, colorSpace: grayColorSpace, name: "mask.png")
writePNG(previewRGBA, bytesPerPixel: 4, format: .RGBA8, colorSpace: rgbColorSpace, name: "preview.png")

let elapsedSec = Date().timeIntervalSince(startTime)
let isoFormatter = ISO8601DateFormatter()
isoFormatter.formatOptions = [.withInternetDateTime]

let metadata: [String: Any] = [
    "extractor": "apple-vision",
    "refine": NSNull(),
    "decontaminate": false,
    "input": inputURL.path,
    "size": [width, height],
    "elapsedSec": (elapsedSec * 1000).rounded() / 1000,
    "createdAt": isoFormatter.string(from: Date()),
]
let metadataData = try! JSONSerialization.data(withJSONObject: metadata, options: [.prettyPrinted])
try! metadataData.write(to: URL(fileURLWithPath: outDir).appendingPathComponent("metadata.json"))

print(String(format: "抽出完了（Apple Vision）: %.2f秒（インスタンス数=%d）", elapsedSec, observation.allInstances.count))
