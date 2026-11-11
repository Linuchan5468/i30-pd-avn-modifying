"""Build and verify a private API17 APK locally. Never installs, uploads, or plays audio."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
PACKAGE = "com.linuc.i30pd.accchime"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deterministic_zip(destination, files):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, path in sorted(files):
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            archive.writestr(entry, path.read_bytes())


def dex_classes(data):
    if data[:8] != b"dex\n035\x00":
        raise SystemExit("Expected a single DEX035 compatible with API17")
    strings_size, strings_off, types_size, types_off = struct.unpack_from("<4I", data, 56)
    classes_size, classes_off = struct.unpack_from("<2I", data, 96)
    strings = []
    for index in range(strings_size):
        start = struct.unpack_from("<I", data, strings_off + 4 * index)[0]
        while data[start] & 128:
            start += 1
        start += 1
        strings.append(data[start:data.index(0, start)].decode("utf-8", errors="replace"))
    types = [strings[struct.unpack_from("<I", data, types_off + 4 * i)[0]] for i in range(types_size)]
    return [types[struct.unpack_from("<I", data, classes_off + 32 * i)[0]] for i in range(classes_size)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jdk", required=True, type=Path)
    parser.add_argument("--build-tools", required=True, type=Path)
    parser.add_argument("--android-jar", required=True, type=Path)
    parser.add_argument("--r8-jar", required=True, type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.build_dir or ROOT / "builds" / stamp).resolve()
    out.mkdir(parents=True, exist_ok=False)
    java, javac, keytool = [args.jdk / "bin" / (name + (".exe" if os.name == "nt" else "")) for name in ("java", "javac", "keytool")]
    aapt2, zipalign = [args.build_tools / (name + (".exe" if os.name == "nt" else "")) for name in ("aapt2", "zipalign")]
    signer = args.build_tools / "lib/apksigner.jar"
    for path in (java, javac, keytool, aapt2, zipalign, signer, args.android_jar, args.r8_jar):
        if not path.is_file():
            raise SystemExit("Required local build tool missing: " + str(path))

    def run(label, command, env=None):
        result = subprocess.run([str(part) for part in command], capture_output=True, env=env)
        (out / (label + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit(label + " failed; see " + str(out / (label + ".log")))
        return (result.stdout + result.stderr).decode("utf-8", errors="replace")

    manifest = ROOT / "app/AndroidManifest.xml"
    manifest_hash = sha256(manifest)
    tree = ET.parse(manifest).getroot()
    application = tree.find("application")
    sdk = tree.find("uses-sdk")
    permissions = sorted(item.get(ANDROID_NS + "name") for item in tree.findall("uses-permission"))
    if tree.get("package") != PACKAGE or tree.get(ANDROID_NS + "sharedUserId") is not None:
        raise SystemExit("Private package or shared UID check failed")
    if application is None or application.get(ANDROID_NS + "debuggable") != "false":
        raise SystemExit("Manifest must explicitly disable debugging")
    if sdk is None or sdk.get(ANDROID_NS + "minSdkVersion") != "17" or sdk.get(ANDROID_NS + "targetSdkVersion") != "17":
        raise SystemExit("Expected min/target API17")
    if permissions != ["android.permission.RECEIVE_BOOT_COMPLETED"]:
        raise SystemExit("Unexpected app permissions: " + repr(permissions))
    version = tree.get(ANDROID_NS + "versionName")
    if not version or not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        raise SystemExit("Unsafe version name")

    media_receipt = ROOT / "private-media/media-receipt.json"
    if not media_receipt.exists():
        raise SystemExit("Run scripts/prepare_media.py first")
    media = json.loads(media_receipt.read_text(encoding="utf-8"))
    for record in media["files"]:
        if sha256(ROOT / record["asset"]) != record["asset_sha256"]:
            raise SystemExit("Asset differs from private media receipt: " + record["asset"])

    # Compile OEM ABI in a separate directory. Only application classes become D8 program input.
    abi, classes, dex = [out / name for name in ("compile-only-abi", "app-classes", "dex")]
    for directory in (abi, classes, dex):
        directory.mkdir()
    abi_sources = sorted((ROOT / "compile-stubs/src").rglob("*.java"))
    sources = sorted((ROOT / "app/src/main/java").rglob("*.java"))
    if not sources or not abi_sources:
        raise SystemExit("No app or compile-only ABI sources")
    source_hashes = {p.relative_to(ROOT).as_posix(): sha256(p) for p in sources + abi_sources}
    # The prepared API16 jar contains Android classes, not Java core classes. Use it
    # as the Android compile classpath and Java 8 language/bytecode, matching D8 min17.
    javac_common = [javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", "-Xlint:-options"]
    run("compile-abi", [*javac_common, "-classpath", args.android_jar, "-d", abi, *abi_sources])
    run("compile-app", [*javac_common, "-classpath", os.pathsep.join((str(args.android_jar), str(abi))),
                        "-d", classes, *sources])
    abi_jar, program_jar = out / "compile-only-abi.jar", out / "app-program.jar"
    deterministic_zip(abi_jar, [(p.relative_to(abi).as_posix(), p) for p in abi.rglob("*.class")])
    deterministic_zip(program_jar, [(p.relative_to(classes).as_posix(), p) for p in classes.rglob("*.class")])
    run("d8", [java, "-cp", args.r8_jar, "com.android.tools.r8.D8", "--min-api", "17",
               "--lib", args.android_jar, "--lib", args.jdk, "--classpath", abi_jar,
               "--output", dex, program_jar])
    dex_files = sorted(dex.glob("*.dex"))
    if len(dex_files) != 1 or dex_files[0].name != "classes.dex":
        raise SystemExit("Expected one classes.dex; multidex is not allowed")
    defined = dex_classes(dex_files[0].read_bytes())
    if not defined or any(not name.startswith("Lcom/linuc/i30pd/accchime/") for name in defined):
        raise SystemExit("DEX contains a non-app class, including an OEM compile stub: " + repr(defined))

    resource_apk = out / "resources.apk"
    run("aapt2-link", [aapt2, "link", "--manifest", manifest, "-I", args.android_jar,
                       "--min-sdk-version", "17", "--target-sdk-version", "17", "--no-auto-version",
                       "-A", ROOT / "app/src/main/assets", "-0", "wav", "-o", resource_apk])
    unaligned, aligned = out / "unsigned-unaligned.apk", out / "unsigned-aligned.apk"
    with zipfile.ZipFile(resource_apk) as source, zipfile.ZipFile(unaligned, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        entry = zipfile.ZipInfo("classes.dex", (1980, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_STORED
        target.writestr(entry, dex_files[0].read_bytes())
    run("zipalign", [zipalign, "-f", "4", unaligned, aligned])

    # Unique local development key. Preserve it for future private updates; never ship the key.
    private = ROOT / ".private"
    private.mkdir(exist_ok=True)
    keystore, password_file = private / "i30pd-acc-chime-dev.p12", private / "signing-password.txt"
    if keystore.exists() != password_file.exists():
        raise SystemExit("Incomplete local signing material; refusing to replace the private key")
    if not keystore.exists():
        password_file.write_text(secrets.token_urlsafe(36), encoding="ascii")
    password = password_file.read_text(encoding="ascii").strip()
    env = dict(os.environ, I30PD_CHIME_KEYSTORE_PASSWORD=password)
    alias = "i30pd-acc-chime-private-dev"
    if not keystore.exists():
        run("generate-private-key", [keytool, "-genkeypair", "-keystore", keystore, "-storetype", "PKCS12",
            "-storepass:env", "I30PD_CHIME_KEYSTORE_PASSWORD", "-keypass:env", "I30PD_CHIME_KEYSTORE_PASSWORD",
            "-alias", alias, "-keyalg", "RSA", "-keysize", "2048", "-sigalg", "SHA256withRSA", "-validity", "3650",
            "-dname", "CN=i30PD ACC Chime Private Development,O=Local Private Build", "-noprompt"], env=env)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    final = dist / ("i30-PD-ACC-Chime-" + version + "-API17-private.apk")
    signed = out / final.name
    run("sign-v1", [java, "-jar", signer, "sign", "--ks", keystore, "--ks-key-alias", alias,
        "--ks-pass", "env:I30PD_CHIME_KEYSTORE_PASSWORD", "--key-pass", "env:I30PD_CHIME_KEYSTORE_PASSWORD",
        "--min-sdk-version", "17", "--v1-signing-enabled", "true", "--v2-signing-enabled", "false",
        "--v3-signing-enabled", "false", "--v4-signing-enabled", "false", "--out", signed, aligned], env=env)
    verification = run("verify-api17-v1", [java, "-jar", signer, "verify", "--min-sdk-version", "17",
        "--max-sdk-version", "17", "--verbose", "--print-certs", signed])
    if "Verified using v1 scheme (JAR signing): true" not in verification:
        raise SystemExit("APK v1 verification did not pass")
    run("verify-alignment", [zipalign, "-c", "4", signed])
    with zipfile.ZipFile(signed) as archive:
        dex_names = [name for name in archive.namelist() if re.fullmatch(r"classes\d*\.dex", name)]
        if dex_names != ["classes.dex"]:
            raise SystemExit("Final APK has unexpected DEX entries")
        for record in media["files"]:
            name = "assets/" + Path(record["asset"]).name
            if archive.getinfo(name).compress_type != zipfile.ZIP_STORED:
                raise SystemExit("WAV must be ZIP_STORED for AssetManager.openFd: " + name)
            if hashlib.sha256(archive.read(name)).hexdigest() != record["asset_sha256"]:
                raise SystemExit("Packaged WAV hash differs")
        if any(name.startswith(("com/hkmc/", ".private/", "private-media/")) for name in archive.namelist()):
            raise SystemExit("Private source/signing/original material entered APK")
    run("inspect-final-manifest", [aapt2, "dump", "xmltree", signed, "--file", "AndroidManifest.xml"])
    if manifest_hash != sha256(manifest) or any(sha256(ROOT / p) != h for p, h in source_hashes.items()):
        raise SystemExit("App sources changed during build; rerun with the completed sources")
    shutil.copy2(signed, final)
    cert_match = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)", verification)
    receipt = {"schema": 1, "status": "offline_build_checks_passed", "package": PACKAGE,
        "version_code": int(tree.get(ANDROID_NS + "versionCode")), "version_name": version,
        "min_api": 17, "target_api": 17, "dex_version": "035", "single_dex": True,
        "java_target": "8", "android_compile_jar": str(args.android_jar.resolve()),
        "apk": final.name, "apk_bytes": final.stat().st_size, "apk_sha256": sha256(final),
        "manifest_sha256": manifest_hash, "dex_sha256": sha256(dex_files[0]),
        "debuggable": False, "shared_uid": None, "permissions": permissions,
        "v1_signature_api17_verified": True, "v2_signature": False, "v3_signature": False,
        "signer_certificate_sha256": cert_match.group(1).lower() if cert_match else None,
        "private_signing_key": "Local unique development key under .private; excluded from APK/dist",
        "oem_stub_definitions_packaged": False, "defined_classes": defined, "wav_zip_stored": True,
        "media_receipt_sha256": sha256(media_receipt), "assets": media["files"],
        "sources_sha256": source_hashes,
        "d8_java_core_library": "Local JDK supplied as library input only; no Java/OEM library classes packaged",
        "tool_sha256": {str(p.resolve()): sha256(p) for p in (args.android_jar, args.r8_jar, aapt2, signer)},
        "built_at_utc": stamp, "build_log_directory": str(out),
        "not_tested": ["APK installation", "real vehicle signals", "quickboot lifecycle", "audible playback"],
        "vehicle_emulator_adb_network_audio_operations": False}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (dist / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (dist / "SHA256SUMS.txt").write_text(receipt["apk_sha256"] + "  " + final.name + "\n", encoding="ascii")
    print(str(final))
    print("SHA256 " + receipt["apk_sha256"])
    print("API17 v1 signature, DEX035, stub exclusion, stored WAV hashes and alignment verified.")


if __name__ == "__main__":
    main()
