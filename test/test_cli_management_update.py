from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace
from io import StringIO

import pytest

from cli import entrypoint_runtime
from cli.management_runtime import install as install_runtime
from cli.management_runtime.commands_runtime import update as update_runtime


class _TtyOutput(StringIO):
    def isatty(self) -> bool:
        return True


class _TtyInput(StringIO):
    def isatty(self) -> bool:
        return True


class _PipeOutput(StringIO):
    def isatty(self) -> bool:
        return False


def _npm_managed_release(monkeypatch, tmp_path: Path, *, version: str = "8.2.1") -> Path:
    package_root = tmp_path / "npm-package"
    script_root = package_root / ".ccb-release" / "ccb-linux-x86_64"
    script_root.mkdir(parents=True)
    (script_root / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (script_root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (script_root / "ccb").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (package_root / "package.json").write_text(
        json.dumps({"name": "@seemseam/ccb", "version": version}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CCB_INSTALL_KIND", "npm")
    monkeypatch.setenv("CCB_NPM_PACKAGE_NAME", "@seemseam/ccb")
    monkeypatch.setenv("CCB_NPM_PACKAGE_ROOT", str(package_root))
    monkeypatch.setenv("CCB_NPM_PACKAGE_VERSION", version)
    return script_root


def _clear_post_update_env(monkeypatch) -> None:
    for name in (
        "CCB_INSTALL_ROLES",
        "CCB_POST_UPDATE_REQUIRED",
        "CCB_POST_UPDATE_TIMEOUT_SECONDS",
        "CCB_ENTRYPOINT_SMOKE_TIMEOUT_SECONDS",
        "CCB_PROVIDER_UPDATE_FLOW",
        "CCB_PROVIDER_UPDATE_MODE",
        "CCB_UPDATE_PROVIDERS",
        "CCB_POST_UPDATE_CACHE_CLEANUP_FLOW",
        "CCB_POST_UPDATE_CACHE_CLEANUP_ENABLED",
        "CCB_POST_UPDATE_MOBILE_HOST_REFRESH_FLOW",
        "CCB_UPDATE_CACHE_CLEANUP",
    ):
        monkeypatch.delenv(name, raising=False)


def test_cmd_update_defaults_to_latest_release(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setenv("CODEX_INSTALL_PREFIX", str(install_dir))
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "pick_temp_base_dir", lambda _install_dir: tmp_base)
    monkeypatch.setattr(update_runtime, "get_available_versions", lambda: ["5.1.0", "5.3.0", "5.2.8"])

    def _fake_update_via_tarball(
        tmp_base_arg,
        *,
        install_dir,
        target_version,
        old_info,
        provider_mode,
        cache_cleanup_enabled,
    ):
        captured["tmp_base"] = tmp_base_arg
        captured["install_dir"] = install_dir
        captured["target_version"] = target_version
        captured["old_info"] = old_info
        captured["provider_mode"] = provider_mode
        captured["cache_cleanup_enabled"] = cache_cleanup_enabled
        return 0

    monkeypatch.setattr(update_runtime, "_update_via_tarball", _fake_update_via_tarball)

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=tmp_path / "script-root")

    assert code == 0
    assert captured["tmp_base"] == tmp_base
    assert captured["install_dir"] == install_dir
    assert captured["target_version"] == "5.3.0"
    assert captured["provider_mode"] == "prompt"
    assert captured["cache_cleanup_enabled"] is True


def test_cmd_update_delegates_npm_managed_install_without_mutating_payload(monkeypatch, tmp_path: Path, capsys) -> None:
    script_root = _npm_managed_release(monkeypatch, tmp_path, version="8.2.1")
    before = (script_root / "VERSION").read_bytes()
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        update_runtime,
        "_update_via_tarball",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("npm payload must not update in place")),
    )
    monkeypatch.setattr(
        update_runtime,
        "get_available_versions",
        lambda: (_ for _ in ()).throw(AssertionError("latest npm update should delegate without a release lookup")),
    )

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=script_root)

    assert code == 0
    assert (script_root / "VERSION").read_bytes() == before
    output = capsys.readouterr().out
    assert "managed by npm" in output
    assert "npm install -g @seemseam/ccb@latest" in output
    assert "Updating to" not in output


def test_cmd_update_resolves_explicit_version_before_npm_delegation(monkeypatch, tmp_path: Path, capsys) -> None:
    script_root = _npm_managed_release(monkeypatch, tmp_path)
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "get_available_versions", lambda: ["8.1.3", "8.2.1", "8.3.0"])

    code = update_runtime.cmd_update(SimpleNamespace(target="8.1"), script_root=script_root)

    assert code == 0
    assert "npm install -g @seemseam/ccb@8.1.3" in capsys.readouterr().out


def test_npm_provenance_rejects_payload_outside_attested_package(monkeypatch, tmp_path: Path) -> None:
    _npm_managed_release(monkeypatch, tmp_path)
    foreign_root = tmp_path / "foreign-release"
    foreign_root.mkdir()

    assert install_runtime.npm_install_provenance(script_root=foreign_root) is None


def test_cmd_update_errors_when_latest_release_cannot_be_resolved(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()

    monkeypatch.setenv("CODEX_INSTALL_PREFIX", str(install_dir))
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "pick_temp_base_dir", lambda _install_dir: tmp_base)
    monkeypatch.setattr(update_runtime, "get_available_versions", lambda: [])

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=tmp_path / "script-root")

    assert code == 1


def test_cmd_update_current_release_runs_provider_flow_without_reinstall(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    provider_modes: list[str] = []

    monkeypatch.setenv("CODEX_INSTALL_PREFIX", str(install_dir))
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "pick_temp_base_dir", lambda _install_dir: tmp_base)
    monkeypatch.setattr(update_runtime, "_resolve_latest_release_version", lambda: "8.3.0")
    monkeypatch.setattr(
        update_runtime,
        "get_version_info",
        lambda _path: {"version": "8.3.0", "commit": "same-build"},
    )
    monkeypatch.setattr(
        update_runtime,
        "_run_provider_updates_nonblocking",
        lambda *, mode: provider_modes.append(mode),
    )
    monkeypatch.setattr(
        update_runtime,
        "_update_via_tarball",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("current CCB must not reinstall")),
    )

    code = update_runtime.cmd_update(
        SimpleNamespace(target=None, providers="check"),
        script_root=tmp_path / "script-root",
    )

    assert code == 0
    assert provider_modes == ["check"]
    assert "Already up to date" in capsys.readouterr().out


