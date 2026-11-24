"""Stream verified DEV112P frame groups from an ONELAB recording in progress."""

from __future__ import annotations

import argparse
from base64 import b64decode, b64encode
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import sys
from threading import Event
import time


SCHEMA = "dev112p-bc-frame/0.1"
HEADER = b"ICLH"
TAIL = b"ICLT"
CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Profile:
    chips: int
    chirps: int
    bins: int
    first_bin: int
    frame_period_s: float
    tx_polling: int

    @classmethod
    def from_ini(cls, content):
        def field(name):
            value = re.search(rb"(?mi)^" + name + rb"[ \t]*=[ \t]*([^\r\n;]+)", content)
            if value is None:
                raise ValueError(f"Missing ONELAB field {name.decode()}")
            return value[1].strip()

        if field(b"DataType").lower() != b"1dfft":
            raise ValueError("Expected an ONELAB 1DFFT recording")
        profile = cls(int(field(b"ChipCount")), int(field(b"ChirpsPerFrame")),
                      int(field(b"1DFFTOutputCols")), int(field(b"1DFFTOutputStartCol")),
                      float(field(b"FrameTimeUS")) / 1_000_000, int(field(b"TxPolling")))
        if not (1 <= profile.chips <= 4 and 1 <= profile.chirps <= 512 and
                1 <= profile.bins <= 1024 and 0 < profile.frame_period_s < float("inf")):
            raise ValueError("Unsupported packet dimensions or frame period")
        return profile

    @property
    def positions(self):
        return self.chips * 2 * self.chirps


def json_line(value):
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(json_line(value))
    temporary.replace(path)


def next_recording(root, previous, stop):
    while not stop.is_set():
        candidates = [
            path for path in root.glob("*/RadarData_1.dat")
            if path not in previous and path.with_name("FILE_Radar_Config.ini").is_file()
        ]
        if len(candidates) > 1:
            raise ValueError("发现多份新录制，请停止本轮并重新启动一次")
        if candidates:
            try:
                with candidates[0].open("rb"):
                    return candidates[0].parent
            except (FileNotFoundError, PermissionError):
                # ONELAB can still be creating the file or holding it open.
                pass
        stop.wait(0.1)
    return None


@dataclass(frozen=True)
class Observation:
    ns: int
    utc: str

    @classmethod
    def now(cls):
        return cls(time.monotonic_ns(), datetime.now(timezone.utc).isoformat())


class FrameGroup:
    def __init__(self, profile, frame_id, counter, observation):
        self.frame_id = frame_id
        self.counter = counter
        self.observation = observation
        self.raw_data = bytearray(profile.positions * profile.bins * 4)
        self.mask = bytearray(profile.positions)
        self.valid_count = 0
        self.reasons = set()
        self.emitted = False


