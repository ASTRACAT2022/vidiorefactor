import AppKit
import SwiftUI

struct EnhancementPreset: Identifiable, Hashable {
    let id: String
    let title: String
    let detail: String
    let filters: [String]
    let defaultCRF: Int

    static let all: [EnhancementPreset] = [
        EnhancementPreset(
            id: "soft",
            title: "Soft",
            detail: "Мягкая чистка",
            filters: [
                "hqdn3d=1.0:1.0:4:4",
                "eq=contrast=1.03:saturation=1.04",
                "unsharp=5:5:0.35:3:3:0.15",
            ],
            defaultCRF: 19
        ),
        EnhancementPreset(
            id: "balanced",
            title: "Balanced",
            detail: "Оптимально",
            filters: [
                "hqdn3d=1.5:1.5:6:6",
                "eq=contrast=1.06:saturation=1.08",
                "unsharp=5:5:0.55:3:3:0.25",
            ],
            defaultCRF: 18
        ),
        EnhancementPreset(
            id: "strong",
            title: "Strong",
            detail: "Сильная чистка",
            filters: [
                "hqdn3d=2.5:2.5:8:8",
                "eq=contrast=1.08:saturation=1.10",
                "unsharp=7:7:0.75:5:5:0.35",
            ],
            defaultCRF: 17
        ),
        EnhancementPreset(
            id: "upscale",
            title: "Upscale",
            detail: "Увеличение 2x",
            filters: [
                "hqdn3d=1.8:1.8:7:7",
                "eq=contrast=1.05:saturation=1.08",
                "scale=iw*2:ih*2:flags=lanczos",
                "unsharp=5:5:0.45:3:3:0.20",
            ],
            defaultCRF: 18
        ),
    ]

    static func byID(_ id: String) -> EnhancementPreset {
        all.first { $0.id == id } ?? all[1]
    }
}

struct EncodeJob {
    let input: String
    let output: String
    let preset: EnhancementPreset
    let speedMode: String
    let width: String
    let height: String
    let fps: String
    let crf: String
    let codec: String
    let encoderPreset: String
    let bitrate: String
    let copyAudio: Bool
    let removeAudio: Bool
    let overwrite: Bool
}

@MainActor
final class EncoderRunner: ObservableObject {
    @Published var isRunning = false
    @Published var log = ""
    @Published var status = "Готово"

    private var process: Process?

    func preview(job: EncodeJob) {
        do {
            let command = try makeCommand(job: job)
            log = command.map(shellQuote).joined(separator: " ")
            status = "Команда собрана"
        } catch {
            status = "Ошибка"
            log = error.localizedDescription
        }
    }

    func run(job: EncodeJob) {
        do {
            let command = try makeCommand(job: job)
            let fallbackCommand = commandUsesVideoToolbox(command) ? try makeCommand(job: job, forceCPU: true, forceOverwrite: true) : nil
            try start(command: command, fallbackCommand: fallbackCommand, resetLog: true)
        } catch {
            status = "Ошибка"
            log = error.localizedDescription
            isRunning = false
            process = nil
        }
    }

    func cancel() {
        process?.terminate()
        status = "Остановка..."
    }

