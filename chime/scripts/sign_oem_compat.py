"""Create a separate local certificate-compatible copy; never installs or publishes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CERT_SHA256 = "c8a2e9bccf597c2fb6dc66bee293fc13f2fc47ec77bc6b2b0d52c11f51192ab8"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(path):
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise SystemExit("Duplicate ZIP entries")
        return {name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in sorted(archive.namelist())
                if not name.upper().startswith("META-INF/") and not name.endswith("/")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--java", required=True, type=Path)
    parser.add_argument("--build-tools", required=True, type=Path)
    parser.add_argument("--platform-key", required=True, type=Path)
    parser.add_argument("--platform-cert", required=True, type=Path)
    args = parser.parse_args()
    current_version = ET.parse(ROOT / "app/AndroidManifest.xml").getroot().get(
        "{http://schemas.android.com/apk/res/android}versionName")
    if not current_version or not re.fullmatch(r"[A-Za-z0-9._-]+", current_version):
        raise SystemExit("Unsafe current version name")
    original = (args.input or ROOT / "dist" / ("i30-PD-ACC-Chime-" + current_version + "-API17-private.apk")).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out_dir or ROOT / "builds" / (stamp + "-oem-compat")).resolve()
    if out == original.parent:
        raise SystemExit("Choose a distinct output directory; original dist must remain unchanged")
    out.mkdir(parents=True, exist_ok=False)
    signer = args.build_tools / "lib/apksigner.jar"
    aapt2, zipalign = args.build_tools / ("aapt2.exe" if os.name == "nt" else "aapt2"), args.build_tools / ("zipalign.exe" if os.name == "nt" else "zipalign")
    for path in (original, args.java, signer, aapt2, zipalign, args.platform_key, args.platform_cert):
        if not path.is_file():
            raise SystemExit("Required local input missing: " + str(path))

    def run(name, command):
        result = subprocess.run([str(item) for item in command], capture_output=True)
        (out / (name + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit(name + " failed; inspect " + str(out / (name + ".log")))
        return (result.stdout + result.stderr).decode("utf-8", errors="replace")

    original_hash = sha256(original)
    before = payload(original)
    original_xml = run("original-manifest", [aapt2, "dump", "xmltree", original, "--file", "AndroidManifest.xml"])
    if "sharedUserId" in original_xml or "debuggable(0x0101000f)=false" not in original_xml:
        raise SystemExit("Expected ordinary UID and explicitly non-debuggable application")
    if '"android.permission.RECEIVE_BOOT_COMPLETED"' not in original_xml:
        raise SystemExit("Expected original permission declaration")
    version_match = re.search(r':versionName[^\n]*="([^"]+)"', original_xml)
    code_match = re.search(r':versionCode[^\n]*=(0x[0-9a-fA-F]+|[0-9]+)', original_xml)
    if not version_match or not code_match or not re.fullmatch(r"[A-Za-z0-9._-]+", version_match.group(1)):
        raise SystemExit("Unable to read safe version metadata from input APK")
    version = version_match.group(1)
    final = out / ("i30-PD-ACC-Chime-" + version + "-API17-OEM-compat-private.apk")
    run("sign-oem-compatible-v1", [args.java, "-jar", signer, "sign", "--key", args.platform_key,
        "--cert", args.platform_cert, "--v1-signer-name", "CERT", "--min-sdk-version", "17",
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "false", "--v3-signing-enabled", "false",
        "--v4-signing-enabled", "false", "--out", final, original])
    verification = run("verify-api17-v1", [args.java, "-jar", signer, "verify", "--min-sdk-version", "17",
        "--max-sdk-version", "17", "--verbose", "--print-certs", final])
    cert = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)", verification)
    if cert is None or cert.group(1).lower() != EXPECTED_CERT_SHA256:
        raise SystemExit("Signer differs from the documented target-compatible platform test certificate")
    if "Verified using v1 scheme (JAR signing): true" not in verification:
        raise SystemExit("API17 v1 signature did not verify")
    run("verify-alignment", [zipalign, "-c", "4", final])
    final_xml = run("compatible-manifest", [aapt2, "dump", "xmltree", final, "--file", "AndroidManifest.xml"])
    if original_xml != final_xml or payload(final) != before or sha256(original) != original_hash:
        raise SystemExit("Original APK or application payload changed")
    with zipfile.ZipFile(final) as archive:
        wavs = [n for n in before if n.startswith("assets/") and n.endswith(".wav")]
        if len(wavs) != 2 or any(archive.getinfo(n).compress_type != zipfile.ZIP_STORED for n in wavs):
            raise SystemExit("Expected the two unchanged ZIP_STORED private WAV assets")
        if archive.read("classes.dex")[:8] != b"dex\n035\x00":
            raise SystemExit("Expected unchanged DEX035")
    receipt = {"schema": 1, "status": "offline_compatibility_copy_verified", "created_utc": stamp,
        "input_apk": str(original), "input_sha256": original_hash, "input_preserved": True,
        "version_name": version, "version_code": int(code_match.group(1), 0),
        "apk": str(final), "apk_sha256": sha256(final), "apk_bytes": final.stat().st_size,
        "signer_certificate_sha256": EXPECTED_CERT_SHA256,
        "reason": "Target OEM PackageManager installNewPackageLI rejects newly installed packages after boot unless their certificate matches platform/shared/media; original private signer was rejected with -104 despite valid JAR signatures.",
        "certificate_role": "User-supplied platform test certificate; fingerprint pinned to the single documented target. No key is downloaded or bundled. Original developer key unchanged.",
        "v1_signature_api17_verified": True, "payload_unchanged": True, "payload_sha256": before,
        "manifest_unchanged": True, "normal_app_uid": True, "shared_uid": None, "debuggable": False,
        "permissions_unchanged": True, "permissions": ["android.permission.RECEIVE_BOOT_COMPLETED"],
        "wav_zip_stored": True, "dex_version": "035", "original_private_media_unchanged": True,
        "java_recompiled": False, "installed": False, "physical_or_vm_commands": False,
        "published": False, "private_app_and_voice_assets": True}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (out / "SHA256SUMS.txt").write_text(receipt["apk_sha256"] + "  " + final.name + "\n", encoding="ascii")
    print(str(final))
    print("SHA256 " + receipt["apk_sha256"])
    print("Certificate " + EXPECTED_CERT_SHA256)


if __name__ == "__main__":
    main()