class FrameAssembler:
    def __init__(self, profile, recording_id, emit, log):
        self.profile = profile
        self.recording_id = recording_id
        self.emit = emit
        self.log = log
        self.buffer = bytearray()
        self.frame = None
        self.frame_id = 0
        self.tick = 0
        self.origin_ns = None
        self.counts = Counter()
        self.samples_be = struct.Struct(f">{profile.bins * 2}h")
        self.samples_le = struct.Struct(f"<{profile.bins * 2}h")
        self.packet_size = self.samples_be.size + 8

    def report(self, kind, **fields):
        self.log(kind, frame_id=self.frame_id, **fields)

    def finish_frame(self, reason=None):
        group = self.frame
        if group is None or group.emitted:
            return
        if reason is not None:
            group.reasons.add(reason)
        complete = group.valid_count == self.profile.positions
        if not complete:
            group.reasons.add("missing_chirps")
        self.emit({
            "schema_version": SCHEMA,
            "recording_id": self.recording_id,
            "frame_id": group.frame_id,
            "device_frame_id": group.counter,
            "timestamp": self.tick * self.profile.frame_period_s,
            "timestamp_source": "nominal_device_counter",
            "host_observed_s": (group.observation.ns - self.origin_ns) / 1e9,
            "host_observed_utc": group.observation.utc,
            "raw_data": b64encode(group.raw_data).decode("ascii"),
            "valid_chirps": b64encode(group.mask).decode("ascii"),
            "integrity_status": "complete" if complete else "partial",
            "integrity_reasons": sorted(group.reasons),
            "valid_chirp_count": group.valid_count,
        })
        group.emitted = True
        self.counts["complete_frames" if complete else "partial_frames"] += 1
        self.frame_id += 1

    def accept_packet(self, packet, observation):
        tag, high, low = packet[1:4]
        chip, counter = packet[-2] & 3, packet[-2] >> 4
        rx = tag >> 7
        chirp = (((high >> 3) & 1) << 8) | ((tag & 15) << 4) | (high >> 4)
        if (packet[0] != 0xAA or packet[-1] != 0x55 or ((tag >> 4) & 7) != 3 or
                ((high & 7) << 8) + low != self.profile.bins + 1 or
                chip >= self.profile.chips or chirp >= self.profile.chirps):
            self.counts["invalid_packets"] += 1
            self.report("invalid_packet", reason="header_or_position")
            return
        samples = self.samples_be.unpack_from(packet, 4)
        if (sum(samples) & 0xFFFF) != int.from_bytes(packet[-4:-2], "big"):
            self.counts["invalid_packets"] += 1
            self.report("invalid_packet", reason="checksum")
            return
        if self.frame is None or self.frame.counter != counter:
            if self.frame is not None:
                previous = self.frame.counter
                self.finish_frame()
                step = (counter - previous) % 16
                self.tick += step
                if step > 1:
                    self.counts["minimum_missing_groups"] += step - 1
                    self.report("counter_gap", from_counter=previous, to_counter=counter,
                                minimum_missing_groups=step - 1)
            else:
                self.origin_ns = observation.ns
            self.frame = FrameGroup(self.profile, self.frame_id, counter, observation)

        group = self.frame
        position = (chip * 2 + rx) * self.profile.chirps + chirp
        if group.mask[position]:
            self.counts["duplicate_packets"] += 1
            group.reasons.add("duplicate_packet")
            self.log("duplicate_packet", frame_id=group.frame_id, device_frame_id=counter,
                     chip=chip, rx=rx, chirp=chirp, group_already_emitted=group.emitted)
        elif group.emitted:
            self.counts["late_packets"] += 1
            self.log("late_packet", frame_id=group.frame_id, device_frame_id=counter,
                     chip=chip, rx=rx, chirp=chirp)
        else:
            self.samples_le.pack_into(group.raw_data, position * self.samples_le.size, *samples)
            group.mask[position] = 1
            group.valid_count += 1
            self.counts["valid_packets"] += 1
            if group.valid_count == self.profile.positions:
                self.finish_frame()

    def feed(self, chunk, observation):
        self.buffer.extend(chunk)
        cursor = discarded = 0
        while cursor < len(self.buffer):
            start = self.buffer.find(HEADER, cursor)
            if start < 0:
                end = max(cursor, len(self.buffer) - (len(HEADER) - 1))
                discarded += end - cursor
                cursor = end
                break
            discarded += start - cursor
            cursor = start
            if len(self.buffer) - start < 8:
                break
            length, kind = struct.unpack_from("<HB", self.buffer, start + 4)
            end = start + 8 + length
            if kind == 0 and length != self.packet_size:
                self.counts["invalid_wrappers"] += 1
                self.report("invalid_wrapper", reason="fft_packet_length", packet_length=length)
            elif end + 4 > len(self.buffer):
                break
            elif self.buffer[end:end + 4] == TAIL:
                if kind == 0:
                    self.accept_packet(self.buffer[start + 8:end], observation)
                else:
                    self.counts["aux_wrappers"] += 1
                cursor = end + 4
                continue
            else:
                self.counts["invalid_wrappers"] += 1
                self.report("invalid_wrapper", reason="wrapper_tail")
            discarded += 4
            cursor = start + 4
        del self.buffer[:cursor]
        if discarded:
            self.counts["unparsed_bytes"] += discarded
            self.report("unparsed_bytes", count=discarded)

    def finish(self):
        if self.buffer:
            self.counts["unparsed_bytes"] += len(self.buffer)
            self.report("unfinished_wrapper", count=len(self.buffer))
        self.finish_frame("end_of_session")