def test_cmd_update_windows_uses_release_surface_diagnostic(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        update_runtime,
        "get_available_versions",
        lambda: (_ for _ in ()).throw(AssertionError("diagnostic-only Windows update must not resolve releases")),
    )

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=tmp_path / "script-root")

    assert code == 1
    output = capsys.readouterr().out
    assert "Windows x64 release route is blocked" in output
    assert "Use install.ps1 from a validated Windows release ZIP or source checkout" in output


def test_cmd_update_routes_source_to_fork_without_release_install(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "source-install"
    source_dir.mkdir()
    (source_dir / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (source_dir / ".git").mkdir()
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    calls = []

    def update_source(args, *, script_root):
        calls.append((args.target, script_root))
        return 0

    monkeypatch.setattr(update_runtime, "update_source_from_fork", update_source)
    monkeypatch.setattr(
        update_runtime, "resolve_managed_install_dir",
        lambda **kwargs: pytest.fail('Source updates must not target the release prefix'),
    )

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=source_dir)

    assert code == 0
    assert calls == [(None, source_dir)]


def test_release_artifact_name_uses_linux_arch_aliases(monkeypatch) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "amd64")
    assert update_runtime._release_artifact_name() == "ccb-linux-x86_64.tar.gz"

    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "arm64")
    assert update_runtime._release_artifact_name() == "ccb-linux-aarch64.tar.gz"


def test_release_artifact_name_uses_macos_universal_bundle(monkeypatch) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "arm64")

    assert update_runtime._release_artifact_name() == "ccb-macos-universal.tar.gz"


def test_release_artifact_name_keeps_windows_beta_out_of_stable_update_route(monkeypatch) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "AMD64")

    assert update_runtime._release_artifact_name() is None


def test_release_artifact_url_points_to_release_download() -> None:
    url = update_runtime._release_artifact_url("6.0.0", artifact_name="ccb-linux-x86_64.tar.gz")

    assert url == "https://github.com/bfly123/claude_code_bridge/releases/download/v6.0.0/ccb-linux-x86_64.tar.gz"


def test_release_extract_dir_name_strips_tar_suffixes() -> None:
    assert update_runtime._release_extract_dir_name("ccb-linux-x86_64.tar.gz") == "ccb-linux-x86_64"
    assert update_runtime._release_extract_dir_name("ccb-linux-aarch64.tgz") == "ccb-linux-aarch64"
    assert update_runtime._release_extract_dir_name("ccb-preview.zip") == "ccb-preview"


def test_update_via_tarball_uses_staged_unix_installer(monkeypatch, tmp_path: Path) -> None:
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    calls: dict[str, object] = {}
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")

    def _fake_download(_url: str, destination: Path) -> bool:
        extracted_dir = tmp_base / "payload-src"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        (extracted_dir / "install.sh").write_text("#!/usr/bin/env bash\r\nexit 0\r\n", encoding="utf-8")
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(extracted_dir, arcname="ccb-linux-x86_64")
        return True

    monkeypatch.setattr(update_runtime, "download_tarball", _fake_download)
    monkeypatch.setattr(
        update_runtime,
        "get_version_info",
        lambda _path: {"version": "6.0.8", "commit": "targetbuild"},
    )
    monkeypatch.setattr(update_runtime, "_print_update_outcome", lambda old_info, new_info: None)
    post_update_calls: list[dict[str, object]] = []

    def _fake_run_staged(action: str, *, source_dir: Path, install_dir: Path, extra_env: dict[str, str] | None = None) -> int:
        calls["action"] = action
        calls["source_dir"] = source_dir
        calls["install_dir"] = install_dir
        calls["extra_env"] = dict(extra_env or {})
        return 0

    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", _fake_run_staged)
    monkeypatch.setattr(
        update_runtime,
        "_update_builtin_roles_after_update",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("old updater must not update roles directly")),
    )
    monkeypatch.setattr(
        update_runtime,
        "_run_post_update_with_new_entrypoint",
        lambda **kwargs: post_update_calls.append(dict(kwargs)) or True,
    )

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="6.0.8",
        old_info={"version": "6.0.7"},
    )

    assert code == 0
    assert calls["action"] == "install"
    assert calls["source_dir"].name == update_runtime._release_extract_dir_name(update_runtime._release_artifact_name())
    assert calls["source_dir"].parent != install_dir
    assert calls["install_dir"] == install_dir
    assert calls["extra_env"] == {
        "CODEX_INSTALL_PREFIX": str(install_dir),
        "CCB_CLEAN_INSTALL": "1",
        "CCB_INSTALL_ROLES": "0",
    }
    assert post_update_calls == [
        {
            "install_dir": install_dir,
            "old_info": {"version": "6.0.7"},
            "new_info": {"version": "6.0.8", "commit": "targetbuild"},
            "provider_mode": "prompt",
            "cache_cleanup_enabled": True,
        }
    ]


def test_update_rejects_same_version_different_build_before_install(monkeypatch, tmp_path: Path, capsys) -> None:
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    calls: list[str] = []
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")

    def _fake_download(_url: str, destination: Path) -> bool:
        extracted_dir = tmp_path / "payload-src"
        extracted_dir.mkdir()
        (extracted_dir / "install.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (extracted_dir / "VERSION").write_text("8.1.4\n", encoding="utf-8")
        (extracted_dir / "BUILD_INFO.json").write_text(
            '{"version": "8.1.4", "commit": "newbuild"}\n', encoding="utf-8"
        )
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(extracted_dir, arcname="ccb-linux-x86_64")
        return True

    monkeypatch.setattr(update_runtime, "download_tarball", _fake_download)
    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", lambda *_args, **_kwargs: calls.append("install") or 0)

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="8.1.4",
        old_info={"version": "8.1.4", "commit": "oldbuild"},
    )

    assert code == 1
    assert calls == []
    assert "same-version build identity collision" in capsys.readouterr().out


def test_update_restores_prior_prefix_when_installer_fails(monkeypatch, tmp_path: Path) -> None:
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "identity.txt").write_text("old build\n", encoding="utf-8")
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")

    def _fake_download(_url: str, destination: Path) -> bool:
        extracted_dir = tmp_path / "payload-src"
        extracted_dir.mkdir()
        (extracted_dir / "install.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (extracted_dir / "VERSION").write_text("8.1.3\n", encoding="utf-8")
        (extracted_dir / "BUILD_INFO.json").write_text(
            '{"version": "8.1.3", "commit": "rollback1"}\n', encoding="utf-8"
        )
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(extracted_dir, arcname="ccb-linux-x86_64")
        return True

    def _fake_install(*_args, **_kwargs) -> int:
        shutil.rmtree(install_dir)
        install_dir.mkdir()
        (install_dir / "identity.txt").write_text("partial replacement\n", encoding="utf-8")
        return 23

    monkeypatch.setattr(update_runtime, "download_tarball", _fake_download)
    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", _fake_install)

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="8.1.3",
        old_info={"version": "8.1.4", "commit": "oldbuild"},
    )

    assert code == 23
    assert (install_dir / "identity.txt").read_text(encoding="utf-8") == "old build\n"