    private func start(command: [String], fallbackCommand: [String]?, resetLog: Bool) throws {
        let executable = command[0]
        let arguments = Array(command.dropFirst())
        let task = Process()
        let pipe = Pipe()

        task.executableURL = URL(fileURLWithPath: executable)
        task.arguments = arguments
        task.standardOutput = pipe
        task.standardError = pipe

        process = task
        isRunning = true
        status = "Обработка..."
        if resetLog {
            log = command.map(shellQuote).joined(separator: " ") + "\n\n"
        } else {
            log += "\n" + command.map(shellQuote).joined(separator: " ") + "\n\n"
        }

        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in
                self?.log += text
            }
        }

        task.terminationHandler = { [weak self] finishedTask in
            pipe.fileHandleForReading.readabilityHandler = nil
            Task { @MainActor in
                self?.process = nil
                if finishedTask.terminationStatus == 0 {
                    self?.isRunning = false
                    self?.status = "Готово"
                    self?.log += "\nГотово: файл сохранен.\n"
                } else if let fallbackCommand {
                    self?.log += "\nVideoToolbox не запустился, переключаюсь на libx264 veryfast.\n"
                    do {
                        try self?.start(command: fallbackCommand, fallbackCommand: nil, resetLog: false)
                    } catch {
                        self?.isRunning = false
                        self?.status = "Ошибка"
                        self?.log += "\n\(error.localizedDescription)\n"
                    }
                } else {
                    self?.isRunning = false
                    self?.status = "Завершено с ошибкой"
                    self?.log += "\nffmpeg завершился с кодом \(finishedTask.terminationStatus).\n"
                }
            }
        }

        try task.run()
    }

    private func makeCommand(job: EncodeJob, forceCPU: Bool = false, forceOverwrite: Bool = false) throws -> [String] {
        let inputURL = URL(fileURLWithPath: job.input)
        let outputURL = URL(fileURLWithPath: job.output)

        guard !job.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AppError("Выберите входное видео.")
        }
        guard !job.output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AppError("Выберите файл для сохранения.")
        }
        guard FileManager.default.fileExists(atPath: inputURL.path) else {
            throw AppError("Входной файл не найден.")
        }
        guard inputURL.standardizedFileURL.path != outputURL.standardizedFileURL.path else {
            throw AppError("Входной и выходной файл должны отличаться.")
        }
        let outputDirectory = outputURL.deletingLastPathComponent().path
        guard FileManager.default.fileExists(atPath: outputDirectory) else {
            throw AppError("Папка для сохранения не найдена.")
        }
        guard job.overwrite || forceOverwrite || !FileManager.default.fileExists(atPath: outputURL.path) else {
            throw AppError("Выходной файл уже существует. Включите перезапись или выберите другое имя.")
        }
        guard let ffmpeg = findExecutable("ffmpeg") else {
            throw AppError("ffmpeg не найден. Установите его через Homebrew: brew install ffmpeg")
        }

        var filters = job.preset.filters
        if !job.width.isEmpty || !job.height.isEmpty {
            let width = try positiveNumber(job.width, fallback: "-2", name: "Ширина")
            let height = try positiveNumber(job.height, fallback: "-2", name: "Высота")
            filters.removeAll { $0.hasPrefix("scale=") }
            filters.append("scale=\(width):\(height):flags=lanczos")
        }
        if !job.fps.isEmpty {
            filters.append("fps=\(try positiveNumber(job.fps, fallback: "", name: "FPS"))")
        }

        let encoding = encodingSettings(job: job, forceCPU: forceCPU)
        var command = [
            ffmpeg,
            "-hide_banner",
            job.overwrite || forceOverwrite ? "-y" : "-n",
            "-i",
            inputURL.path,
            "-vf",
            filters.joined(separator: ","),
            "-c:v",
            encoding.codec,
        ]

        if isVideoToolbox(encoding.codec) {
            command += [
                "-b:v",
                try bitrateValue(job.bitrate),
                "-allow_sw",
                "true",
            ]
        } else {
            let crf = job.crf.isEmpty ? "\(job.preset.defaultCRF + encoding.crfDelta)" : try crfValue(job.crf)
            command += ["-preset", encoding.encoderPreset, "-crf", crf]
        }

        if job.removeAudio {
            command.append("-an")
        } else {
            command += ["-c:a", job.copyAudio ? "copy" : "aac"]
        }

        command += ["-movflags", "+faststart", outputURL.path]
        return command
    }
}

struct ContentView: View {
    @StateObject private var runner = EncoderRunner()
    @State private var input = ""
    @State private var output = ""
    @State private var selectedPreset = "balanced"
    @State private var speedMode = "fast"
    @State private var width = ""
    @State private var height = ""
    @State private var fps = ""
    @State private var crf = ""
    @State private var codec = "auto"
    @State private var encoderPreset = "auto"
    @State private var bitrate = "12M"
    @State private var copyAudio = true
    @State private var removeAudio = false
    @State private var overwrite = false

