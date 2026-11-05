"""Standard-library-only SHA256 verifier for offline ZIP/folder delivery."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


def safe(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe archive/manifest path: " + name)


def verify(target):
    target = Path(target)
    if target.is_file():
        with zipfile.ZipFile(target) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Duplicate ZIP entries")
            for name in names:
                safe(name)
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            files = manifest["files"]
            if not files or set(names) != set(files) | {"manifest.json"}:
                raise ValueError("Missing/unexpected files")
            for n, digest in files.items():
                safe(n)
                h = hashlib.sha256()
                with z.open(n) as f:
                    for b in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(b)
                if h.hexdigest() != digest:
                    raise ValueError("SHA256 mismatch: " + n)
    else:
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        if not files:
            raise ValueError("Empty manifest")
        root = target.resolve()
        for n, digest in files.items():
            safe(n)
            f = (root / n).resolve()
            if not f.is_relative_to(root):
                raise ValueError("Path outside delivery directory")
            h = hashlib.sha256()
            with f.open("rb") as stream:
                for b in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(b)
            if h.hexdigest() != digest:
                raise ValueError("SHA256 mismatch: " + n)
    print(f"TRANSFER_OK: {len(files)} files; source_kind={manifest['source_kind']}")
    if manifest["source_kind"] == "synthetic":
        print("*** DEMO ONLY: not measured radar data. ***")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default=".")
    try:
        verify(parser.parse_args().target)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as e:
        print("TRANSFER_FAILED:", e, file=sys.stderr)
        sys.exit(2)