def test_update_uses_external_transaction_when_temp_base_is_inside_prefix(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "identity.txt").write_text("old build\n", encoding="utf-8")
    tmp_base = install_dir / ".tmp"
    tmp_base.mkdir()
    safe_tmp = tmp_path / "safe-tmp"
    safe_tmp.mkdir()
    monkeypatch.setattr(update_runtime.tempfile, "gettempdir", lambda: str(safe_tmp))

    transaction_dir = update_runtime._safe_update_transaction_dir(tmp_base=tmp_base, install_dir=install_dir)
    try:
        assert transaction_dir.parent == safe_tmp
        backup_dir = update_runtime._backup_install_prefix(install_dir=install_dir, transaction_dir=transaction_dir)
        assert backup_dir is not None
        assert (backup_dir / "identity.txt").read_text(encoding="utf-8") == "old build\n"
        assert not (backup_dir / ".tmp" / "previous-install").exists()
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)


def test_update_restores_prior_prefix_when_transaction_base_is_inside_prefix(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "identity.txt").write_bytes(b"old build\x00\n")
    tmp_base = install_dir / ".tmp"
    tmp_base.mkdir()
    safe_tmp = tmp_path / "safe-tmp"
    safe_tmp.mkdir()
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(update_runtime.tempfile, "gettempdir", lambda: str(safe_tmp))

    def _fake_download(_url: str, destination: Path) -> bool:
        source_dir = tmp_path / "payload"
        source_dir.mkdir()
        (source_dir / "install.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (source_dir / "VERSION").write_text("8.1.3\n", encoding="utf-8")
        (source_dir / "BUILD_INFO.json").write_text('{"version":"8.1.3","commit":"newbuild"}', encoding="utf-8")
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(source_dir, arcname="ccb-linux-x86_64")
        return True

    def _fake_install(*_args, **_kwargs) -> int:
        shutil.rmtree(install_dir)
        install_dir.mkdir()
        (install_dir / "identity.txt").write_bytes(b"partial\n")
        return 23

    monkeypatch.setattr(update_runtime, "download_tarball", _fake_download)
    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", _fake_install)

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="8.1.3",
        old_info={"version": "8.1.4", "commit": "oldbuild"},
    )

    assert code == 23
    assert (install_dir / "identity.txt").read_bytes() == b"old build\x00\n"
    assert not list((install_dir / ".tmp").rglob("previous-install"))


def test_new_install_failure_removes_partial_prefix(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    transaction_dir = tmp_path / "transaction"
    transaction_dir.mkdir()

    def _fake_install(*_args, **_kwargs) -> int:
        install_dir.mkdir()
        (install_dir / "partial.txt").write_text("partial\n", encoding="utf-8")
        return 23

    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", _fake_install)
    code = update_runtime.run_staged_unix_installer("install", source_dir=tmp_path, install_dir=install_dir)
    assert code == 23
    update_runtime._restore_install_prefix(install_dir=install_dir, backup_dir=None)
    assert not install_dir.exists()


def test_update_fails_closed_when_no_external_transaction_directory_exists(monkeypatch, tmp_path: Path, capsys) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    tmp_base = install_dir / ".tmp"
    tmp_base.mkdir()
    calls: list[str] = []
    monkeypatch.setattr(update_runtime.tempfile, "gettempdir", lambda: str(tmp_base))
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(update_runtime, "download_tarball", lambda *_args, **_kwargs: calls.append("download") or True)
    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", lambda *_args, **_kwargs: calls.append("install") or 0)

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="8.1.3",
        old_info={"version": "8.1.4", "commit": "oldbuild"},
    )

    assert code == 1
    assert calls == []
    assert "safe external rollback storage" in capsys.readouterr().out


def test_update_rejects_symlinked_install_prefix_before_backup(monkeypatch, tmp_path: Path, capsys) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    install_dir = tmp_path / "install-link"
    install_dir.symlink_to(target_dir, target_is_directory=True)
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    calls: list[str] = []
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(update_runtime, "download_tarball", lambda *_args, **_kwargs: calls.append("download") or True)

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="8.1.3",
        old_info={"version": "8.1.4", "commit": "oldbuild"},
    )

    assert code == 1
    assert calls == []
    assert not list(tmp_base.iterdir())
    assert "symbolic link" in capsys.readouterr().out


def test_restore_failure_retains_external_backup_evidence(monkeypatch, tmp_path: Path, capsys) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "identity.txt").write_text("old build\n", encoding="utf-8")
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    transaction_dir = tmp_path / "safe-transaction"
    transaction_dir.mkdir()
    backup_dir = update_runtime._backup_install_prefix(install_dir=install_dir, transaction_dir=transaction_dir)

    monkeypatch.setattr(update_runtime, "_restore_install_prefix", lambda **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    retained = update_runtime._restore_or_retain_backup(install_dir=install_dir, backup_dir=backup_dir)

    assert retained is True
    assert backup_dir is not None and backup_dir.exists()
    assert "recoverable backup retained at" in capsys.readouterr().out


def test_post_update_delegation_runs_installed_entrypoint(monkeypatch, tmp_path: Path) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ccb_entry = install_dir / "ccb"
    ccb_entry.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def _fake_run(command, **kwargs):
        calls.append({"command": list(command), "kwargs": dict(kwargs)})
        return update_runtime.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "6.0.7"},
        new_info={"version": "6.0.8"},
    )

    assert ok is True
    assert calls[0]["command"] == [str(ccb_entry), "--print-version"]
    assert calls[1]["command"] == [
        str(ccb_entry),
        update_runtime.POST_UPDATE_COMMAND,
        "--from-version",
        "6.0.7",
        "--to-version",
        "6.0.8",
    ]
    assert calls[1]["kwargs"]["cwd"] == Path.cwd()
    assert calls[1]["kwargs"]["env"]["CODEX_INSTALL_PREFIX"] == str(install_dir)
    assert calls[1]["kwargs"]["env"]["CCB_SKIP_STARTUP_UPDATE_CHECK"] == "1"
    assert calls[1]["kwargs"]["env"]["CCB_PROVIDER_UPDATE_FLOW"] == "1"
    assert calls[1]["kwargs"]["env"]["CCB_PROVIDER_UPDATE_MODE"] == "prompt"
    assert calls[1]["kwargs"]["env"]["CCB_POST_UPDATE_CACHE_CLEANUP_FLOW"] == "1"
    assert calls[1]["kwargs"]["env"]["CCB_POST_UPDATE_CACHE_CLEANUP_ENABLED"] == "1"
    assert calls[1]["kwargs"]["env"]["CCB_POST_UPDATE_MOBILE_HOST_REFRESH_FLOW"] == "1"
    assert calls[1]["kwargs"]["timeout"] == update_runtime.POST_UPDATE_WITH_PROVIDERS_TIMEOUT_SECONDS


