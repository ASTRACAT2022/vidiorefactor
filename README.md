# Video Refactor

Video Refactor - приложение для macOS и CLI-утилита для быстрого улучшения видео через `ffmpeg`.

Проект помогает почистить шумы, слегка усилить контраст и насыщенность, добавить резкость, сделать апскейл и перекодировать видео в удобный формат. Для macOS есть нативный SwiftUI-интерфейс, а для автоматизации - Python CLI.

## Возможности

- Нативное macOS-приложение с GUI.
- CLI-режим для терминала и пакетной обработки.
- Пресеты улучшения: `soft`, `balanced`, `strong`, `upscale`.
- Шумоподавление через `hqdn3d`.
- Цветокоррекция через `eq`.
- Повышение резкости через `unsharp`.
- Апскейл через `lanczos`.
- Быстрые режимы кодирования: `quality`, `balanced`, `fast`, `turbo`.
- Поддержка аппаратного VideoToolbox-кодирования на macOS с fallback на быстрый CPU-режим.
- Настройки ширины, высоты, FPS, CRF, битрейта, кодека и аудио.

## Требования

- macOS 12 или новее для GUI.
- Python 3 для CLI.
- `ffmpeg`.

Установка `ffmpeg`:

```bash
brew install ffmpeg
```

## macOS GUI

Готовое приложение после сборки находится здесь:

```bash
dist/Video Refactor.app
```

Сборка:

```bash
./build_mac_app.sh
```

В приложении можно выбрать входное видео, путь сохранения, пресет улучшения, режим скорости, размер, FPS, качество, кодек и аудио.

Режимы скорости:

- `Quality` - медленнее, лучше качество.
- `Balanced` - компромисс качества и скорости.
- `Fast` - быстрый CPU-режим.
- `Turbo Mac` - попытка аппаратного кодирования через macOS VideoToolbox.

Если `Turbo Mac` не запускается на конкретном видео или системе, приложение автоматически переключается на `libx264 veryfast`.

## CLI

Базовый запуск:

```bash
python3 video_enhancer.py input.mp4 output.mp4
```

Примеры:

```bash
python3 video_enhancer.py input.mp4 output.mp4 --preset soft
python3 video_enhancer.py input.mp4 output.mp4 --preset strong
python3 video_enhancer.py input.mp4 output_4k.mp4 --width 3840
python3 video_enhancer.py input.mp4 output_upscale.mp4 --preset upscale
python3 video_enhancer.py input.mp4 output_fast.mp4 --speed fast
python3 video_enhancer.py input.mp4 output_turbo.mp4 --speed turbo --bitrate 12M
python3 video_enhancer.py input.mp4 output.mp4 --dry-run
```

Пресеты:

- `soft` - мягкая чистка для нормального исходника.
- `balanced` - стандартный вариант по умолчанию.
- `strong` - сильнее убирает шум и сжатие, но может сделать картинку менее естественной.
- `upscale` - улучшение плюс увеличение в 2 раза.

Скорость:

- `quality` - CPU H.264, `slow`.
- `balanced` - CPU H.264, `medium`.
- `fast` - CPU H.264, `veryfast`; используется по умолчанию.
- `turbo` - macOS VideoToolbox; при ошибке автоматически fallback на `libx264 veryfast`.

Качество CPU-кодирования задается через `--crf`: меньше значение означает лучше качество и больше размер файла. Хороший диапазон для H.264: `17-22`.

В режиме `turbo` качество задается битрейтом:

```bash
python3 video_enhancer.py input.mp4 output.mp4 --speed turbo --bitrate 12M
```

## Ограничения

Video Refactor использует классические фильтры `ffmpeg`. Он может улучшить вид видео, убрать часть шума, сделать картинку резче и приятнее, но не восстанавливает настоящие потерянные детали как AI-super-resolution.

Для AI-дорисовки можно добавить отдельный pipeline с обработкой кадров через ML-модель, но это будет тяжелее и медленнее.

## Структура проекта

```text
MacApp/
  Info.plist
  VideoRefactorApp.swift
video_enhancer.py
build_mac_app.sh
README.md
```
