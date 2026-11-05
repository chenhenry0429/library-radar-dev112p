"""Teammate's offline reader. Usage: python read_data.py <recording directory>."""
import argparse
import sys
from contract import load_b, load_c, paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    args = parser.parse_args()
    arrays, meta = load_b(args.folder)
    print("source_kind:", meta["source_kind"])
    if meta["source_kind"] == "synthetic":
        print("*** DEMO ONLY: NOT measured radar data; NOT for accuracy evaluation. ***")
    print("recording:", meta["recording_id"], "seat:", meta["seat_id"])
    for k, a in arrays.items():
        print(k, "shape=", a.shape, "dtype=", a.dtype)
    print("data_level:", meta["data_level"])
    print("axis_order:", meta["axis_order"], "unit:", meta["value_unit"])
    print("time range (relative seconds):", arrays["timestamps"][[0, -1]].tolist())
    p = paths(args.folder)
    if p["result"].exists() or p["algorithm"].exists():
        rows, _, _ = load_c(args.folder)
        print("C rows validated:", len(rows))
    print("READ_OK. Access arrays['raw_data'] without reshaping or allowing pickle.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as e:
        print("READ_FAILED:", e, file=sys.stderr)
        sys.exit(2)