def test_post_update_without_provider_flow_keeps_short_default_timeout(monkeypatch, tmp_path: Path) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ccb_entry = install_dir / "ccb"
    ccb_entry.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def _fake_run(command, **kwargs):
        calls.append({"command": list(command), "kwargs": dict(kwargs)})
        return update_runtime.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "8.2.0"},
        new_info={"version": "8.3.0"},
        provider_mode="none",
        cache_cleanup_enabled=False,
    )

    assert ok is True
    assert calls[1]["kwargs"]["timeout"] == update_runtime.POST_UPDATE_TIMEOUT_SECONDS
    assert calls[1]["command"][-1] == "--no-cache-cleanup"


def test_post_update_delegation_prefers_current_bin_wrapper(monkeypatch, tmp_path: Path) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    install_dir = tmp_path / "install"
    bin_dir = tmp_path / "bin"
    install_dir.mkdir()
    bin_dir.mkdir()
    (install_dir / "ccb").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    ccb_entry = bin_dir / "ccb"
    ccb_entry.write_text(f'#!/usr/bin/env bash\nexec /venv/bin/python "{install_dir / "ccb"}" "$@"\n', encoding="utf-8")
    calls: list[dict[str, object]] = []

    def _fake_run(command, **kwargs):
        calls.append({"command": list(command), "kwargs": dict(kwargs)})
        return update_runtime.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(update_runtime.sys, "argv", [str(ccb_entry), "update"])
    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "6.0.7"},
        new_info={"version": "6.0.8"},
    )

    assert ok is True
    assert calls[0]["command"] == [str(ccb_entry), "--print-version"]
    assert calls[1]["command"][0] == str(ccb_entry)


def test_post_update_delegation_honors_codex_bin_dir(monkeypatch, tmp_path: Path) -> None:
    _clear_post_update_env(monkeypatch)
    install_dir = tmp_path / "install"
    bin_dir = tmp_path / "custom-bin"
    install_dir.mkdir()
    bin_dir.mkdir()
    (install_dir / "ccb").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    ccb_entry = bin_dir / "ccb"
    # Explicit CODEX_BIN_DIR is authoritative and intentionally bypasses
    # install_dir wrapper target detection.
    ccb_entry.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_run(command, **kwargs):
        calls.append(list(command))
        return update_runtime.subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("CODEX_BIN_DIR", str(bin_dir))
    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "6.0.7"},
        new_info={"version": "6.0.8"},
    )

    assert ok is True
    assert calls[0] == [str(ccb_entry), "--print-version"]
    assert calls[1][0] == str(ccb_entry)


def test_post_update_delegation_warns_without_failing_core_update(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ccb_entry = install_dir / "ccb"
    ccb_entry.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def _fake_run(command, **kwargs):
        if list(command)[-1] == "--print-version":
            return update_runtime.subprocess.CompletedProcess(command, 0)
        return update_runtime.subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "6.0.7"},
        new_info={"version": "6.0.8"},
    )

    captured = capsys.readouterr()
    assert ok is True
    assert "Post-update provisioning exited with code 17" in captured.out
    assert "Core update completed" in captured.out


def test_post_update_delegation_timeout_warns_without_failing_core_update(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    monkeypatch.setenv("CCB_POST_UPDATE_TIMEOUT_SECONDS", "1")
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ccb_entry = install_dir / "ccb"
    ccb_entry.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def _fake_run(command, **kwargs):
        if list(command)[-1] == "--print-version":
            return update_runtime.subprocess.CompletedProcess(command, 0)
        raise update_runtime.subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "6.0.7"},
        new_info={"version": "6.0.8"},
    )

    captured = capsys.readouterr()
    assert ok is True
    assert "Post-update provisioning timed out after 1s" in captured.out
    assert "Core update completed" in captured.out


def test_post_update_required_failure_fails_update(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    monkeypatch.setenv("CCB_POST_UPDATE_REQUIRED", "1")
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ccb_entry = install_dir / "ccb"
    ccb_entry.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def _fake_run(command, **kwargs):
        if list(command)[-1] == "--print-version":
            return update_runtime.subprocess.CompletedProcess(command, 0)
        return update_runtime.subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(update_runtime.subprocess, "run", _fake_run)

    ok = update_runtime._run_post_update_with_new_entrypoint(
        install_dir=install_dir,
        old_info={"version": "6.0.7"},
        new_info={"version": "6.0.8"},
    )

    captured = capsys.readouterr()
    assert ok is False
    assert "Required post-update provisioning exited with code 17" in captured.out


@pytest.mark.parametrize("required_env", ["CCB_INSTALL_ROLES", "CCB_POST_UPDATE_REQUIRED"])
def test_post_update_required_roles_catalog_unavailable_returns_failure(
    monkeypatch,
    tmp_path: Path,
    capsys,
    required_env: str,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv(required_env, "1")
    monkeypatch.setattr(
        update_runtime,
        "role_catalog_status",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("catalog down")),
    )
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda active: None)

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    captured = capsys.readouterr()
    assert code == 1
    assert "Agent Roles catalog unavailable" in captured.out


def test_post_update_refreshes_tmux_ui_without_affecting_provisioning(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_post_update_env(monkeypatch)
    calls: list[bool] = []
    monkeypatch.setenv("CCB_INSTALL_ROLES", "0")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda active: calls.append(active))

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    captured = capsys.readouterr()
    assert code == 0
    assert calls == [True]
    assert "Tmux UI post-update refresh skipped" not in captured.out


