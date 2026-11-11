"""Prepare two user-supplied PCM WAV assets offline; never plays or uploads audio."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
UP, DOWN = 147, 160
HALF_WIDTH, BETA, CUTOFF = 128, 10.0, 0.96 * 44100.0 / 48000.0


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_pcm(path):
    with wave.open(str(path), "rb") as wav:
        channels, width, rate, frames, compression, _ = wav.getparams()
        if (channels, width, rate, compression) != (2, 2, 48000, "NONE"):
            raise SystemExit("Expected PCM16 little-endian 48 kHz stereo: " + str(path))
        samples = np.frombuffer(wav.readframes(frames), dtype="<i2").reshape(frames, 2)
    return samples


def phase_bank():
    offsets = np.arange(-HALF_WIDTH, HALF_WIDTH + 1, dtype=np.int64)
    distance = np.arange(UP, dtype=np.float64)[:, None] / UP - offsets[None, :]
    inside = np.abs(distance) <= HALF_WIDTH
    window = np.i0(BETA * np.sqrt(np.maximum(0.0, 1.0 - (distance / HALF_WIDTH) ** 2))) / np.i0(BETA)
    coefficients = CUTOFF * np.sinc(CUTOFF * distance) * window * inside
    # Unit DC response per phase is filter design, not recording gain normalization.
    coefficients /= coefficients.sum(axis=1, keepdims=True)
    return offsets, coefficients


def resample(samples, offsets, coefficients):
    count = (len(samples) * UP + DOWN // 2) // DOWN
    padded = np.pad(samples.astype(np.float64), ((HALF_WIDTH, HALF_WIDTH), (0, 0)))
    output = np.empty((count, 2), dtype=np.float64)
    for first in range(0, count, 1024):
        product = np.arange(first, min(first + 1024, count), dtype=np.int64) * DOWN
        base, phase = product // UP, product % UP
        indices = base[:, None] + offsets[None, :] + HALF_WIDTH
        output[first:first + len(product)] = np.einsum("nt,ntc->nc", coefficients[phase], padded[indices], optimize=False)
    rounded = np.rint(output)
    if np.any(rounded < -32768) or np.any(rounded > 32767):
        raise SystemExit("Resampling would clip; refusing to change gain or silently clip.")
    return rounded.astype("<i2")


def stats(samples, rate):
    values = samples.astype(np.float64)
    amplitude = np.abs(values).max(axis=1)
    crossing = {}
    for threshold in (0, 8, 16, 32, 128, 256):
        frames = np.flatnonzero(amplitude > threshold)
        crossing[str(threshold)] = int(frames[0]) if len(frames) else None
    zero_frames = crossing["0"] if crossing["0"] is not None else len(samples)
    return {"frames": len(samples), "sample_rate_hz": rate, "duration_seconds": len(samples) / rate,
            "peak_pcm_per_channel": np.abs(values).max(axis=0).tolist(),
            "rms_pcm_per_channel": np.sqrt(np.mean(values * values, axis=0)).tolist(),
            "leading_all_channel_exact_zero_frames": zero_frames,
            "leading_all_channel_exact_zero_seconds": zero_frames / rate,
            "first_frame_above_abs_pcm_threshold": crossing,
            "first_seconds_above_abs_pcm_threshold": {key: None if value is None else value / rate
                                                       for key, value in crossing.items()}}


def archive_current(label):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", label) or label in (".", ".."):
        raise SystemExit("Unsafe archive label")
    destination = ROOT / "private-media/archives" / label
    if destination.exists():
        raise SystemExit("Archive already exists; refusing to replace: " + str(destination))
    inputs = [*(ROOT / "private-media/originals").glob("*.wav"),
              *(ROOT / "app/src/main/assets").glob("*.wav"),
              ROOT / "private-media/media-receipt.json", ROOT / "app/AndroidManifest.xml"]
    if len(inputs) != 6 or any(not path.is_file() for path in inputs):
        raise SystemExit("Expected two current originals, two assets, media receipt and manifest")
    destination.mkdir(parents=True, exist_ok=False)
    records = {}
    for source in inputs:
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(source) != sha256(target):
            raise SystemExit("Archive byte verification failed: " + str(source))
        records[relative.as_posix()] = {"sha256": sha256(target), "bytes": target.stat().st_size}
    (destination / "archive-receipt.json").write_text(json.dumps({"schema": 1, "version_label": label,
        "immutable_archive": True, "files": records}, indent=2) + "\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav-1", required=True, type=Path)
    parser.add_argument("--wav-2", required=True, type=Path)
    parser.add_argument("--archive-current", help="Archive this active version before accepting replaced source WAVs")
    args = parser.parse_args()
    # Validate both new inputs before archiving or replacing active media.
    for index in (1, 2):
        read_pcm(getattr(args, "wav_%d" % index))
    archived = archive_current(args.archive_current) if args.archive_current else None
    originals = ROOT / "private-media/originals"
    assets = ROOT / "app/src/main/assets"
    originals.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    offsets, coefficients = phase_bank()
    records = []
    for index in (1, 2):
        name = "original_%02d.wav" % index
        source, preserved = getattr(args, "wav_%d" % index), originals / name
        if not source.is_file():
            raise SystemExit("Missing private source: " + str(source))
        replaced = preserved.exists() and sha256(preserved) != sha256(source)
        if replaced and archived is None:
            raise SystemExit("Preserved original differs; refusing to replace: " + str(preserved))
        if not preserved.exists() or replaced:
            shutil.copy2(source, preserved)
        assert sha256(source) == sha256(preserved)
        samples = read_pcm(preserved)
        output = resample(samples, offsets, coefficients)
        target = assets / ("chime_%02d.wav" % index)
        with wave.open(str(target), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(output.tobytes(order="C"))
        with wave.open(str(target), "rb") as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 2, 44100, len(output))
        records.append({"source_name": source.name, "original": str(preserved.relative_to(ROOT)).replace("\\", "/"),
                        "original_sha256": sha256(preserved), "original_bytes": preserved.stat().st_size,
                        "input": stats(samples, 48000),
                        "asset": str(target.relative_to(ROOT)).replace("\\", "/"), "asset_sha256": sha256(target),
                        "asset_bytes": target.stat().st_size,
                        "output": stats(output, 44100)})
    receipt = {"schema": 1, "status": "prepared_offline", "originals_unchanged": True, "audio_played": False,
               "format": "PCM16 little-endian stereo WAV", "gain_adjustment_db": 0, "normalization": False,
               "trimmed_frames": 0, "leading_silence_or_low_level_signal_preserved": True,
               "previous_version_archive": None if archived is None else archived.relative_to(ROOT).as_posix(),
               "clipping": False, "resampler": {"implementation": "NumPy float64 rational windowed-sinc v1",
                    "ratio": "147/160", "phase_count": UP, "half_width_input_samples": HALF_WIDTH,
                    "kaiser_beta": BETA, "cutoff_hz": CUTOFF * 48000 / 2, "edge_extension": "zeros",
                    "chunk_output_frames": 1024, "rounding": "nearest, ties to even", "numpy": np.__version__,
                    "filter_coefficients_sha256": hashlib.sha256(coefficients.astype("<f8").tobytes()).hexdigest()},
               "files": records}
    destination = ROOT / "private-media/media-receipt.json"
    destination.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["asset"] + " " + record["asset_sha256"])
    print("Private originals and media receipt: " + str(destination))


if __name__ == "__main__":
    main()
