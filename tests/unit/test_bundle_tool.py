"""OS별 시연 번들 도구(frame.tools.bundle). 실제 내려받기·pip 설치·실행은 CI 의 bundle 작업이 확인한다."""

import hashlib
import io
import os
import re
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

import frame
from frame.tools import bundle, demo

REPO = Path(__file__).resolve().parents[2]
WINDOWS = bundle.TARGETS["x86_64-pc-windows-msvc"]
LINUX = bundle.TARGETS["x86_64-unknown-linux-gnu"]
MAC_ARM = bundle.TARGETS["aarch64-apple-darwin"]


def test_every_target_pins_a_runtime_hash_and_has_a_unique_label():
    assert set(bundle.TARGETS) == {
        "x86_64-pc-windows-msvc",
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
    }
    labels = [t.label for t in bundle.TARGETS.values()]
    assert len(set(labels)) == len(labels)
    assert all(re.fullmatch(r"[0-9a-f]{64}", t.sha256) for t in bundle.TARGETS.values())


@pytest.mark.parametrize(
    ("platform", "machine", "label"),
    [
        ("win32", "AMD64", "windows-x64"),
        ("darwin", "arm64", "macos-arm64"),
        ("darwin", "x86_64", "macos-x64"),
        ("linux", "x86_64", "linux-x64"),
        ("linux", "aarch64", "linux-arm64"),
    ],
)
def test_detect_target(platform, machine, label):
    assert bundle.detect_target(platform, machine).label == label


@pytest.mark.parametrize(("platform", "machine"), [("win32", "ARM64"), ("freebsd14", "amd64"), ("linux", "riscv64")])
def test_unsupported_platforms_are_rejected(platform, machine):
    with pytest.raises(bundle.BundleError):
        bundle.detect_target(platform, machine)


def test_asset_url_encodes_the_plus_sign():
    name = "cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    assert bundle.asset_name(WINDOWS) == name
    assert bundle.asset_url(WINDOWS) == (
        "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/"
        + name.replace("+", "%2B")
    )


def test_archive_names_per_os():
    assert bundle.archive_name(WINDOWS) == "frame-demo-windows-x64.zip"
    assert bundle.archive_name(MAC_ARM) == "frame-demo-macos-arm64.tar.gz"
    assert bundle.archive_name(LINUX) == "frame-demo-linux-x64.tar.gz"


def test_a_tampered_runtime_archive_is_rejected_and_kept_if_user_supplied(tmp_path):
    fake = tmp_path / "python.tar.gz"
    fake.write_bytes(b"not the real runtime")
    with pytest.raises(bundle.BundleError, match="SHA-256"):
        bundle.fetch_python(LINUX, tmp_path / "cache", local_archive=fake)
    assert fake.exists()


def test_a_matching_runtime_archive_is_accepted(tmp_path):
    fake = tmp_path / "python.tar.gz"
    fake.write_bytes(b"runtime")
    target = bundle.Target(LINUX.triple, LINUX.label, LINUX.title, hashlib.sha256(b"runtime").hexdigest())
    assert bundle.fetch_python(target, tmp_path / "cache", local_archive=fake) == fake


def _tar(path: Path, name: str, data: bytes = b"x", mode: int = 0o644) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(data), mode
        tar.addfile(info, io.BytesIO(data))
    return path


needs_safe_tar = pytest.mark.skipif(
    not hasattr(tarfile, "data_filter"), reason="tarfile 안전 필터는 Python 3.11.4+ (번들 빌드 요구 사항)"
)


@needs_safe_tar
def test_extract_python_unpacks_the_runtime_folder(tmp_path):
    archive = _tar(tmp_path / "pbs.tar.gz", "python/bin/python3.12", b"#!python", 0o755)
    python_dir = bundle.extract_python(archive, tmp_path / "stage")
    assert (python_dir / "bin" / "python3.12").read_bytes() == b"#!python"


@needs_safe_tar
def test_extract_python_blocks_path_traversal(tmp_path):
    archive = _tar(tmp_path / "evil.tar.gz", "../escaped.txt")
    with pytest.raises(bundle.BundleError):
        bundle.extract_python(archive, tmp_path / "stage")
    assert not (tmp_path / "escaped.txt").exists()


def test_windows_wrappers_are_ascii_crlf_and_use_the_bundled_python(tmp_path):
    bundle.write_wrappers(tmp_path, WINDOWS)
    raw = (tmp_path / "run.cmd").read_bytes()
    assert raw.isascii() and b"\r\n" in raw  # cmd.exe 는 콘솔 코드 페이지로 읽는다
    text = raw.decode()
    assert "python\\python.exe" in text and "-I -m frame.tools.demo --root . run %*" in text
    assert "--root . setup %*" in (tmp_path / "setup.cmd").read_text(encoding="ascii")


def test_posix_wrappers_handle_macos_quarantine_and_use_the_bundled_python(tmp_path):
    bundle.write_wrappers(tmp_path, MAC_ARM)
    raw = (tmp_path / "run.sh").read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert text.startswith("#!/bin/sh")
    assert "com.apple.quarantine" in text and "[y/N]" in text  # 지우기 전에 묻는다
    assert '-I -m frame.tools.demo --root "$DIR" run "$@"' in text
    assert '--root "$DIR" setup "$@"' in (tmp_path / "setup.sh").read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "run.sh").stat().st_mode) == 0o755