def test_post_update_refreshes_active_mobile_host_non_blocking(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_INSTALL_ROLES", "0")
    monkeypatch.setenv("CCB_POST_UPDATE_MOBILE_HOST_REFRESH_FLOW", "1")
    calls: list[Path] = []
    result = SimpleNamespace(pid=321, route_provider="relay")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(
        update_runtime,
        "restart_running_mobile_host_service",
        lambda *, script_root: calls.append(script_root) or result,
    )

    code = update_runtime._run_post_update_provisioning(
        install_dir=tmp_path / "install",
    )

    assert code == 0
    assert calls == [tmp_path / "install"]
    assert "Mobile Host refreshed with the installed CCB version" in capsys.readouterr().out


def test_post_update_mobile_host_refresh_failure_warns_without_rollback(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_INSTALL_ROLES", "0")
    monkeypatch.setenv("CCB_POST_UPDATE_MOBILE_HOST_REFRESH_FLOW", "1")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(
        update_runtime,
        "restart_running_mobile_host_service",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("port busy")),
    )

    code = update_runtime._run_post_update_provisioning(
        install_dir=tmp_path / "install",
    )

    captured = capsys.readouterr().out
    assert code == 0
    assert "Mobile Host post-update refresh failed" in captured
    assert "ccb update mobile" in captured


def test_required_post_update_failure_skips_mobile_host_refresh_before_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_POST_UPDATE_REQUIRED", "1")
    monkeypatch.setenv("CCB_POST_UPDATE_MOBILE_HOST_REFRESH_FLOW", "1")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(
        update_runtime,
        "_update_builtin_roles_after_update",
        lambda **_kwargs: 1,
    )
    monkeypatch.setattr(
        update_runtime,
        "restart_running_mobile_host_service",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("host restarted before required rollback")
        ),
    )

    assert update_runtime._run_post_update_provisioning(
        install_dir=tmp_path / "install",
    ) == 1


def test_post_update_tmux_ui_refresh_failure_is_non_blocking(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_INSTALL_ROLES", "0")
    monkeypatch.setattr(
        update_runtime,
        "set_tmux_ui_active",
        lambda active: (_ for _ in ()).throw(RuntimeError("tmux unavailable")),
    )

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    captured = capsys.readouterr()
    assert code == 0
    assert "Tmux UI post-update refresh skipped: RuntimeError: tmux unavailable" in captured.out


def test_post_update_required_installed_role_update_failure_returns_failure(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_INSTALL_ROLES", "1")
    rows = (
        {
            "role_id": "agentroles.archi",
            "status": "update_available",
            "version": "0.2.0",
            "installed_version": "0.1.0",
        },
    )
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)
    monkeypatch.setattr(update_runtime, "cmd_roles", lambda *_args, **_kwargs: 42)

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    captured = capsys.readouterr()
    assert code == 1
    assert "Role Pack update failed: agentroles.archi" in captured.out
    assert "Role Pack updates had 1 failure" in captured.out


def test_post_update_does_not_prompt_for_new_non_default_role_install(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_INSTALL_ROLES", "1")
    calls: list[list[str]] = []
    workflow_ids = (
        'agentroles.ccb_checker', 'agentroles.ccb_clarification_broker',
        'agentroles.ccb_plan_reviewer', 'agentroles.ccb_round_checker',
        'agentroles.ccb_worker', 'agentroles.su_ccb',
    )
    rows = (
        {
            "role_id": "agentroles.ccb_self",
            "status": "available",
            "version": "0.1.0",
            "name": "CCB Self",
            "description": "Maintains CCB projects.",
        },
        {
            "role_id": "agentroles.new",
            "status": "available",
            "version": "0.1.0",
            "name": "New Role",
            "description": "New catalog role.",
        },
    )

    rows += tuple({'role_id': role_id, 'status': 'available', 'version': '0.1.0'}
                  for role_id in workflow_ids)

    class _TtyInput:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            raise AssertionError("post-update role provisioning should not prompt")

    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.sys, "stdin", _TtyInput())
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)

    def _fake_cmd_roles(argv, **_kwargs):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(update_runtime, "cmd_roles", _fake_cmd_roles)

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    captured = capsys.readouterr()
    assert code == 0
    output = stdout.getvalue() + captured.out
    assert calls == [["install", "agentroles.ccb_self"]]
    assert "New Agent Roles available in the catalog" in output
    assert "agentroles.new v0.1.0" in output
    assert "intro: New catalog role." in output
    assert "install: ccb roles install agentroles.new" in output
    assert "bind:    ccb roles add agentroles.new:<provider>" in output
    assert "Install newly available Agent Roles now?" not in output
    for role_id in workflow_ids:
        assert role_id not in output


def test_catalog_followups_with_only_workflow_preview_roles_are_quiet(capsys) -> None:
    update_runtime._print_catalog_followups((
        {'role_id': 'agentroles.ccb_worker', 'status': 'available'},
        {'role_id': 'agentroles.su_ccb', 'status': 'available'},
    ))
    assert capsys.readouterr().out == ''


def test_workflow_preview_missing_source_diagnostic_is_preserved(capsys) -> None:
    update_runtime._print_catalog_followups((
        {'role_id': 'agentroles.ccb_worker', 'status': 'installed_source_missing',
         'path': '/missing/role'},
    ))
    assert 'Installed Role Pack source missing: agentroles.ccb_worker (/missing/role)' in capsys.readouterr().out


def test_post_update_required_default_role_install_failure_returns_failure(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_INSTALL_ROLES", "1")
    rows = (
        {
            "role_id": "agentroles.ccb_self",
            "status": "available",
            "version": "0.1.0",
            "name": "CCB Self",
            "description": "Maintains CCB projects.",
        },
    )
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)
    monkeypatch.setattr(update_runtime, "cmd_roles", lambda *_args, **_kwargs: 42)

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    captured = capsys.readouterr()
    assert code == 1
    assert "Default Role Pack install failed: agentroles.ccb_self" in captured.out
    assert "Default Role Pack installs had 1 failure" in captured.out


def test_post_update_internal_command_runs_new_process_provisioning(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        update_runtime,
        "_update_builtin_roles_after_update",
        lambda **kwargs: calls.append({"roles": dict(kwargs)}),
    )

    code = update_runtime.maybe_handle_post_update_command(
        [update_runtime.POST_UPDATE_COMMAND, "--from-version", "6.0.7", "--to-version", "6.0.8"],
        script_root=install_dir,
    )

    assert code == 0
    assert calls == [{"roles": {"install_dir": install_dir}}]


