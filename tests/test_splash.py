"""Generated fixtures/fake ADB only. Never accesses a device or OEM image."""
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import re
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("splash", Path(__file__).resolve().parents[1] / "tools/splash.py")
splash = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(splash)


def fixture():
    data = bytearray(splash.SIZE)
    for index, height in enumerate((480, 480, 480, 480, 100)):
        off = index * splash.STRIDE
        length = 800 * height * 3 + (0 if index == 3 else 2)
        data[off:off + 2] = b"BM"
        struct.pack_into("<III", data, off + 2, 54 + length, 0, 54)
        struct.pack_into("<IiiHHIIiiII", data, off + 14,
                         40, 800, height, 1, 24, 0, length, 2834, 2834, 0, 0)
    return bytes(data)


class FakeAdb:
    def __init__(self, original, corrupt_stage=False, corrupt_after=False, failed_rollback=False):
        self.partition = original
        self.remote = {}
        self.writes = []
        self.reads = []
        self.corrupt_stage = corrupt_stage
        self.corrupt_after = corrupt_after
        self.failed_rollback = failed_rollback
        self.lock_token = None

    def run(self, verb, local, remote):
        assert verb == "push"
        self.remote[remote] = Path(local).read_bytes()

    def pull(self, remote, local):
        data = self.remote[remote]
        if self.corrupt_stage:
            return b"tampered"
        Path(local).write_bytes(data)
        return data

    def read_partition(self, work, label):
        self.reads.append(label)
        if self.corrupt_after and label.startswith("after-"):
            return b"bad readback"
        if self.failed_rollback and label.startswith("rollback-after"):
            return b"bad rollback"
        return self.partition

    def shell(self, script, root=False, timeout=90):
        if not root:
            if "\nmkdir " + splash.LOCK + "\n" in script:
                splash.require(self.lock_token is None, "lock already held")
                self.lock_token = re.search(r"printf '%s\\n' ([0-9a-f]+)", script).group(1)
            else:
                token = re.search(r'owner\)\" = ([0-9a-f]+)', script).group(1)
                splash.require(self.lock_token == token, "not lock owner")
                self.lock_token = None
            return ""
        assert root and timeout is None
        assert "of=/dev/block/mmcblk0p7 bs=4096 count=2048\nsync" in script
        assert "= 179:7" in script and "= 16384" in script
        source = script.split("dd if=", 1)[1].split(" ", 1)[0]
        self.writes.append(source.rsplit("/", 1)[1])
        self.partition = self.remote[source]
        return ""

    def readiness(self, work):
        return {"profile": splash.PROFILE}


class SplashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture()
        cls.candidate = bytearray(cls.original)
        cls.candidate[54] = 1
        cls.candidate[4128822] = 2
        cls.candidate = bytes(cls.candidate)

    def test_compatible_layout_and_functional_recovery_height(self):
        info = splash.inspect(self.original)
        self.assertEqual([480, 480, 480, 480, 100], [s["height"] for s in info["slots"]])

    def test_truncated_wrong_bpp_and_wrong_mapping_headers_refused(self):
        for data in (self.original[:-1], b"bad", self.original + b"x"):
            with self.assertRaises(splash.Refusal):
                splash.inspect(data)
        for index, value in ((28, 32), (34, 99), (18, 0)):
            malformed = bytearray(self.original)
            malformed[index] = value
            with self.assertRaises(splash.Refusal):
                splash.inspect(malformed)

    def test_only_two_branding_pixel_ranges_allowed(self):
        splash.check_pixel_only(self.original, self.candidate)
        for position in (0, 54 + 1152000, splash.STRIDE + 54, 4 * splash.STRIDE + 54, splash.SIZE - 1):
            data = bytearray(self.candidate)
            data[position] ^= 1
            with self.assertRaises(splash.Refusal):
                splash.check_pixel_only(self.original, data)

    def test_pixel_encoding_is_bottom_up_bgr_and_preserves_every_other_byte(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "own.png"
            image = Image.new("RGB", (800, 480), (1, 2, 3))
            image.putpixel((0, 479), (17, 18, 19))
            image.save(p)
            data = splash.encode_image(self.original, p)
            self.assertEqual(b"\x13\x12\x11", data[54:57])
            self.assertEqual(b"\x03\x02\x01", data[54 + 800 * 3:57 + 800 * 3])
            splash.check_pixel_only(self.original, data)

    def test_bad_artwork_refused_without_resizing_or_alpha_compositing(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "own.png"
            for mode, size, color in (("RGB", (799, 480), (0, 0, 0)), ("RGBA", (800, 480), (0, 0, 0, 254))):
                Image.new(mode, size, color).save(p)
                with self.assertRaises(splash.Refusal):
                    splash.encode_image(self.original, p)

    def transaction(self, adb, expected_error=False):
        with tempfile.TemporaryDirectory() as temp:
            work = splash.Work(Path(temp) / "run")
            if expected_error:
                with self.assertRaises(splash.Refusal):
                    splash.write_and_verify(adb, work, self.candidate, self.original, "apply")
            else:
                splash.write_and_verify(adb, work, self.candidate, self.original, "apply")
            receipt = work.local / "receipt.json"
            return receipt.read_text() if receipt.exists() else None

    def test_apply_verifies_staging_current_partition_and_two_full_readbacks(self):
        adb = FakeAdb(self.original)
        receipt = self.transaction(adb)
        self.assertEqual(["desired.img"], adb.writes)
        self.assertEqual(["immediately-before", "after-1", "after-2"], adb.reads)
        self.assertIn("apply_two_full_readbacks_verified", receipt)
        self.assertEqual(self.candidate, adb.partition)
        self.assertIsNone(adb.lock_token)

    def test_staged_tamper_refused_before_partition_write(self):
        adb = FakeAdb(self.original, corrupt_stage=True)
        self.transaction(adb, expected_error=True)
        self.assertEqual([], adb.writes)
        self.assertIsNone(adb.lock_token)

    def test_second_writer_is_refused_before_staging_or_writes(self):
        adb = FakeAdb(self.original)
        with splash.DeviceLock(adb):
            owner = adb.lock_token
            self.transaction(adb, expected_error=True)
            self.assertEqual(owner, adb.lock_token)
            self.assertEqual({}, adb.remote)
            self.assertEqual([], adb.writes)
        self.assertIsNone(adb.lock_token)

    def test_nonowner_cannot_release_lock(self):
        adb = FakeAdb(self.original)
        with splash.DeviceLock(adb):
            owner = adb.lock_token
            with self.assertRaises(splash.Refusal):
                splash.DeviceLock(adb).__exit__(None, None, None)
            self.assertEqual(owner, adb.lock_token)

    def test_restore_reverses_candidate_and_verifies_both_reads(self):
        adb = FakeAdb(self.candidate)
        with tempfile.TemporaryDirectory() as temp:
            work = splash.Work(Path(temp) / "run")
            splash.write_and_verify(adb, work, self.original, self.candidate, "restore")
            self.assertEqual(self.original, adb.partition)
            self.assertEqual(["immediately-before", "after-1", "after-2"], adb.reads)
            self.assertIn("restore_two_full_readbacks_verified", (work.local / "receipt.json").read_text())

    def test_live_change_refused_before_partition_write(self):
        adb = FakeAdb(self.candidate)
        self.transaction(adb, expected_error=True)
        self.assertEqual([], adb.writes)

    def test_readback_failure_restores_previous_and_reads_twice(self):
        adb = FakeAdb(self.original, corrupt_after=True)
        receipt = self.transaction(adb, expected_error=True)
        self.assertEqual(["desired.img", "rollback.img"], adb.writes)
        self.assertEqual(self.original, adb.partition)
        self.assertIn('"rollback_verified": true', receipt)
        self.assertEqual(["rollback-after-1", "rollback-after-2"], adb.reads[-2:])

    def test_unverified_rollback_is_reported_as_failure(self):
        adb = FakeAdb(self.original, corrupt_after=True, failed_rollback=True)
        receipt = self.transaction(adb, expected_error=True)
        self.assertIn('"rollback_verified": false', receipt)

    def test_plan_tamper_and_nonbranding_changes_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "original.img").write_bytes(self.original)
            (path / "candidate.img").write_bytes(self.candidate)
            splash.save_json(path / "plan.json", {"schema": 1, "profile": splash.PROFILE,
                "original_sha256": splash.sha(self.original), "candidate_sha256": "0" * 64})
            with self.assertRaises(splash.Refusal):
                splash.load_plan(path)

    def test_no_write_without_flag_or_known_profile(self):
        with mock.patch.object(splash, "Adb", side_effect=AssertionError("must not connect")):
            for operation in ("apply", "restore"):
                with self.assertRaises(splash.Refusal):
                    splash.main([operation, "--serial", "fixture", "--profile", splash.PROFILE,
                                 "--plan", "unused", "--out", "unused"])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                splash.main(["check", "--serial", "fixture", "--profile", "unknown", "--out", "unused"])

    def test_property_and_mapping_mismatch_refused_before_root(self):
        class Reader(splash.Adb):
            def __init__(self, property_bad=False, dev="179:7"):
                super().__init__("fake", "fixture")
                self.property_bad, self.dev = property_bad, dev
            def run(self, *args, timeout=90):
                return b"device\n"
            def shell(self, script, **kwargs):
                if script.startswith("getprop "):
                    return "wrong" if self.property_bad else splash.PROPERTIES[script[8:]]
                if script.endswith("/dev"):
                    return self.dev
                raise AssertionError("must stop before root")
        for reader in (Reader(True), Reader(dev="179:8")):
            with self.assertRaises(splash.Refusal):
                reader.readiness(Path("unused"))


if __name__ == "__main__":
    unittest.main()
