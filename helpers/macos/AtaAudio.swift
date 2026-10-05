// ata-audio — helper de captura do Ata para macOS 14.2+ (Core Audio process tap + microfone).
//
// Contrato com ata.capture.macos (um JSON por linha no stdout; stderr só diagnóstico):
//   ata-audio record --far <far.wav> --mic <mic.wav> [--far-device UID] [--mic-device UID]
//     {"event":"started","track":"far","start_epoch":1791234567.123,"device":"..."}
//     {"event":"level","track":"far","db":-23.5}                     (1x por segundo por faixa)
//     {"event":"error","track":"far","code":"permission_denied","message":"..."}
//     {"event":"stopped","track":"far","samples":2531200}
//   ata-audio devices
//     {"event":"devices","far":{"id":UID,"description":nome},"mic":{"id":UID,"description":nome}}
// Parada: SIGINT/SIGTERM -> fecha os WAVs, emite "stopped" para cada faixa, sai 0.
// Saída: far.wav e mic.wav 16 kHz mono PCM16. start_epoch vem do mHostTime do 1º buffer de cada faixa
// convertido para epoch com o MESMO relógio do host (as duas faixas ficam alinhadas -> start_measured).
// Permissão negada NUNCA é silenciosa: emite error/permission_denied e segue gravando a outra faixa.
//
// Compilar: ver README.md ao lado (swiftc -O -framework CoreAudio -framework AVFoundation ...).

import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

// MARK: - saída JSON

let outLock = NSLock()

func emit(_ fields: [String: Any]) {
    outLock.lock(); defer { outLock.unlock() }
    guard let data = try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys]),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

func fail(_ code: String, track: String? = nil, _ message: String) {
    var d: [String: Any] = ["event": "error", "code": code, "message": message]
    if let track { d["track"] = track }
    emit(d)
}

// MARK: - relógio do host -> epoch

func epochFromHostTime(_ hostTime: UInt64) -> Double {
    let nowHost = AudioGetCurrentHostTime()
    let nowEpoch = Date().timeIntervalSince1970
    let deltaNs = Double(AudioConvertHostTimeToNanos(nowHost)) - Double(AudioConvertHostTimeToNanos(hostTime))
    return nowEpoch - deltaNs / 1e9
}

// MARK: - escrita 16 kHz mono PCM16

let targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

final class TrackWriter {
    let name: String
    let file: AVAudioFile
    var converter: AVAudioConverter?
    var samples: Int64 = 0
    var started = false
    var sumSquares: Double = 0
    var levelCount: Int = 0
    let lock = NSLock()

    init(name: String, path: String) throws {
        self.name = name
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
        ]
        file = try AVAudioFile(forWriting: URL(fileURLWithPath: path), settings: settings,
                               commonFormat: .pcmFormatInt16, interleaved: true)
    }

    /// Converte um buffer do dispositivo para 16 kHz mono e grava. `hostTime` = mHostTime do buffer.
    func append(_ buffer: AVAudioPCMBuffer, hostTime: UInt64, device: String) {
        lock.lock(); defer { lock.unlock() }
        if !started {
            started = true
            emit(["event": "started", "track": name, "start_epoch": epochFromHostTime(hostTime), "device": device])
        }
        if converter == nil || converter!.inputFormat != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: targetFormat)
        }
        guard let converter else { return }
        let ratio = 16000.0 / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if consumed { status.pointee = .noDataNow; return nil }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        if error != nil || out.frameLength == 0 { return }
        do { try file.write(from: out) } catch {
            fail("write_failed", track: name, "falha ao gravar \(name).wav"); return
        }
        samples += Int64(out.frameLength)
        if let p = out.int16ChannelData?[0] {
            for i in 0..<Int(out.frameLength) { let v = Double(p[i]) / 32768.0; sumSquares += v * v }
            levelCount += Int(out.frameLength)
        }
        if levelCount >= 16000 {
            let rms = (sumSquares / Double(levelCount)).squareRoot()
            emit(["event": "level", "track": name, "db": rms > 0 ? 20 * log10(rms) : -120.0])
            sumSquares = 0; levelCount = 0
        }
    }
}

// MARK: - Core Audio helpers

func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioObjectID {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var id = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &id)
    return id
}

func stringProperty(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &value) == noErr, let v = value else { return "" }
    return v.takeRetainedValue() as String
}

func tapFormat(_ tap: AudioObjectID) -> AudioStreamBasicDescription? {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var asbd = AudioStreamBasicDescription()
    var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    return AudioObjectGetPropertyData(tap, &addr, 0, nil, &size, &asbd) == noErr ? asbd : nil
}

// MARK: - captura do sistema (far) via process tap

final class SystemTap {
    var tapID = AudioObjectID(kAudioObjectUnknown)
    var aggregateID = AudioObjectID(kAudioObjectUnknown)
    var procID: AudioDeviceIOProcID?
    let writer: TrackWriter
    let queue = DispatchQueue(label: "ata.tap", qos: .userInitiated)