def describe_session(profile, config, recording_id, *, replay=False):
    return {
        "schema_version": SCHEMA,
        "recording_id": recording_id,
        "device_model": "DEV112P",
        "data_level": "1dfft",
        "replay": replay,
        "source": "onelab_growing_file",
        "raw_shape": [profile.chips, 2, profile.chirps, profile.bins, 2],
        "raw_dtype": "<i2",
        "axis_order": ["chip", "rx", "chirp", "range_fft_bin", "iq_component"],
        "valid_chirps_shape": [profile.chips, 2, profile.chirps],
        "valid_chirps_dtype": "uint8 0/1",
        "array_encoding": "base64 C-order",
        "value_unit": "device_fft_counts",
        "timestamp_unit": "seconds",
        "timestamp_origin": "first observed frame group",
        "timestamp_source": "nominal_device_counter",
        "host_observed_source": "local file read time, may be batched by ONELAB",
        "config_sha256": hashlib.sha256(config).hexdigest(),
        "profile": asdict(profile),
    }


def produce(source, output, recording_id, stop, *, replay=False,
            poll_s=0.025, stall_s=2.0, group_timeout_s=1.0):
    config = (source / "FILE_Radar_Config.ini").read_bytes()
    profile = Profile.from_ini(config)
    source_path = source / "RadarData_1.dat"
    with source_path.open("rb", buffering=0) as incoming:
        initial = os.fstat(incoming.fileno())
        output.mkdir(parents=True, exist_ok=False)
        (output / "FILE_Radar_Config.ini").write_bytes(config)
        write_json(output / "session.json", describe_session(
            profile, config, recording_id, replay=replay))
        with ((output / "frames.ndjson").open("wb") as frames,
              (output / "events.ndjson").open("wb") as events,
              (output / "source.dat").open("wb") as raw):
            def emit(frame):
                frames.write(json_line(frame))
                frames.flush()

            event_counts = Counter()

            def log(kind, **fields):
                event_counts[kind] += 1
                events.write(json_line({"event": kind, "host_utc": Observation.now().utc, **fields}))
                events.flush()

            assembler = FrameAssembler(profile, recording_id, emit, log)
            stalled = backed_up = False
            last_read = time.monotonic()
            end_reason = "operator_stop"
            stop_at = None
            log("session_started", replay=replay)
            try:
                while True:
                    try:
                        current = source_path.stat()
                    except FileNotFoundError:
                        end_reason = "source_removed"
                        break
                    if ((current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino)
                            or current.st_size < incoming.tell()):
                        end_reason = "source_replaced_or_truncated"
                        break
                    if stop_at is None and stop.is_set():
                        stop_at = current.st_size
                    limit = current.st_size if stop_at is None else min(stop_at, current.st_size)
                    unread = limit - incoming.tell()
                    if stop_at is not None and unread == 0:
                        break
                    if not replay and (unread > 4 * CHUNK_SIZE) != backed_up:
                        backed_up = not backed_up
                        log("source_backlog" if backed_up else "source_caught_up", unread_bytes=unread)
                    if chunk := incoming.read(min(CHUNK_SIZE, unread)):
                        observed = Observation.now()
                        if stalled:
                            log("source_resumed")
                            stalled = False
                        last_read = observed.ns / 1e9
                        raw.write(chunk)
                        raw.flush()
                        assembler.feed(chunk, observed)
                        continue
                    if replay or stop_at is not None:
                        if stop_at is None:
                            end_reason = "replay_eof"
                        break
                    idle_s = time.monotonic() - last_read
                    if idle_s >= group_timeout_s and assembler.frame and not assembler.frame.emitted:
                        log("group_timeout", device_frame_id=assembler.frame.counter)
                        assembler.finish_frame("group_timeout")
                    if idle_s >= stall_s and not stalled:
                        log("source_stalled", idle_s=round(idle_s, 3))
                        stalled = True
                    stop.wait(poll_s)
            except Exception as error:
                end_reason = "error"
                log("producer_error", message=str(error))
                raise
            finally:
                assembler.finish()
                log("session_ended", reason=end_reason)
                summary = {"recording_id": recording_id, "reason": end_reason,
                           "frames": assembler.frame_id, "source_bytes": raw.tell(),
                           "counts": dict(assembler.counts), "events": dict(event_counts)}
                write_json(output / "summary.json", summary)
    return summary


