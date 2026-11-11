"""Conservative i30 PD V141 splash workflow. No command reboots the device.

Only apply/restore write a partition, and both require --write-p7 explicitly.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import uuid

PROFILE = "pd-kr-v141"
TARGET = "/dev/block/mmcblk0p7"
SIZE = 8388608
STRIDE = 0x150000
ROOT_TOOL = "/system/bin/amossu"
LOCK = "/data/local/tmp/i30pd-splash-p7.lock"
ROOT_SHA = "5933f058153591052254ce6db5193b4f511be2cc6e0aa00144ea9654af5c25c0"
PROPERTIES = {
    "ro.product.name": "pd_kr",
    "ro.product.model": "daudioplus",
    "ro.build.version.release": "4.2.2",
    "ro.build.version.incremental": "eng.vmadmin.20260114.084130",
    "ro.build.product2": "PD.KOR.SOP.V141.260114.STD_H",
}
RANGES = ((54, 1152054), (4128822, 5280822))


class Refusal(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def inspect(data):
    require(len(data) == SIZE, "Expected a full 8 MiB partition backup.")
    result = []
    for index, height in enumerate((480, 480, 480, 480, 100)):
        off = index * STRIDE
        require(data[off:off + 2] == b"BM", "Missing BMP slot %d." % index)
        file_size, reserved, pixels = struct.unpack_from("<III", data, off + 2)
        dib, width, actual_h, planes, bpp, comp, image_size, _, _, colors, important = struct.unpack_from("<IiiHHIIiiII", data, off + 14)
        pixel_size = 800 * height * 3
        require((dib, width, actual_h, planes, bpp, comp, pixels, reserved, colors, important)
                == (40, 800, height, 1, 24, 0, 54, 0, 0, 0), "Unsupported BMP header/layout at slot %d." % index)
        # Stock slots may declare two BMP tail bytes beyond the actual pixels.
        require(image_size in (pixel_size, pixel_size + 2), "Unsupported BMP image size.")
        require(file_size == 54 + image_size and off + file_size <= SIZE,
                "Unsupported BMP file size.")
        result.append({"slot": index, "offset": off, "width": width, "height": height,
                       "file_bytes": file_size, "pixel_bytes": pixel_size})
    return {"profile": PROFILE, "bytes": len(data), "sha256": sha(data), "slots": result}


def check_pixel_only(original, candidate):
    inspect(original)
    inspect(candidate)
    at = 0
    for first, last in RANGES:
        require(original[at:first] == candidate[at:first], "Change outside branding pixels.")
        at = last
    require(original[at:] == candidate[at:], "Change outside branding pixels/tail.")
    require(original != candidate, "Candidate has no pixel changes.")


def encode_image(original, artwork):
    from PIL import Image
    inspect(original)
    with Image.open(artwork) as image:
        require(image.size == (800, 480), "Artwork must be exactly 800 x 480; no automatic resize/crop.")
        require(image.mode in ("RGB", "RGBA"), "Save artwork as RGB/RGBA PNG first.")
        if image.mode == "RGBA":
            require(image.getextrema()[3] == (255, 255), "Artwork must be fully opaque.")
        image = image.convert("RGB")
        # BMP rows are bottom-up. This only encodes pixels; it does not edit artwork.
        pixels = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes("raw", "BGR")
    candidate = bytearray(original)
    for first, last in RANGES:
        candidate[first:last] = pixels
    check_pixel_only(original, candidate)
    return bytes(candidate)


class Adb:
    def __init__(self, executable, serial):
        require(bool(serial) and not serial.startswith("-"), "An explicit ADB --serial is required.")
        self.executable, self.serial = executable, serial

    def run(self, *args, timeout=90):
        result = subprocess.run([self.executable, "-s", self.serial, *map(str, args)], capture_output=True, timeout=timeout)
        require(result.returncode == 0, "ADB failed: " + result.stderr.decode(errors="replace").strip())
        return result.stdout

    def shell(self, script, root=False, timeout=90):
        marker = "__AVN_STATUS_" + uuid.uuid4().hex
        command = ROOT_TOOL + " -c " + shlex.quote(script) if root else "/system/bin/sh -c " + shlex.quote(script)
        command += "; result=$?; printf '\\n" + marker + ":%s\\n' \"$result\""
        output = self.run("shell", command, timeout=timeout).decode(errors="replace").replace("\r", "")
        match = re.search(r"\n" + marker + r":(\d+)\n?\Z", output)
        require(match is not None and match.group(1) == "0", "Remote operation failed; " + output[-1200:])
        return output[:match.start()].strip()

    def pull(self, remote, local):
        self.run("pull", remote, local)
        return Path(local).read_bytes()

    def read_partition(self, directory, label):
        require(re.fullmatch(r"[a-z0-9-]+", label) is not None, "Invalid readback label.")
        remote = directory.remote + "/" + label + ".img"
        self.shell("set -e\ndd if=" + TARGET + " of=" + remote + " bs=4096 count=2048\n"
                   "chown 2000:2000 " + remote + "\nchmod 600 " + remote, root=True)
        data = self.pull(remote, directory.local / (label + ".img"))
        inspect(data)
        return data

    def readiness(self, work):
        require(self.run("get-state", timeout=10).strip() == b"device", "ADB must be in device state.")
        for key, expected in PROPERTIES.items():
            require(self.shell("getprop " + key) == expected, "Unsupported profile/property: " + key)
        require(self.shell("cat /sys/class/block/mmcblk0p7/dev") == "179:7", "Wrong block mapping.")
        require(self.shell("cat /sys/class/block/mmcblk0p7/size") == "16384", "Wrong partition size.")
        require(self.shell("cat /sys/class/block/mmcblk0p7/ro") == "0", "Partition is read-only.")
        node = self.shell("ls -l " + TARGET)
        require(node.startswith("brw") and re.search(r"\b179,\s+7\b", node), "Unexpected block device node.")
        mounts = self.shell("cat /proc/mounts")
        # Match aliases too: a mounted device with the same major/minor is refused.
        for line in mounts.splitlines():
            source = line.split()[0]
            if source.startswith("/dev/"):
                mounted = self.shell("ls -lL " + shlex.quote(source))
                require(not re.search(r"\b179,\s+7\b", mounted), "Splash block is mounted.")
        root_binary = self.pull(ROOT_TOOL, work / "amossu-check.bin")
        require(sha(root_binary) == ROOT_SHA, "Unrecognized existing maintenance root tool.")
        mode = self.shell("ls -l " + ROOT_TOOL)
        require(mode.startswith("-rwsr-sr-x") and re.search(r"\b(root|0)\s+(root|0)\b", mode), "Unexpected maintenance root permissions/owner.")
        require(re.search(r"\buid=0\(root\)", self.shell("id", root=True)), "Existing maintenance root is unavailable.")
        # Do not retain the root binary: only its public hash belongs in receipts.
        (work / "amossu-check.bin").unlink()
        return {"profile": PROFILE, "target": TARGET, "major_minor": "179:7", "bytes": SIZE,
                "properties": dict(PROPERTIES), "root_tool_sha256": ROOT_SHA}


class Work:
    def __init__(self, local):
        self.local = Path(local).resolve()
        self.local.mkdir(parents=True, exist_ok=False)
        self.remote = "/data/local/tmp/i30pd-splash-" + uuid.uuid4().hex

    def stage(self, adb):
        adb.shell("mkdir " + self.remote + " && chmod 700 " + self.remote)


def load_plan(folder):
    folder = Path(folder).resolve()
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    require(plan.get("profile") == PROFILE and plan.get("schema") == 1, "Unsupported plan.")
    original = (folder / "original.img").read_bytes()
    candidate = (folder / "candidate.img").read_bytes()
    require(sha(original) == plan["original_sha256"] and sha(candidate) == plan["candidate_sha256"], "Plan image hash mismatch.")
    check_pixel_only(original, candidate)
    return plan, original, candidate


class DeviceLock:
    """One cooperative partition writer, including rollback, per device."""
    def __init__(self, adb):
        self.adb = adb
        self.token = uuid.uuid4().hex

    def __enter__(self):
        try:
            # mkdir is atomic. Existing lock directories are never overwritten.
            self.adb.shell("set -e\nmkdir " + LOCK + "\nchmod 700 " + LOCK
                           + "\nprintf '%s\\n' " + self.token + " > " + LOCK + "/owner")
        except Refusal as error:
            raise Refusal("Cannot acquire p7 write lock. Another operation may be active; "
                          "see docs/SPLASH.md before recovering a stale lock.") from error
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.adb.shell("set -e\ntest \"$(cat " + LOCK + "/owner)\" = " + self.token
                           + "\nrm " + LOCK + "/owner\nrmdir " + LOCK)
        except BaseException:
            if error is None:
                raise
            print("p7 lock could not be released; preserve evidence and inspect the connection.", file=sys.stderr)
        return False


def write_and_verify(adb, work, desired, rollback, desired_label):
    # Hold from remote staging through full readbacks and any rollback.
    with DeviceLock(adb):
        _write_and_verify_locked(adb, work, desired, rollback, desired_label)


def _write_and_verify_locked(adb, work, desired, rollback, desired_label):
    # Both write inputs are pushed and independently pulled before the write.
    for label, data in (("desired", desired), ("rollback", rollback)):
        local = work.local / (label + ".img")
        local.write_bytes(data)
        remote = work.remote + "/" + label + ".img"
        adb.run("push", local, remote)
        require(adb.pull(remote, work.local / (label + "-staged-readback.img")) == data,
                "Staged remote input changed.")
    # A full fresh read must still match the known pre-write image.
    require(adb.read_partition(work, "immediately-before") == rollback,
            "Live bytes changed since this backup/plan; refusing to overwrite.")
    guard = ("test \"$(cat /sys/class/block/mmcblk0p7/dev)\" = 179:7\n"
             "test \"$(cat /sys/class/block/mmcblk0p7/size)\" = 16384\n"
             "test \"$(cat /sys/class/block/mmcblk0p7/ro)\" = 0\n"
             "test -b " + TARGET + "\n")
    def copy(label):
        # A timeout must never deliberately terminate the write/sync transaction.
        # Recheck profile, mapping, mounts and maintenance root before every write.
        adb.readiness(work.local)
        return adb.shell("set -e\n" + guard + "dd if=" + work.remote + "/" + label
                         + ".img of=" + TARGET + " bs=4096 count=2048\nsync", root=True, timeout=None)
    try:
        copy("desired")
        for number in (1, 2):
            require(adb.read_partition(work, "after-%d" % number) == desired,
                    "Full write readback mismatch.")
    except BaseException as problem:
        # Best effort only: power/USB loss may prevent rollback. Preserve evidence.
        receipt = {"status": "write_or_verification_failed", "error": str(problem),
                   "rollback_verified": False, "target": TARGET}
        try:
            copy("rollback")
            one = adb.read_partition(work, "rollback-after-1")
            two = adb.read_partition(work, "rollback-after-2")
            require(one == rollback and two == rollback, "Rollback verification mismatch.")
            receipt["rollback_verified"] = True
        except BaseException as failure:
            receipt["rollback_error"] = str(failure)
        save_json(work.local / "receipt.json", receipt)
        raise Refusal("Write did not verify. Check receipt.json; do not reboot if rollback is unverified.") from problem
    save_json(work.local / "receipt.json", {"status": desired_label + "_two_full_readbacks_verified",
        "profile": PROFILE, "target": TARGET, "bytes": SIZE,
        "before_sha256": sha(rollback), "after_sha256": sha(desired),
        "reboot_requested": False, "remote_recovery_directory": work.remote})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="Inspect a local full-partition backup only")
    p.add_argument("image", type=Path)
    p = sub.add_parser("prepare", help="Create a pixel-only candidate offline; never write the device")
    p.add_argument("--backup", required=True, type=Path, help="Directory made by backup")
    p.add_argument("--artwork", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    for name in ("check", "backup", "apply", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--adb", default="adb")
        p.add_argument("--serial", required=True)
        p.add_argument("--profile", choices=[PROFILE], required=True)
        p.add_argument("--out", required=True, type=Path, help="New directory for full backups/receipts")
        if name in ("apply", "restore"):
            p.add_argument("--plan", required=True, type=Path)
            p.add_argument("--write-p7", action="store_true", help="Explicitly authorize this partition write")
    args = parser.parse_args(argv)
    if args.command == "inspect":
        print(json.dumps(inspect(args.image.read_bytes()), indent=2))
        return
    if args.command == "prepare":
        meta = json.loads((args.backup / "backup.json").read_text(encoding="utf-8"))
        require(meta.get("profile") == PROFILE and meta.get("two_full_reads_equal") is True,
                "Use a verified backup made by this tool.")
        original = (args.backup / "original.img").read_bytes()
        require(sha(original) == meta["sha256"], "Backup differs from its receipt.")
        second = (args.backup / "second-read.img").read_bytes()
        require(original == second, "The two original readbacks differ.")
        candidate = encode_image(original, args.artwork)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=False)
        (out / "original.img").write_bytes(original)
        (out / "candidate.img").write_bytes(candidate)
        save_json(out / "plan.json", {"schema": 1, "profile": PROFILE,
            "original_sha256": sha(original), "candidate_sha256": sha(candidate),
            "bytes": SIZE, "changed_slots": [0, 3], "pixel_ranges": RANGES,
            "all_other_bytes_preserved": True})
        print("Prepared only. Review plan.json; apply is a separate explicit command.")
        return
    if args.command in ("apply", "restore"):
        require(args.write_p7, "Partition write requires the explicit --write-p7 flag.")
        plan, original, candidate = load_plan(args.plan)
    work = Work(args.out)
    adb = Adb(args.adb, args.serial)
    readiness = adb.readiness(work.local)
    save_json(work.local / "readiness.json", readiness)
    if args.command == "check":
        print("Confirmed profile and existing root tool match. No partition written.")
        return
    work.stage(adb)
    if args.command == "backup":
        one = adb.read_partition(work, "original")
        two = adb.read_partition(work, "second-read")
        require(one == two, "Independent reads differ; no usable backup created.")
        save_json(work.local / "backup.json", {"schema": 1, "profile": PROFILE,
            "target": TARGET, "bytes": SIZE, "sha256": sha(one), "two_full_reads_equal": True})
        print("Two full backup reads match: " + sha(one))
    else:
        desired, rollback = (candidate, original) if args.command == "apply" else (original, candidate)
        write_and_verify(adb, work, desired, rollback, args.command)
        print("Two full readbacks verified. No reboot requested. Preserve this directory.")


if __name__ == "__main__":
    try:
        main()
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as error:
        print("STOP: " + str(error), file=sys.stderr)
        sys.exit(1)