def test_post_update_internal_command_runs_provider_flow_when_authorized(
    monkeypatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "install"
    provider_modes: list[str] = []
    monkeypatch.setenv("CCB_PROVIDER_UPDATE_FLOW", "1")
    monkeypatch.setenv("CCB_PROVIDER_UPDATE_MODE", "all")
    monkeypatch.setattr(update_runtime, "_update_builtin_roles_after_update", lambda **_kwargs: 0)
    monkeypatch.setattr(
        update_runtime,
        "_run_provider_updates_nonblocking",
        lambda *, mode: provider_modes.append(mode),
    )

    code = update_runtime.maybe_handle_post_update_command(
        [update_runtime.POST_UPDATE_COMMAND, "--from-version", "8.2.0", "--to-version", "8.3.0"],
        script_root=install_dir,
    )

    assert code == 0
    assert provider_modes == ["all"]


def test_post_update_internal_command_runs_cache_migration_when_authorized(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_post_update_env(monkeypatch)
    install_dir = tmp_path / "install"
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("CCB_POST_UPDATE_CACHE_CLEANUP_FLOW", "1")
    monkeypatch.setenv("CCB_POST_UPDATE_CACHE_CLEANUP_ENABLED", "1")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(update_runtime, "_update_builtin_roles_after_update", lambda **_kwargs: 0)
    monkeypatch.setattr(
        update_runtime,
        "run_post_update_provider_cache_cleanup",
        lambda **kwargs: calls.append(dict(kwargs)),
    )

    code = update_runtime.maybe_handle_post_update_command(
        [update_runtime.POST_UPDATE_COMMAND, "--from-version", "8.3.0", "--to-version", "8.4.0"],
        script_root=install_dir,
    )

    assert code == 0
    assert calls == [
        {
            "from_version": "8.3.0",
            "to_version": "8.4.0",
            "cwd": Path.cwd(),
        }
    ]


def test_post_update_cache_migration_opt_out_is_honored(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_POST_UPDATE_CACHE_CLEANUP_FLOW", "1")
    monkeypatch.setenv("CCB_POST_UPDATE_CACHE_CLEANUP_ENABLED", "0")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(update_runtime, "_update_builtin_roles_after_update", lambda **_kwargs: 0)
    monkeypatch.setattr(
        update_runtime,
        "run_post_update_provider_cache_cleanup",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cleanup must be skipped")),
    )

    code = update_runtime.maybe_handle_post_update_command(
        [
            update_runtime.POST_UPDATE_COMMAND,
            "--from-version",
            "8.3.0",
            "--to-version",
            "8.4.0",
            "--no-cache-cleanup",
        ],
        script_root=tmp_path / "install",
    )

    assert code == 0


def test_post_update_cache_migration_failure_does_not_fail_core_update(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_POST_UPDATE_CACHE_CLEANUP_FLOW", "1")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(update_runtime, "_update_builtin_roles_after_update", lambda **_kwargs: 0)
    monkeypatch.setattr(
        update_runtime,
        "run_post_update_provider_cache_cleanup",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("disk busy")),
    )

    code = update_runtime._run_post_update_provisioning(
        install_dir=tmp_path / "install",
        from_version="8.3.0",
        to_version="8.4.0",
    )

    assert code == 0
    assert "core update is unaffected" in capsys.readouterr().out


def test_required_post_update_failure_skips_cache_migration_before_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_POST_UPDATE_CACHE_CLEANUP_FLOW", "1")
    monkeypatch.setenv("CCB_POST_UPDATE_REQUIRED", "1")
    monkeypatch.setattr(update_runtime, "set_tmux_ui_active", lambda _active: None)
    monkeypatch.setattr(update_runtime, "_update_builtin_roles_after_update", lambda **_kwargs: 1)
    monkeypatch.setattr(
        update_runtime,
        "run_post_update_provider_cache_cleanup",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cleanup must wait for a committed update")),
    )

    code = update_runtime._run_post_update_provisioning(install_dir=tmp_path / "install")

    assert code == 1


def test_entrypoint_routes_internal_post_update_command(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        entrypoint_runtime,
        "maybe_handle_post_update_command",
        lambda tokens, *, script_root: calls.append({"tokens": list(tokens), "script_root": script_root}) or 0,
    )

    code = entrypoint_runtime.run_cli_entrypoint(
        [update_runtime.POST_UPDATE_COMMAND, "--from-version", "6.0.7"],
        version="6.0.8",
        script_root=tmp_path / "install",
        cwd=tmp_path,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert code == 0
    assert calls == [
        {
            "tokens": [update_runtime.POST_UPDATE_COMMAND, "--from-version", "6.0.7"],
            "script_root": tmp_path / "install",
        }
    ]


def test_post_update_required_env_accepts_roles_without_prompt(monkeypatch, tmp_path: Path) -> None:
    _clear_post_update_env(monkeypatch)
    monkeypatch.setenv("CCB_POST_UPDATE_REQUIRED", "1")
    rows = (
        {
            "role_id": "agentroles.archi",
            "status": "current",
            "version": "0.2.0",
            "installed_version": "0.2.0",
        },
    )

    class _TtyInput:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            raise AssertionError("required post-update roles should not prompt")

    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.sys, "stdin", _TtyInput())
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)
    monkeypatch.setattr(
        update_runtime,
        "cmd_roles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("current roles should not update")),
    )

    code = update_runtime._update_builtin_roles_after_update(install_dir=tmp_path / "install")

    assert code == 0
    assert "Refresh installed Agent Roles" not in stdout.getvalue()
    assert "Installed Role Packs already match the catalog" in stdout.getvalue()


def test_update_roles_defaults_without_prompt(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    rows = (
        {
            "role_id": "agentroles.archi",
            "status": "update_available",
            "version": "0.2.0",
            "installed_version": "0.1.0",
            "name": "Architecture Reviewer",
            "description": "Reviews architecture drift.",
        },
        {
            "role_id": "agentroles.ccb_self",
            "status": "available",
            "version": "0.1.0",
            "name": "CCB Self",
            "description": "Maintains CCB projects.",
        },
        {
            "role_id": "agentroles.new",
            "status": "available",
            "version": "0.1.0",
            "name": "New Role",
            "description": "New catalog role.",
        },
    )

    class _TtyInput:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            raise AssertionError("post-update role provisioning should not prompt")

    monkeypatch.setattr(update_runtime.sys, "stdin", _TtyInput())
    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)

    def _fake_cmd_roles(argv, *, script_root, cwd, stdout, stderr):
        calls.append({"argv": argv, "script_root": script_root, "cwd": cwd})
        print("role_status: updated", file=stdout)
        return 0

    monkeypatch.setattr(update_runtime, "cmd_roles", _fake_cmd_roles)

    update_runtime._update_builtin_roles_after_update(install_dir=tmp_path / "install")

    assert calls == [
        {"argv": ["update", "agentroles.archi"], "script_root": tmp_path / "install", "cwd": Path.cwd()},
        {"argv": ["install", "agentroles.ccb_self"], "script_root": tmp_path / "install", "cwd": Path.cwd()},
    ]
    assert "Refresh installed and recommended Agent Roles from the catalog now?" not in stdout.getvalue()
    assert "Install newly available Agent Roles now?" not in stdout.getvalue()
    assert "Role Pack updated: agentroles.archi" in stdout.getvalue()
    assert "Default Role Pack installed: agentroles.ccb_self" in stdout.getvalue()
    assert "New Agent Roles available in the catalog" in stdout.getvalue()
    assert "agentroles.new v0.1.0" in stdout.getvalue()
    assert "intro: New catalog role." in stdout.getvalue()
    assert "install: ccb roles install agentroles.new" in stdout.getvalue()


def test_update_roles_current_status_does_not_run_update_hooks(monkeypatch, tmp_path: Path) -> None:
    rows = (
        {
            "role_id": "agentroles.archi",
            "status": "current",
            "version": "0.2.0",
            "installed_version": "0.2.0",
            "name": "Architecture Reviewer",
            "description": "Reviews architecture drift.",
        },
    )

    class _TtyInput:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            return "\n"

    monkeypatch.setattr(update_runtime.sys, "stdin", _TtyInput())
    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)
    monkeypatch.setattr(
        update_runtime,
        "cmd_roles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not update current roles")),
    )

    update_runtime._update_builtin_roles_after_update(install_dir=tmp_path / "install")

    assert "Installed Role Packs already match the catalog" in stdout.getvalue()