def follow_frames(output, *, start_timeout_s=20.0, poll_s=0.025, on_backlog=None):
    """Yield each committed JSON frame while the producer is still writing."""
    output = Path(output)
    path = output / "frames.ndjson"
    deadline = time.monotonic() + start_timeout_s
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"No B frame journal under {output}")
        time.sleep(poll_s)
    with path.open("rb") as stream:
        backed_up = False
        while True:
            position = stream.tell()
            line = stream.readline()
            if line.endswith(b"\n"):
                frame = json.loads(line)
                if on_backlog is not None:
                    pending = path.stat().st_size - stream.tell()
                    if (pending > 4 * CHUNK_SIZE) != backed_up:
                        backed_up = not backed_up
                        on_backlog({
                            "event": "consumer_backlog" if backed_up else "consumer_caught_up",
                            "host_utc": Observation.now().utc,
                            "frame_id": frame["frame_id"],
                            "pending_bytes": pending,
                        })
                yield frame
            elif line:
                if (output / "summary.json").is_file():
                    raise ValueError("Truncated final frame journal record")
                stream.seek(position)
                time.sleep(poll_s)
            elif (output / "summary.json").is_file() and position == path.stat().st_size:
                return
            else:
                time.sleep(poll_s)


def decode_arrays(frame, session):
    """Return (IQ int16 ndarray, valid bool ndarray) for the C consumer."""
    import numpy as np

    if frame["schema_version"] != SCHEMA or session["schema_version"] != SCHEMA:
        raise ValueError("Unsupported B-to-C frame schema")
    if frame["recording_id"] != session["recording_id"]:
        raise ValueError("Frame belongs to a different recording")
    iq = np.frombuffer(b64decode(frame["raw_data"], validate=True), dtype="<i2")
    iq = iq.reshape(session["raw_shape"])
    valid = np.frombuffer(b64decode(frame["valid_chirps"], validate=True), dtype=np.uint8)
    if np.any(valid > 1):
        raise ValueError("Chirp mask must contain only 0 and 1")
    return iq, valid.reshape(session["valid_chirps_shape"]).astype(bool)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    producer = commands.add_parser("produce", help="Tail one ONELAB recording")
    producer.add_argument("source", type=Path)
    producer.add_argument("output", type=Path)
    producer.add_argument("--recording-id", required=True)
    producer.add_argument("--replay", action="store_true", help="Exit at EOF for a completed recording")
    consumer = commands.add_parser("probe", help="Display frame envelopes while B is writing")
    consumer.add_argument("output", type=Path)
    args = cli.parse_args()
    try:
        if args.command == "probe":
            def log_backlog(event):
                with (args.output / "probe_events.ndjson").open("ab") as events:
                    events.write(json_line(event))

            for frame in follow_frames(args.output, on_backlog=log_backlog):
                print(f'{frame["recording_id"]} frame={frame["frame_id"]} '
                      f't={frame["timestamp"]:.4f}s {frame["integrity_status"]} '
                      f'valid={frame["valid_chirp_count"]}', flush=True)
            summary = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
            print(f'B session ended: {summary["reason"]}', flush=True)
        else:
            stop = Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            summary = produce(args.source, args.output, args.recording_id, stop, replay=args.replay)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    except (OSError, ValueError) as error:
        print(f"B-to-C: {error}", file=sys.stderr)
        return 1
    return int(summary["reason"] not in {"operator_stop", "replay_eof"})


if __name__ == "__main__":
    raise SystemExit(main())
