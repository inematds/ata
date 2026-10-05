# ata-audio (helper de captura para macOS)

Grava o áudio do sistema (as outras pessoas da call) por um **Core Audio process tap** (macOS 14.2+) e o
microfone pelo `AVAudioEngine`, em `far.wav` e `mic.wav` (16 kHz, mono, PCM16). Fala com o Ata por eventos
JSON, um por linha, no stdout. O contrato está no topo de `AtaAudio.swift` e em `src/ata/capture/macos.py`.

## Compilar

Precisa do Xcode 15.3+ (ou Command Line Tools com o SDK do macOS 14.2+):

```sh
cd helpers/macos
swiftc -O -parse-as-library -target arm64-apple-macos14.2 \
  -framework CoreAudio -framework AudioToolbox -framework AVFoundation \
  AtaAudio.swift -o ata-audio
# Intel: troque o target por x86_64-apple-macos14.2
```

Se o compilador reclamar de `@main`/top-level code, tire o `-parse-as-library` (o arquivo usa código de topo).

Depois ponha o binário no PATH (por exemplo `~/.local/bin/ata-audio`) ou aponte o Ata para ele:

```sh
export ATA_AUDIO_HELPER=~/caminho/ata-audio
# ou no ~/.config/ata/config.toml:
# [audio]
# macos_helper = "~/caminho/ata-audio"
```

## Permissões

- **Gravação de áudio do sistema**: Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela e Áudio
  do Sistema > "Somente áudio do sistema" — libere o Terminal (ou o app que roda o `ata`).
- **Microfone**: Ajustes do Sistema > Privacidade e Segurança > Microfone.

Para o macOS associar a permissão ao helper, assine o binário (ad hoc já serve) e, se for empacotar como app,
inclua `NSAudioCaptureUsageDescription` e `NSMicrophoneUsageDescription` no `Info.plist`:

```sh
codesign --force --sign - ata-audio
```

Permissão negada nunca vira falha muda: o helper emite
`{"event":"error","track":"far","code":"permission_denied"}`, o Ata grava a faixa como silêncio, marca
`far_silent` no bundle e mostra onde liberar.

## Testar à mão

```sh
./ata-audio devices
./ata-audio record --far /tmp/far.wav --mic /tmp/mic.wav   # Ctrl+C para parar
```

Status: fonte de referência, **não compilado nem testado no CI** (o CI roda no Linux). Os testes do Ata
cobrem só o parser de eventos (`tests/capture/test_capture_macos.py`).