def test_update_roles_env_skip_does_not_update(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCB_INSTALL_ROLES", "0")
    rows = (
        {
            "role_id": "agentroles.ccb_self",
            "status": "available",
            "version": "0.1.0",
            "name": "CCB Self",
            "description": "Maintains CCB projects.",
        },
        {
            "role_id": "agentroles.new",
            "status": "available",
            "version": "0.1.0",
            "name": "New Role",
            "description": "New catalog role.",
        },
    )

    class _TtyInput:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            raise AssertionError("CCB_INSTALL_ROLES=0 should not prompt")

    monkeypatch.setattr(update_runtime.sys, "stdin", _TtyInput())
    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)
    monkeypatch.setattr(
        update_runtime,
        "cmd_roles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not update roles")),
    )

    update_runtime._update_builtin_roles_after_update(install_dir=tmp_path / "install")

    assert "Role Pack update skipped by CCB_INSTALL_ROLES=0" in stdout.getvalue()
    assert "agentroles.ccb_self" not in stdout.getvalue()


def test_update_roles_noninteractive_defaults_without_prompt(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    rows = (
        {
            "role_id": "agentroles.ccb_self",
            "status": "available",
            "version": "0.1.0",
            "name": "CCB Self",
            "description": "Maintains CCB projects.",
        },
        {
            "role_id": "agentroles.new",
            "status": "available",
            "version": "0.1.0",
            "name": "New Role",
            "description": "New catalog role.",
        },
    )

    class _PipeInput:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(update_runtime.sys, "stdin", _PipeInput())
    stdout = _PipeOutput()
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(update_runtime, "role_catalog_status", lambda **_kwargs: rows)
    monkeypatch.setattr(update_runtime, "cmd_roles", lambda argv, **_kwargs: calls.append(list(argv)) or 0)

    update_runtime._update_builtin_roles_after_update(install_dir=tmp_path / "install")

    assert calls == [["install", "agentroles.ccb_self"]]
    assert "non-interactive update" not in stdout.getvalue()
    assert "Recommended Agent Roles available" not in stdout.getvalue()
    assert "Default Role Pack installed: agentroles.ccb_self" in stdout.getvalue()
    assert "agentroles.new v0.1.0" in stdout.getvalue()
    assert "intro: New catalog role." in stdout.getvalue()
    assert "install: ccb roles install agentroles.new" in stdout.getvalue()


def test_cmd_update_rich_updates_workbench_without_release_lookup(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[str] = []
    status = {"status": "ok", "enabled": True, "rich_update_status": "ok"}

    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        update_runtime,
        "get_available_versions",
        lambda: (_ for _ in ()).throw(AssertionError("rich update must not resolve CCB releases")),
    )
    monkeypatch.setattr(update_runtime, "update_rich_workbench", lambda: calls.append("update") or status)
    printed: list[dict[str, object]] = []
    monkeypatch.setattr(update_runtime, "print_workbench_status", lambda result: printed.append(dict(result)))

    code = update_runtime.cmd_update(SimpleNamespace(target="rich"), script_root=tmp_path / "script-root")

    captured = capsys.readouterr()
    assert code == 0
    assert calls == ["update"]
    assert printed == [status]
    assert "Installing/updating rich workbench bundle" in captured.out


def test_cmd_update_mobile_runs_onboarding_without_release_lookup(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        update_runtime,
        "get_available_versions",
        lambda: (_ for _ in ()).throw(AssertionError("mobile update must not resolve CCB releases")),
    )

    class _Result:
        def to_record(self):
            return {'service_status': 'started'}

    def _start_or_replace(**kwargs):
        calls.append(dict(kwargs))
        return _Result()

    def _onboarding(*, start_service_fn, listen):
        assert listen == '127.0.0.1:8787'
        service = start_service_fn(
            SimpleNamespace(
                mobile_serve=(
                    'ccb',
                    'mobile',
                    'serve',
                    '--listen',
                    '127.0.0.1:8787',
                    '--public-url',
                    'https://desktop.tailnet.ts.net:8787',
                    '--route-provider',
                    'tailnet',
                )
            ),
            SimpleNamespace(),
        )
        assert service == {'service_status': 'started'}
        return 0

    monkeypatch.setattr(update_runtime, "start_or_replace_mobile_host_service", _start_or_replace)
    monkeypatch.setattr(update_runtime, "run_mobile_update_onboarding", _onboarding)

    code = update_runtime.cmd_update(SimpleNamespace(target="mobile"), script_root=tmp_path / "script-root")

    assert code == 0
    assert calls == [
        {
            'script_root': tmp_path / 'script-root',
            'listen': '127.0.0.1:8787',
            'public_url': 'https://desktop.tailnet.ts.net:8787',
            'route_provider': 'tailnet',
            'rotate_pairing': True,
        }
    ]


def test_cmd_update_mobile_interactive_lan_uses_direct_lan_route(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    stdin = _TtyInput("2\n192.168.31.155:8787\n")
    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.sys, "stdin", stdin)
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(
        update_runtime,
        "suggest_lan_listen_address",
        lambda: "192.168.31.20:8787",
    )

    class _Result:
        def to_record(self):
            return {"route_provider": "lan"}

    monkeypatch.setattr(
        update_runtime,
        "start_or_replace_mobile_host_service",
        lambda **kwargs: calls.append(dict(kwargs)) or _Result(),
    )

    def _lan_onboarding(*, start_service_fn, listen):
        assert listen == "192.168.31.155:8787"
        assert start_service_fn() == {"route_provider": "lan"}
        return 0

    monkeypatch.setattr(update_runtime, "run_mobile_lan_onboarding", _lan_onboarding)
    monkeypatch.setattr(
        update_runtime,
        "run_mobile_update_onboarding",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("LAN route must not enter Tailscale onboarding")
        ),
    )

    code = update_runtime.cmd_update(
        SimpleNamespace(target="mobile"),
        script_root=tmp_path / "script-root",
    )

    assert code == 0
    assert calls == [
        {
            "script_root": tmp_path / "script-root",
            "listen": "192.168.31.155:8787",
            "public_url": None,
            "route_provider": "lan",
            "rotate_pairing": True,
        }
    ]
    assert "Choose how this computer connects" in stdout.getvalue()


