"""`pw-record` falso para os testes do backend PipeWire (rodado como ``python fake_pw_record.py <argv...>``).

Plano em JSON na env ``FAKE_PW_PLAN``:
  mode: "ok" (padrão) | "die" (sai na hora com erro) | "ignore_sigint" (só sai com SIGKILL)
  seconds: duração do áudio escrito (3.0)
  far: "noise" | "silence"     mic: "bleed" (cópia atrasada do far) | "own" (ruído independente) | "silence"
  mic_delay: atraso do vazamento no mic, s (0.15)     lead: silêncio no início, s (1.5)
  argv_dir: se dado, grava o argv recebido em <argv_dir>/<far|mic>.json
O WAV sai com tamanhos RIFF/data ZERADOS (como um gravador que morreu) para exercitar audio.repair_header.
"""

import json
import os
import signal
import struct
import sys
import time

import numpy as np

RATE = 16000


def header() -> bytes:
    return (b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", 0))


def noise(n: int, seed: int, lead: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n).astype(np.float32) * 0.2
    t = np.arange(n) / RATE
    env = (np.sin(2 * np.pi * 3.0 * t) > 0).astype(np.float32)   # rajadas tipo fala
    x *= env
    x[: int(lead * RATE)] = 0.0
    return x


def main() -> int:
    argv = sys.argv[1:]
    plan = json.loads(os.environ.get("FAKE_PW_PLAN") or "{}")
    monitor = "stream.capture.sink=true" in argv
    track = "far" if monitor else "mic"
    if plan.get("argv_dir"):
        with open(os.path.join(plan["argv_dir"], f"{track}.json"), "w") as f:
            json.dump(argv, f)
    mode = plan.get("mode", "ok")
    if mode == "die" or plan.get(f"die_{track}"):
        print(f"falha ao conectar no alvo {argv[argv.index('--target') + 1]}", file=sys.stderr)
        return 2
    if mode == "ignore_sigint":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    secs = float(plan.get("seconds", 3.0))
    n = int(secs * RATE)
    lead = float(plan.get("lead", 1.5))
    base = noise(n, 7, lead)
    if track == "far":
        kind = plan.get("far", "noise")
        x = base if kind == "noise" else np.zeros(n, np.float32)
    else:
        kind = plan.get("mic", "bleed")
        if kind == "bleed":
            k = int(float(plan.get("mic_delay", 0.15)) * RATE)
            x = np.concatenate([np.zeros(k, np.float32), base])[:n] * 0.3
        elif kind == "own":
            x = noise(n, 99, lead)
        else:
            x = np.zeros(n, np.float32)
    out = argv[-1]
    with open(out, "wb") as f:
        f.write(header())
        f.write(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())
        f.flush()
    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
