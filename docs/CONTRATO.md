# Contrato do Ata

Fonte de verdade para quem implementa qualquer módulo. Mudou o contrato, mude este arquivo no mesmo commit.

## 1. Bundle `ata/1` (uma pasta por reunião)

```
<recordings>/AAAA-MM-DD-HHMM[-slug]/
  far.wav            16 kHz mono PCM16 — o que a máquina toca (as outras pessoas)
  mic.wav            16 kHz mono PCM16 — o microfone (você)
  meta.json          BundleMeta (schema "ata/1"), escrito pelo gravador ou pelo import
  STOP               existência pede ao gravador para parar
  recorder.log       log do gravador (sem texto de reunião)
  -- derivados (todos recriáveis a partir do áudio + meta) --
  words.json         {"far": [Word...], "mic": [Word...]}  tempo NO RELÓGIO DO BUNDLE
  turns.json         {"turns": [Turn...], "speakers": {...}}
  summary.json       Summary (schema fixo, §4)
  note.md            nota Markdown no idioma do bundle (também copiada para a pasta de notas)
  speakers.json      {"names": {"Pessoa 2": "Ana"}}  (rótulos -> nomes)
  my-notes.md        anotações do usuário (opcional)
  ask.md             perguntas e respostas sobre a reunião
  live/turns.jsonl   turnos finais do modo ao vivo (um JSON por linha)
  pipeline.log       log do processamento (sem texto de reunião)
  done.json          {"ok": true, "at": ISO, "engines": {...}, "seconds": float}
```

`meta.json`:

```json
{
  "schema": "ata/1",
  "name": "2026-10-05-1642-demo",
  "slug": "demo",
  "title": "Demo",
  "created_at": "2026-10-05T16:42:00-03:00",
  "stopped_at": "2026-10-05T16:44:38-03:00",
  "host": "spark-922b",
  "platform": "linux",
  "recorder": {"name": "ata-linux-pw", "version": "0.1.0"},
  "language": {"requested": "pt-BR", "detected": null},
  "speakers_hint": null,
  "tracks": {
    "far": {"file": "far.wav", "device": "xrdp-sink", "start_epoch": 1791234567.12,
            "start_measured": true, "sample_rate": 16000, "samples": 2531200, "silent": false},
    "mic": {"...": "mesmo formato"}
  },
  "engines": {},
  "damage_reasons": []
}
```

Regras:
- `schema` precisa ser `"ata/1"`; o leitor de compatibilidade (`ata.compat.crunchlog`) converte bundles `bundle_version: 2` do CrunchLog para `BundleMeta` em memória (nunca reescreve o meta original).
- **Relógio do bundle** começa em `min(start_epoch)` das faixas presentes. `track_offsets()` dá o deslocamento de cada faixa. Os motores devolvem tempo do ARQUIVO; só `pipeline.run` soma o offset (um único ponto).
- `start_measured=false` quando o início foi estimado (ex.: `pw-record`): vira o motivo `start_unmeasured` no relatório.
- `damage_reasons` usa vocabulário fechado: `far_missing`, `mic_missing`, `far_silent`, `mic_silent`, `start_unmeasured`, `length_drift`, `start_separation`, `recorder_killed`, `dropped_frames`.
- Escrita do meta é atômica (tmp + replace). Leitura é estrita: tipo errado = `BundleError` com o caminho do campo.

## 2. Tipos (src/ata/types.py)

- `Word(text, start, end, confidence=None)` — segundos.
- `Span(start, end, speaker)` — `speaker` é rótulo do diarizador (`"S0"`, `"S1"`...).
- `Turn(start, end, speaker, text, track, words=[])` — `speaker` final (`"Eu"`, `"Pessoa 2"`, ou nome), `track` ∈ `far|mic`.
- `TrackName = Literal["far", "mic"]`.

Rótulos por idioma (`ata.i18n`): eu = `Eu` / `Me` / `Yo`; outros = `Pessoa N` / `Speaker N` / `Persona N`, N começa em 2.

## 3. Protocolos de motor (src/ata/engines/base.py)

```python
class AsrEngine(Protocol):
    name: str
    def transcribe(self, wav: Path, language: str) -> list[Word]: ...      # tempo do arquivo

class Diarizer(Protocol):
    name: str
    def diarize(self, wav: Path, max_speakers: int = 0) -> list[Span]: ...  # 0 = automático

class SpeakerEmbedder(Protocol):
    name: str
    def embed(self, wav: Path, spans: list[Span]) -> dict[str, list[float]]: ...  # rótulo -> vetor

class Summarizer(Protocol):
    name: str
    def summarize(self, prompt: str, schema: dict) -> dict: ...             # JSON que valida no schema

class TextEmbedder(Protocol):
    name: str
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

class StreamingAsr(Protocol):                                               # modo ao vivo
    name: str
    def open(self, language: str, sample_rate: int = 16000) -> "StreamSession": ...
```

Implementações registradas por nome em `ata.engines.registry`:
`fake` (determinístico, para testes e `ATA_ENGINES=fake`), `nemo` (sidecar NeMo-Speech.cpp HTTP/WS em 127.0.0.1),
`onnx` (onnx-asr + sherpa-onnx, CPU), `ollama`, `claude` (`claude -p`), `codex` (`codex exec`).
`ATA_ENGINES=fake` força todos os papéis para `fake`.

## 4. Summary (schema fixo, todos os provedores)

```json
{
  "language": "pt-BR",
  "tldr": "uma ou duas frases",
  "topics":    [{"title": "...", "start": 12.0, "summary": "..."}],
  "decisions": [{"text": "...", "evidence": [{"t": 73.0, "speaker": "Eu"}]}],
  "actions":   [{"text": "...", "owner": "Pessoa 3", "due": "próxima quarta", "evidence": [...]}],
  "questions": [{"text": "...", "evidence": [...]}]
}
```

## 5. Configuração (`~/.config/ata/config.toml`, ou `$ATA_CONFIG`, ou `--config`)

Seções: `[paths] recordings notes models cache`, `[language] default`, `[asr] pt-BR en es` (ref `motor:modelo`),
`[diarization] engine max_speakers mic_speakers`, `[gate] enabled margin_db`, `[cleanup] fillers.<lang> glossary`,
`[summary] provider (ollama|claude|codex|none) ollama_model claude_model codex_model timeout_seconds`,
`[embeddings] provider model`, `[engine] prefer (auto|nemo|onnx) host port`, `[live] enabled engine`,
`[voices] enabled threshold`, `[dashboard] port`, `[cerebro] dir`, `[obsidian] vault`, `[privacy] level`.
Padrões em `ata.config.DEFAULTS`. Variáveis `ATA_*` são só para teste/override pontual.

## 6. Códigos de saída do CLI

`0` ok · `1` falha de execução · `2` uso inválido · `3` nada a fazer (ex.: já gravando / nada gravando) · `4` dependência ausente (motor, PipeWire, CLI).

## 7. Privacidade (invariantes testados)

- Logs, erros, `doctor` e `pipeline.log` nunca contêm texto de reunião.
- Nenhum módulo chama API paga. Saída de texto só via `claude`/`codex` CLI (assinatura) quando o usuário escolhe; `meta.json.engines` registra quem tocou o bundle; `ata privacy <bundle>` mostra.
- Voiceprints: opt-in (`[voices] enabled = false` padrão), locais em `<cache>/voices.json`, apagáveis (`ata voices forget`).