def test_cmd_update_mobile_interactive_official_relay_activates_then_starts(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    stdin = _TtyInput("3\n")
    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.sys, "stdin", stdin)
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(
        update_runtime,
        "ensure_guided_relay_credentials",
        lambda **kwargs: (
            calls.append(
                {
                    "credential_mode": kwargs["relay_mode"],
                    "read_fn": kwargs["read_fn"],
                }
            )
            or SimpleNamespace(ready=True, cancelled=False)
        ),
    )

    class _Result:
        def to_record(self):
            return {"route_provider": "relay"}

    monkeypatch.setattr(
        update_runtime,
        "start_or_replace_mobile_host_service",
        lambda **kwargs: calls.append(dict(kwargs)) or _Result(),
    )

    def _relay_onboarding(*, start_service_fn):
        assert start_service_fn() == {"route_provider": "relay"}
        return 0

    monkeypatch.setattr(
        update_runtime,
        "run_mobile_relay_onboarding",
        _relay_onboarding,
    )

    code = update_runtime.cmd_update(
        SimpleNamespace(target="mobile"),
        script_root=tmp_path / "script-root",
    )

    assert code == 0
    assert calls[0]["credential_mode"] == "official"
    assert calls[1]["route_provider"] == "relay"


def test_cmd_update_mobile_interactive_cancel_never_starts_gateway(
    monkeypatch, tmp_path: Path
) -> None:
    stdin = _TtyInput("\n")
    stdout = _TtyOutput()
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime.sys, "stdin", stdin)
    monkeypatch.setattr(update_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(
        update_runtime,
        "start_or_replace_mobile_host_service",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cancelled setup must not touch gateway")
        ),
    )

    code = update_runtime.cmd_update(
        SimpleNamespace(target="mobile"),
        script_root=tmp_path / "script-root",
    )

    assert code == 0
    assert "no gateway was changed" in stdout.getvalue()


def test_cmd_update_mobile_explicit_lan_never_enters_tailscale(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")

    class _Result:
        def to_record(self):
            return {"route_provider": "lan"}

    monkeypatch.setattr(
        update_runtime,
        "start_or_replace_mobile_host_service",
        lambda **kwargs: calls.append(dict(kwargs)) or _Result(),
    )
    monkeypatch.setattr(
        update_runtime,
        "run_mobile_lan_onboarding",
        lambda *, start_service_fn, listen: (start_service_fn() and 0),
    )
    monkeypatch.setattr(
        update_runtime,
        "run_mobile_update_onboarding",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit LAN must not enter Tailscale")
        ),
    )

    code = update_runtime.cmd_update(
        SimpleNamespace(
            target="mobile",
            route_provider="lan",
            listen="10.0.0.8:8787",
            public_url=None,
        ),
        script_root=tmp_path / "script-root",
    )

    assert code == 0
    assert calls[0]["route_provider"] == "lan"
    assert calls[0]["listen"] == "10.0.0.8:8787"


def test_cmd_update_rich_allows_degraded_workbench_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "update_rich_workbench", lambda: {"status": "degraded", "enabled": True})
    monkeypatch.setattr(update_runtime, "print_workbench_status", lambda result: None)

    code = update_runtime.cmd_update(SimpleNamespace(target="rich"), script_root=tmp_path / "script-root")

    assert code == 0


def test_cmd_update_rich_fails_on_failed_workbench_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "update_rich_workbench", lambda: {"status": "failed"})
    monkeypatch.setattr(update_runtime, "print_workbench_status", lambda result: None)

    code = update_runtime.cmd_update(SimpleNamespace(target="rich"), script_root=tmp_path / "script-root")

    assert code == 1


def test_update_via_tarball_uses_macos_release_artifact(monkeypatch, tmp_path: Path) -> None:
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    calls: dict[str, object] = {}
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "arm64")

    def _fake_download(_url: str, destination: Path) -> bool:
        extracted_dir = tmp_base / "payload-src"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        (extracted_dir / "install.sh").write_text("#!/usr/bin/env bash\r\nexit 0\r\n", encoding="utf-8")
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(extracted_dir, arcname="ccb-macos-universal")
        calls["downloaded_to"] = destination
        return True

    monkeypatch.setattr(update_runtime, "download_tarball", _fake_download)
    monkeypatch.setattr(
        update_runtime,
        "get_version_info",
        lambda _path: {"version": "6.0.8", "commit": "targetbuild"},
    )
    monkeypatch.setattr(update_runtime, "_print_update_outcome", lambda old_info, new_info: None)
    monkeypatch.setattr(update_runtime, "_run_post_update_with_new_entrypoint", lambda **_kwargs: True)

    def _fake_run_staged(action: str, *, source_dir: Path, install_dir: Path, extra_env: dict[str, str] | None = None) -> int:
        calls["action"] = action
        calls["source_dir"] = source_dir
        calls["install_dir"] = install_dir
        return 0

    monkeypatch.setattr(update_runtime, "run_staged_unix_installer", _fake_run_staged)

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="6.0.8",
        old_info={"version": "6.0.7"},
    )

    assert code == 0
    assert str(calls["downloaded_to"]).endswith("ccb-macos-universal.tar.gz")
    assert calls["action"] == "install"
    assert calls["source_dir"].name == "ccb-macos-universal"
    assert calls["source_dir"].parent != install_dir
    assert calls["install_dir"] == install_dir


def test_staged_unix_installer_preserves_binary_sidebar_helper(tmp_path: Path) -> None:
    source_dir = tmp_path / "ccb-macos-universal"
    bin_dir = source_dir / "bin"
    bin_dir.mkdir(parents=True)
    (source_dir / "install.sh").write_bytes(b"#!/usr/bin/env bash\r\necho install\r\n")
    (bin_dir / "ask").write_bytes(b"#!/usr/bin/env bash\r\necho ask\r\n")
    sidebar_bytes = b"\xca\xfe\xba\xbe\x00\x00\x00\x02\r\nbinary\rpayload\x00"
    (bin_dir / "ccb-agent-sidebar").write_bytes(sidebar_bytes)

    staging_root, staged_source = install_runtime._stage_unix_installer_tree(source_dir, temp_base=tmp_path)
    try:
        assert (staged_source / "install.sh").read_bytes() == b"#!/usr/bin/env bash\necho install\n"
        assert (staged_source / "bin" / "ask").read_bytes() == b"#!/usr/bin/env bash\necho ask\n"
        assert (staged_source / "bin" / "ccb-agent-sidebar").read_bytes() == sidebar_bytes
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