    init(writer: TrackWriter) { self.writer = writer }

    /// false = falhou (já emitiu o erro). Permissão "Gravação de áudio do sistema" negada -> permission_denied.
    func start() -> Bool {
        let desc = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        desc.uuid = UUID()
        desc.isPrivate = true
        desc.muteBehavior = .unmuted
        var status = AudioHardwareCreateProcessTap(desc, &tapID)
        guard status == noErr else {
            fail("permission_denied", track: "far", "AudioHardwareCreateProcessTap: OSStatus \(status)")
            return false
        }
        let output = defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice)
        let outputUID = stringProperty(output, kAudioDevicePropertyDeviceUID)
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Ata Tap",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                               kAudioSubTapUIDKey: desc.uuid.uuidString]],
        ]
        status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
        guard status == noErr, var asbd = tapFormat(tapID),
              let format = AVAudioFormat(streamDescription: &asbd) else {
            fail("tap_failed", track: "far", "agregado do tap: OSStatus \(status)")
            return false
        }
        let deviceName = stringProperty(output, kAudioObjectPropertyName)
        status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) { [weak self] _, inInput, inTime, _, _ in
            guard let self,
                  let buffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: inInput, deallocator: nil)
            else { return }
            self.writer.append(buffer, hostTime: inTime.pointee.mHostTime, device: "System Audio (\(deviceName))")
        }
        guard status == noErr else { fail("tap_failed", track: "far", "IOProc: OSStatus \(status)"); return false }
        status = AudioDeviceStart(aggregateID, procID)
        guard status == noErr else { fail("tap_failed", track: "far", "AudioDeviceStart: OSStatus \(status)"); return false }
        return true
    }

    func stop() {
        if let procID {
            AudioDeviceStop(aggregateID, procID)
            AudioDeviceDestroyIOProcID(aggregateID, procID)
        }
        if aggregateID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggregateID) }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
    }
}

// MARK: - microfone (mic) via AVAudioEngine

final class Microphone {
    let engine = AVAudioEngine()
    let writer: TrackWriter
    init(writer: TrackWriter) { self.writer = writer }

    func start() -> Bool {
        let auth = AVCaptureDevice.authorizationStatus(for: .audio)
        if auth == .notDetermined {
            let sem = DispatchSemaphore(value: 0)
            AVCaptureDevice.requestAccess(for: .audio) { _ in sem.signal() }
            sem.wait()
        }
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            fail("permission_denied", track: "mic", "acesso ao microfone negado")
            return false
        }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        let name = AVCaptureDevice.default(for: .audio)?.localizedName ?? "Microfone"
        input.installTap(onBus: 0, bufferSize: 1600, format: format) { [weak self] buffer, time in
            self?.writer.append(buffer, hostTime: time.hostTime, device: name)
        }
        do { try engine.start() } catch {
            fail("device_missing", track: "mic", "microfone não iniciou")
            return false
        }
        return true
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }
}

// MARK: - main

func arg(_ name: String, _ args: [String]) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

let args = Array(CommandLine.arguments.dropFirst())

guard #available(macOS 14.2, *) else {
    fail("unsupported_os", "o ata-audio precisa do macOS 14.2 ou mais novo (Core Audio process tap)")
    exit(3)
}

if args.first == "devices" {
    let out = defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice)
    let inp = defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
    emit(["event": "devices",
          "far": ["id": stringProperty(out, kAudioDevicePropertyDeviceUID),
                  "description": stringProperty(out, kAudioObjectPropertyName), "source": "default"],
          "mic": ["id": stringProperty(inp, kAudioDevicePropertyDeviceUID),
                  "description": stringProperty(inp, kAudioObjectPropertyName), "source": "default"]])
    exit(0)
}

guard args.first == "record", let farPath = arg("--far", args), let micPath = arg("--mic", args) else {
    FileHandle.standardError.write("uso: ata-audio record --far F.wav --mic M.wav | ata-audio devices\n".data(using: .utf8)!)
    exit(2)
}
// --far-device / --mic-device: reservados (v1 usa o tap global e o microfone padrão do sistema).

let farWriter: TrackWriter
let micWriter: TrackWriter
do {
    farWriter = try TrackWriter(name: "far", path: farPath)
    micWriter = try TrackWriter(name: "mic", path: micPath)
} catch {
    fail("write_failed", "não consegui criar os arquivos WAV")
    exit(1)
}

let tap = SystemTap(writer: farWriter)
let mic = Microphone(writer: micWriter)
let farOK = tap.start()
let micOK = mic.start()
if !farOK && !micOK {
    emit(["event": "stopped"])
    exit(1)
}

func shutdown() {
    tap.stop()
    mic.stop()
    for w in [farWriter, micWriter] {
        w.lock.lock()
        emit(["event": "stopped", "track": w.name, "samples": w.samples])
        w.lock.unlock()
    }
    exit(0)
}

signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigint.setEventHandler(handler: shutdown)
sigterm.setEventHandler(handler: shutdown)
sigint.resume()
sigterm.resume()
dispatchMain()