    private var preset: EnhancementPreset {
        EnhancementPreset.byID(selectedPreset)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            Form {
                Section {
                    filePickerRow(title: "Видео", path: $input, isOutput: false)
                    filePickerRow(title: "Сохранить как", path: $output, isOutput: true)
                }

                Section {
                    Picker("Пресет", selection: $selectedPreset) {
                        ForEach(EnhancementPreset.all) { preset in
                            Text(preset.title).tag(preset.id)
                        }
                    }
                    .pickerStyle(.segmented)

                    Text(preset.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Picker("Скорость", selection: $speedMode) {
                        Text("Quality").tag("quality")
                        Text("Balanced").tag("balanced")
                        Text("Fast").tag("fast")
                        Text("Turbo Mac").tag("turbo")
                    }
                    .pickerStyle(.segmented)

                    Text(speedModeDetail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section {
                    HStack {
                        labeledTextField("Ширина", text: $width, placeholder: "auto")
                        labeledTextField("Высота", text: $height, placeholder: "auto")
                        labeledTextField("FPS", text: $fps, placeholder: "source")
                        labeledTextField("CRF", text: $crf, placeholder: "\(preset.defaultCRF)")
                        labeledTextField("Битрейт", text: $bitrate, placeholder: "12M")
                    }

                    HStack {
                        Picker("Кодек", selection: $codec) {
                            Text("Auto").tag("auto")
                            Text("H.264 CPU").tag("libx264")
                            Text("H.265 CPU").tag("libx265")
                            Text("H.264 Mac").tag("h264_videotoolbox")
                            Text("H.265 Mac").tag("hevc_videotoolbox")
                        }
                        Picker("CPU preset", selection: $encoderPreset) {
                            ForEach(["auto", "ultrafast", "veryfast", "medium", "slow", "slower", "veryslow"], id: \.self) { value in
                                Text(value).tag(value)
                            }
                        }
                    }

                    Toggle("Копировать аудио", isOn: $copyAudio)
                        .disabled(removeAudio)
                    Toggle("Удалить аудио", isOn: $removeAudio)
                    Toggle("Перезаписывать файл", isOn: $overwrite)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)

            Divider()
            logPanel
            Divider()
            footer
        }
        .frame(minWidth: 760, minHeight: 680)
    }

    private var speedModeDetail: String {
        switch speedMode {
        case "quality":
            return "Лучшее качество, медленнее: CPU H.264, preset slow."
        case "balanced":
            return "Компромисс качества и скорости: CPU H.264, preset medium."
        case "turbo":
            return "Максимальная скорость: аппаратный кодировщик Mac VideoToolbox."
        default:
            return "Быстрее прежнего режима: CPU H.264, preset veryfast."
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Video Refactor")
                    .font(.title2.weight(.semibold))
                Text(runner.status)
                    .font(.callout)
                    .foregroundStyle(runner.isRunning ? .blue : .secondary)
            }
            Spacer()
            if runner.isRunning {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .padding(18)
    }

    private var logPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Лог")
                .font(.headline)
            ScrollView {
                Text(runner.log.isEmpty ? "Лог пуст" : runner.log)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(runner.log.isEmpty ? .secondary : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(10)
            }
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .frame(height: 180)
        .padding(16)
    }

    private var footer: some View {
        HStack {
            Button {
                runner.preview(job: makeJob())
            } label: {
                Label("Показать команду", systemImage: "terminal")
            }
            .disabled(runner.isRunning)

            Spacer()

            if runner.isRunning {
                Button(role: .destructive) {
                    runner.cancel()
                } label: {
                    Label("Остановить", systemImage: "stop.fill")
                }
            }

            Button {
                runner.run(job: makeJob())
            } label: {
                Label("Улучшить видео", systemImage: "wand.and.stars")
            }
            .buttonStyle(.borderedProminent)
            .disabled(runner.isRunning)
        }
        .padding(16)
    }

    private func filePickerRow(title: String, path: Binding<String>, isOutput: Bool) -> some View {
        HStack {
            Text(title)
                .frame(width: 110, alignment: .leading)
            TextField("", text: path)
                .textFieldStyle(.roundedBorder)
            Button {
                isOutput ? chooseOutput(path: path) : chooseInput(path: path)
            } label: {
                Label("Выбрать", systemImage: isOutput ? "square.and.arrow.down" : "film")
            }
        }
    }

    private func labeledTextField(_ title: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
                .frame(width: 120)
        }
    }

    private func chooseInput(path: Binding<String>) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false

        if panel.runModal() == .OK, let url = panel.url {
            path.wrappedValue = url.path
            if output.isEmpty {
                output = suggestedOutputPath(for: url)
            }
        }
    }

    private func chooseOutput(path: Binding<String>) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = output.isEmpty ? "enhanced.mp4" : URL(fileURLWithPath: output).lastPathComponent
        if panel.runModal() == .OK, let url = panel.url {
            path.wrappedValue = url.path
        }
    }

    private func suggestedOutputPath(for inputURL: URL) -> String {
        let directory = inputURL.deletingLastPathComponent()
        let baseName = inputURL.deletingPathExtension().lastPathComponent
        return directory.appendingPathComponent("\(baseName)_enhanced.mp4").path
    }

    private func makeJob() -> EncodeJob {
        EncodeJob(
            input: input,
            output: output,
            preset: preset,
            speedMode: speedMode,
            width: width.trimmingCharacters(in: .whitespacesAndNewlines),
            height: height.trimmingCharacters(in: .whitespacesAndNewlines),
            fps: fps.trimmingCharacters(in: .whitespacesAndNewlines),
            crf: crf.trimmingCharacters(in: .whitespacesAndNewlines),
            codec: codec,
            encoderPreset: encoderPreset,
            bitrate: bitrate.trimmingCharacters(in: .whitespacesAndNewlines),
            copyAudio: copyAudio,
            removeAudio: removeAudio,
            overwrite: overwrite
        )
    }
}

struct AppError: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? {
        message
    }
}

