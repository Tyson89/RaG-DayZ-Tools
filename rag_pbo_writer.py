import hashlib
import os
import struct

from rag_builder_common import (
    BuildError,
    COPY_CHUNK_SIZE,
    PBO_VERSION_MAGIC,
    WIN_SEP,
    ZERO,
    safe_ascii,
    try_relpath,
    should_skip_dir,
    should_skip_file,
)
from pbo_core import read_pbo_archive


def pack_pbo(source_dir, output_path, prefix, log, extra_patterns=None, properties=None):
    source_dir = os.path.normpath(source_dir)
    output_path = os.path.normpath(output_path)
    if not os.path.isdir(source_dir):
        raise BuildError(f"Source is not a directory: {source_dir}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    files = []
    for root, dirs, filenames in os.walk(source_dir):
        rel_root = try_relpath(root, source_dir)
        if not rel_root:
            log(f"WARNING: Skipped external folder while packing because it is on a different drive than the pack source: {root}")
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for fname in filenames:
            if should_skip_file(fname, extra_patterns):
                continue
            full = os.path.join(root, fname)
            rel = try_relpath(full, source_dir)
            if not rel:
                log(f"WARNING: Skipped external file while packing because it is on a different drive than the pack source: {full}")
                continue
            rel = rel.replace(os.sep, WIN_SEP)
            details = os.stat(full)
            files.append({"name": rel, "path": full, "data": None, "size": details.st_size, "mtime": details.st_mtime_ns, "inode": details.st_ino})

    if prefix:
        normalized_prefix = prefix.replace("/", WIN_SEP).strip(WIN_SEP)
        prefix_data = safe_ascii(normalized_prefix + "\r\n", "PBO prefix file")
        files = [entry for entry in files if entry["name"].lower() not in {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}]
        files.append({"name": "$PBOPREFIX$", "path": None, "data": prefix_data, "size": len(prefix_data)})

    files.sort(key=lambda item: item["name"].lower())
    header = bytearray()
    header.extend(ZERO)
    header.extend(struct.pack("<I", PBO_VERSION_MAGIC))
    header.extend(struct.pack("<IIII", 0, 0, 0, 0))
    header_properties = {"prefix": prefix} if prefix else {}
    for key, value in (properties or {}).items():
        if key.casefold() == "prefix":
            if str(value) != prefix:
                raise BuildError("PBO prefix property conflicts with the selected prefix.")
            continue
        header_properties[key] = value
    for key, value in header_properties.items():
        header.extend(safe_ascii(key, "PBO property name"))
        header.extend(ZERO)
        header.extend(safe_ascii(str(value), "PBO property value"))
        header.extend(ZERO)
    header.extend(ZERO)
    for entry in files:
        header.extend(safe_ascii(entry["name"], "File path"))
        header.extend(ZERO)
        header.extend(struct.pack("<IIIII", 0, entry["size"], 0, 0, entry["size"]))
    header.extend(ZERO)
    header.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))
    temp_output = output_path + ".tmp"
    sha = hashlib.sha1()
    total = 0
    try:
        with open(temp_output, "wb") as out:
            out.write(header)
            sha.update(header)
            total += len(header)
            for entry in files:
                data = entry["data"]
                if data is not None:
                    out.write(data)
                    sha.update(data)
                    total += len(data)
                    continue

                with open(entry["path"], "rb") as file:
                    before = os.fstat(file.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ino) != (entry["size"], entry["mtime"], entry["inode"]):
                        raise BuildError(f"Source changed while packing: {entry['path']}")
                    written = 0
                    while True:
                        chunk = file.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                        sha.update(chunk)
                        total += len(chunk)
                        written += len(chunk)
                    after = os.stat(entry["path"])
                    if written != entry["size"] or (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
                        raise BuildError(f"Source changed while packing: {entry['path']}")
            out.write(ZERO)
            out.write(sha.digest())
            total += 21
        os.replace(temp_output, output_path)
    except Exception:
        if os.path.isfile(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass
        raise
    log(f"Packed {len(files):4d} files / {total:,} bytes -> {output_path}")


def read_packed_pbo_prefix(pbo_path):
    try:
        with open(pbo_path, "rb") as file:
            data = file.read(65536)
    except OSError:
        return ""

    marker = b"prefix\x00"
    index = data.find(marker)

    if index < 0:
        return ""

    start = index + len(marker)
    end = data.find(b"\x00", start)

    if end < 0:
        return ""

    return data[start:end].decode("ascii", errors="ignore")


def verify_packed_pbo(pbo_path, expected_prefix, log, expected_properties=None):
    if not os.path.isfile(pbo_path):
        raise BuildError(f"Post-pack verification failed. PBO does not exist: {pbo_path}")

    size = os.path.getsize(pbo_path)

    if size <= 0:
        raise BuildError(f"Post-pack verification failed. PBO is empty: {pbo_path}")

    archive = read_pbo_archive(pbo_path)
    if archive["footer_size"] != 21:
        raise BuildError(f"Post-pack verification failed. PBO payload length does not match its header: {pbo_path}")
    with open(pbo_path, "rb") as file:
        digest = hashlib.sha1()
        remaining = archive["payload_end"]
        while remaining:
            chunk = file.read(min(COPY_CHUNK_SIZE, remaining))
            if not chunk:
                raise BuildError(f"Post-pack verification failed. PBO payload ended early: {pbo_path}")
            digest.update(chunk)
            remaining -= len(chunk)
        footer = file.read(22)
        if footer != ZERO + digest.digest():
            raise BuildError(f"Post-pack verification failed. PBO checksum mismatch: {pbo_path}")
        digest.update(footer)
    properties = archive["properties"]
    packed_prefix = properties.get("prefix", "")

    if expected_prefix and packed_prefix and packed_prefix != expected_prefix:
        raise BuildError(f"Post-pack verification failed. PBO prefix mismatch. Expected '{expected_prefix}', got '{packed_prefix}'.")

    if expected_prefix and not packed_prefix:
        raise BuildError("Post-pack verification failed. PBO prefix is missing from the header.")
    for key, expected in (expected_properties or {}).items():
        if properties.get(key) != str(expected):
            raise BuildError(f"Post-pack verification failed. PBO property {key} mismatch.")
    log(f"Post-pack verification OK: size={size:,} bytes, prefix={packed_prefix or '<none>'}")
    return digest.hexdigest()


def pbo_entry_bytes_match_file(pbo_path, entry, source_file):
    try:
        source_size = os.path.getsize(source_file)
    except OSError:
        return False, "source WRP is missing"

    if entry.data_size != source_size:
        return False, f"size mismatch, packed={entry.data_size}, source={source_size}"

    if entry.packing_method != 0:
        return False, f"unexpected packed WRP method=0x{entry.packing_method:08X}"

    try:
        with open(pbo_path, "rb") as pbo_file, open(source_file, "rb") as source:
            pbo_file.seek(entry.offset)

            while True:
                left = source.read(COPY_CHUNK_SIZE)

                if not left:
                    break

                right = pbo_file.read(len(left))

                if left != right:
                    return False, "byte mismatch"
    except OSError as error:
        return False, str(error)

    return True, ""