def test_readme_matches_the_os(tmp_path):
    bundle.write_readme(tmp_path, WINDOWS)
    windows = (tmp_path / "README.txt").read_text(encoding="utf-8")
    assert "run.cmd" in windows and "차단 해제" in windows and "xattr" not in windows
    assert "C:\\frame" in windows and "\f" not in windows  # 경로 길이 안내 (역슬래시가 제어 문자로 바뀌지 않게)
    bundle.write_readme(tmp_path, MAC_ARM)
    mac = (tmp_path / "README.txt").read_text(encoding="utf-8")
    assert "sh run.sh" in mac and "xattr" in mac
    bundle.write_readme(tmp_path, LINUX)
    linux = (tmp_path / "README.txt").read_text(encoding="utf-8")
    assert "sh run.sh" in linux and "xattr" not in linux


def test_metadata_marks_the_folder_as_a_bundle(tmp_path):
    bundle.write_metadata(tmp_path, MAC_ARM, REPO)
    assert demo.is_bundle(tmp_path)
    info = demo.bundle_info(tmp_path)
    assert info["label"] == "macos-arm64" and info["target"] == MAC_ARM.triple
    assert info["python"] == bundle.PYTHON_VERSION and info["frame_version"] == frame.__version__
    assert info["git_sha"]


def test_prune_removes_relocation_broken_scripts_but_keeps_python(tmp_path):
    scripts = tmp_path / "python" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pip.exe").write_bytes(b"MZ")
    (tmp_path / "python" / "include").mkdir()
    (tmp_path / "python" / "python.exe").write_bytes(b"MZ")
    cache = tmp_path / "python" / "Lib" / "site-packages" / "openai" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.cpython-312.pyc").write_bytes(b"pyc")
    (cache.parent / "x.py").write_text("", encoding="utf-8")
    bundle.prune(tmp_path, WINDOWS)
    assert not scripts.exists() and not (tmp_path / "python" / "include").exists()
    assert not cache.exists() and (cache.parent / "x.py").exists()  # 캐시는 첫 실행 때 다시 생긴다
    assert (tmp_path / "python" / "python.exe").exists()

    posix = tmp_path / "posix"
    bin_dir = posix / "python" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("python3", "python3.12", "pip3", "uvicorn"):
        (bin_dir / name).write_bytes(b"x")
    bundle.prune(posix, LINUX)
    assert sorted(p.name for p in bin_dir.iterdir()) == ["python3", "python3.12"]


def test_path_budget_stops_windows_bundles_that_are_too_deep(tmp_path):
    stage = tmp_path / "frame-demo-windows-x64"
    deep = stage / "python" / ("d" * 60) / ("e" * 60)
    deep.mkdir(parents=True)
    (deep / "module.py").write_text("", encoding="utf-8")
    with pytest.raises(bundle.BundleError, match="MAX_PATH"):
        bundle.check_path_budget(stage, WINDOWS, budget=120)
    assert bundle.check_path_budget(stage, LINUX, budget=120) > 120  # 경로 제한은 Windows 에만


def test_windows_archive_is_a_zip_under_one_top_folder_with_checksum(tmp_path):
    stage = tmp_path / "frame-demo-windows-x64"
    (stage / "python").mkdir(parents=True)
    (stage / "python" / "python.exe").write_bytes(b"MZ")
    (stage / "run.cmd").write_bytes(b"@echo off\r\n")
    archive = bundle.make_archive(stage, tmp_path / "out", WINDOWS)
    assert archive.name == "frame-demo-windows-x64.zip"
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "frame-demo-windows-x64/python/python.exe" in names
    assert all(n.startswith("frame-demo-windows-x64/") for n in names)
    digest, name = (archive.parent / f"{archive.name}.sha256").read_text(encoding="utf-8").split()
    assert name == archive.name and digest == bundle.sha256_of(archive)


@pytest.mark.skipif(os.name == "nt", reason="실행 권한·심볼릭 링크는 POSIX 에서만 확인")
def test_posix_archive_keeps_exec_bits_and_symlinks(tmp_path):
    stage = tmp_path / "frame-demo-linux-x64"
    bin_dir = stage / "python" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python3.12").write_bytes(b"ELF")
    (bin_dir / "python3.12").chmod(0o755)
    (bin_dir / "python3").symlink_to("python3.12")
    bundle.write_wrappers(stage, LINUX)
    archive = bundle.make_archive(stage, tmp_path / "out", LINUX)
    with tarfile.open(archive) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert members["frame-demo-linux-x64/run.sh"].mode & 0o111
    link = members["frame-demo-linux-x64/python/bin/python3"]
    assert link.issym() and link.linkname == "python3.12" and link.uid == 0


def test_minimal_env_drops_other_python_settings(monkeypatch):
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "elsewhere")
    env = bundle.minimal_env()
    assert not {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"} & set(env)
    assert env["PATH"] and "python" not in env["PATH"].lower()


def test_verify_rejects_a_checksum_mismatch(tmp_path):
    (tmp_path / "frame-demo-windows-x64.zip").write_bytes(b"zip")
    (tmp_path / "frame-demo-windows-x64.zip.sha256").write_text(
        "0" * 64 + "  frame-demo-windows-x64.zip\n", encoding="utf-8"
    )
    with pytest.raises(bundle.BundleError, match="SHA-256"):
        bundle.verify(tmp_path, WINDOWS)


def test_targets_command_lists_every_bundle(capsys):
    assert bundle.main(["targets"], root=REPO) == 0
    out = capsys.readouterr().out
    assert all(t.label in out for t in bundle.TARGETS.values())