func findExecutable(_ name: String) -> String? {
    let candidates = [
        "/opt/homebrew/bin/\(name)",
        "/usr/local/bin/\(name)",
        "/usr/bin/\(name)",
    ]

    for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
        return candidate
    }

    let path = ProcessInfo.processInfo.environment["PATH"] ?? ""
    for directory in path.split(separator: ":") {
        let candidate = "\(directory)/\(name)"
        if FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
    }

    return nil
}

func positiveNumber(_ text: String, fallback: String, name: String) throws -> String {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
        return fallback
    }
    guard let value = Int(trimmed), value > 0 else {
        throw AppError("\(name): введите положительное число.")
    }
    return "\(value)"
}

func crfValue(_ text: String) throws -> String {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let value = Int(trimmed) else {
        throw AppError("CRF: введите число.")
    }
    guard (0...51).contains(value) else {
        throw AppError("CRF: используйте значение от 0 до 51.")
    }
    return "\(value)"
}

func bitrateValue(_ text: String) throws -> String {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    let value = trimmed.isEmpty ? "12M" : trimmed
    let pattern = #"^[1-9][0-9]*([kKmM])?$"#
    guard value.range(of: pattern, options: .regularExpression) != nil else {
        throw AppError("Битрейт: используйте формат вроде 8000k или 12M.")
    }
    return value
}

func isVideoToolbox(_ codec: String) -> Bool {
    codec == "h264_videotoolbox" || codec == "hevc_videotoolbox"
}

func commandUsesVideoToolbox(_ command: [String]) -> Bool {
    command.contains("h264_videotoolbox") || command.contains("hevc_videotoolbox")
}

func encodingSettings(job: EncodeJob, forceCPU: Bool = false) -> (codec: String, encoderPreset: String, crfDelta: Int) {
    if forceCPU {
        return ("libx264", "veryfast", 2)
    }

    let defaultSettings: (codec: String, encoderPreset: String, crfDelta: Int)
    switch job.speedMode {
    case "quality":
        defaultSettings = ("libx264", "slow", 0)
    case "balanced":
        defaultSettings = ("libx264", "medium", 1)
    case "turbo":
        defaultSettings = ("h264_videotoolbox", "medium", 0)
    default:
        defaultSettings = ("libx264", "veryfast", 2)
    }

    let codec = job.codec == "auto" ? defaultSettings.codec : job.codec
    let encoderPreset = job.encoderPreset == "auto" ? defaultSettings.encoderPreset : job.encoderPreset
    return (codec, encoderPreset, defaultSettings.crfDelta)
}

func shellQuote(_ value: String) -> String {
    if value.rangeOfCharacter(from: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: "'\""))) == nil {
        return value
    }
    return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

@main
struct VideoRefactorApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}
