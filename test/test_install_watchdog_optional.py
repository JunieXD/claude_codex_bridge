from __future__ import annotations

import os
import shutil
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _run_install_snippet(
    tmp_path: Path,
    body: str,
    *,
    install_sh: Path = INSTALL_SH,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "CODEX_INSTALL_PREFIX": str(tmp_path / "install"),
            "CODEX_BIN_DIR": str(tmp_path / "bin"),
            "CCB_LANG": "en",
            "CCB_INSTALL_ASSUME_YES": "1",
            # macOS login shells put /usr/bin (Python 3.9) first on PATH;
            # build test venvs from the interpreter running the suite.
            "TEST_PYTHON": sys.executable,
        }
    )
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        source {shlex.quote(str(install_sh))}
        {body}
        """
    )
    return subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_install_watchdog_skip_is_successful_and_explicit(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_INSTALL_WATCHDOG=0
        require_python_version >/dev/null
        install_watchdog
        echo done
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "watchdog auto-install skipped" in completed.stdout
    assert "done" in completed.stdout


def test_install_tomli_skip_is_successful_and_explicit(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_INSTALL_TOMLI=0
        require_python_version >/dev/null
        install_tomli
        echo done
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "tomli auto-install skipped" in completed.stdout
    assert "done" in completed.stdout


def test_install_mobile_relay_dependencies_uses_pinned_manifest(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        mkdir -p "$HOME"
        fake_requirements="$HOME/mobile-relay-requirements.txt"
        printf '%s\\n' 'aiohttp==3.13.5' 'cryptography==48.0.0' > "$fake_requirements"
        relay_ready=0
        mobile_relay_requirements_path() { printf '%s\\n' "$fake_requirements"; }
        python_has_mobile_relay_dependencies() { [[ "$relay_ready" == "1" ]]; }
        pip_install_with_index_fallback() {
          shift 2
          printf '%s\\n' "$*" > "$HOME/mobile-relay-pip-argv.txt"
          relay_ready=1
        }
        install_mobile_relay_dependencies_for_python python3
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Mobile Relay Python dependencies installed" in completed.stdout
    pip_argv = (tmp_path / "home" / "mobile-relay-pip-argv.txt").read_text(
        encoding="utf-8"
    )
    assert f"--requirement {tmp_path / 'home' / 'mobile-relay-requirements.txt'}" in pip_argv


def test_install_mobile_relay_dependencies_skip_is_explicit(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        install_mobile_relay_dependencies_for_python python3
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "CCB_INSTALL_MOBILE_RELAY_DEPS=0" in completed.stdout


def test_install_requirements_continue_when_optional_watchdog_is_skipped(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        install_tomli() { echo "tomli stub"; }
        CCB_INSTALL_WATCHDOG=0
        install_mobile_relay_dependencies_for_python() { echo "relay deps:$1"; }
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "tomli stub" in completed.stdout
    assert "watchdog auto-install skipped" in completed.stdout
    assert "relay deps:" in completed.stdout
    assert "tmux stub" in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_resolve_source_kind_recognizes_linked_git_worktree(tmp_path: Path) -> None:
    worktree_root = tmp_path / "linked-worktree"
    worktree_root.mkdir()
    linked_install = worktree_root / "install.sh"
    shutil.copy2(INSTALL_SH, linked_install)
    (worktree_root / ".git").write_text(
        "gitdir: /tmp/example.git/worktrees/linked\n",
        encoding="utf-8",
    )
    completed = _run_install_snippet(
        tmp_path,
        "resolve_source_kind",
        install_sh=linked_install,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert completed.stdout.strip() == "source"


def test_install_role_pack_provisioning_runs_by_default_without_prompt(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        mkdir -p "$CODEX_INSTALL_PREFIX" "$CODEX_BIN_DIR"
        cat > "$CODEX_BIN_DIR/ccb" <<'SH'
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> "$CODEX_INSTALL_PREFIX/ccb-argv.txt"
        exit 0
        SH
        chmod +x "$CODEX_BIN_DIR/ccb"
        check_role_pack_dependencies() { echo "deps:$1"; return 0; }
        provision_role_packs
        cat "$CODEX_INSTALL_PREFIX/ccb-argv.txt"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Role Pack provisioning enabled by default" in completed.stdout
    assert "Install catalog Role Packs and dependencies now?" not in completed.stdout
    assert "Role Pack provisioning skipped in non-interactive install" not in completed.stdout
    assert "roles update agentroles.archi" in completed.stdout
    assert "roles update agentroles.ccb_self" in completed.stdout


def test_install_neovim_provisioning_function_is_removed() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "provision_neovim_tool" not in text
    assert "CCB_INSTALL_NEOVIM" not in text
    assert "tools install neovim" not in text


def test_sourced_install_script_has_no_neovim_provisioning_function(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        if declare -F provision_neovim_tool >/dev/null; then
          echo unexpected-neovim-function
          exit 9
        fi
        echo no-neovim-function
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "no-neovim-function" in completed.stdout


def test_install_requirements_defers_tomli_to_managed_venv(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        install_tomli() { echo unexpected-system-tomli; exit 9; }
        install_watchdog() { echo unexpected-system-watchdog; exit 9; }
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "managed Python venv" in completed.stdout
    assert "unexpected-system-tomli" not in completed.stdout
    assert "unexpected-system-watchdog" not in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_install_requirements_defers_watchdog_to_managed_venv(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        install_watchdog() { echo unexpected-system-watchdog; exit 9; }
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "managed Python venv" in completed.stdout
    assert "unexpected-system-watchdog" not in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_release_install_requirements_never_use_system_pip_by_default(
    tmp_path: Path,
) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        install_tomli() { echo unexpected-system-tomli; exit 9; }
        install_watchdog() { echo unexpected-system-watchdog; exit 9; }
        install_mobile_relay_dependencies_for_python() {
          echo unexpected-system-relay-deps
          exit 9
        }
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "managed Python venv" in completed.stdout
    assert "unexpected-system-tomli" not in completed.stdout
    assert "unexpected-system-watchdog" not in completed.stdout
    assert "unexpected-system-relay-deps" not in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_install_tomli_for_python_uses_real_virtualenv_scope(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/tomli-pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        pathlib.Path(os.environ["PIP_ARGV_MARKER"]).write_text("\\n".join(sys.argv), encoding="utf-8")
        if any(arg.startswith("--user") for arg in sys.argv):
            print("unexpected-user-scope")
            raise SystemExit(9)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "tomli.py").write_text("__version__ = 'test'\\n", encoding="utf-8")
        raise SystemExit(0)
        PY
        python_has_toml_reader() {
          "$PYTHON_BIN" - <<'PY'
        import importlib.util
        import sys

        raise SystemExit(0 if importlib.util.find_spec("tomli") else 1)
        PY
        }
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_tomli_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "unexpected-user-scope" not in completed.stdout
    assert "TOML parser available" in completed.stdout
    pip_argv = (tmp_path / "home" / "tomli-pip-argv.txt").read_text(encoding="utf-8")
    assert "install" in pip_argv
    assert "tomli>=2.0.0" in pip_argv
    assert "--user" not in pip_argv


def test_install_watchdog_for_python_uses_real_virtualenv_scope(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        pathlib.Path(os.environ["PIP_ARGV_MARKER"]).write_text("\\n".join(sys.argv), encoding="utf-8")
        if any(arg.startswith("--user") for arg in sys.argv):
            print("unexpected-user-scope")
            raise SystemExit(9)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "watchdog.py").write_text("__version__ = 'test'\\n", encoding="utf-8")
        raise SystemExit(0)
        PY
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "unexpected-user-scope" not in completed.stdout
    assert "watchdog installed" in completed.stdout
    pip_argv = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8")
    assert "install" in pip_argv
    assert "watchdog>=2.1.0" in pip_argv
    assert "--user" not in pip_argv


def test_install_watchdog_opts_legacy_pip_into_system_truststore(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        if __name__ == "__main__":
            import os
            import pathlib
            import sys

            pathlib.Path(os.environ["PIP_ARGV_MARKER"]).write_text(
                " ".join(sys.argv[1:]) + "\\n",
                encoding="utf-8",
            )
            pathlib.Path(os.environ["FAKE_MODULES_DIR"], "watchdog.py").write_text(
                "__version__ = 'test'\\n",
                encoding="utf-8",
            )
        PY
        pip_should_use_system_truststore() { return 0; }
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        cat "$pip_argv_marker"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "watchdog installed" in completed.stdout
    pip_argv = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8")
    assert "--use-feature=truststore" in pip_argv
    assert "install watchdog>=2.1.0" in pip_argv


def test_legacy_pip_truststore_probe_requires_available_backend(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_distribution="$HOME/fake-distribution"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_distribution/pip-23.1.2.dist-info"
        cat > "$fake_distribution/pip-23.1.2.dist-info/METADATA" <<'EOF'
        Metadata-Version: 2.1
        Name: pip
        Version: 23.1.2
        EOF
        if PYTHONPATH="$fake_distribution" pip_should_use_system_truststore "$venv_dir/bin/python"; then
          echo unexpected-truststore
          exit 9
        fi
        mkdir -p "$fake_distribution/pip/_vendor/truststore"
        PYTHONPATH="$fake_distribution" pip_should_use_system_truststore "$venv_dir/bin/python"
        echo truststore-ready
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "unexpected-truststore" not in completed.stdout
    assert "truststore-ready" in completed.stdout


def test_install_watchdog_retries_macos_tls_failure_with_fallback_index(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        marker = pathlib.Path(os.environ["PIP_ARGV_MARKER"])
        with marker.open("a", encoding="utf-8") as stream:
            stream.write(" ".join(sys.argv[1:]) + "\\n")
        fallback = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
        if fallback not in sys.argv:
            print("SSL: CERTIFICATE_VERIFY_FAILED unable to get local issuer certificate")
            raise SystemExit(1)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "watchdog.py").write_text(
            "__version__ = 'test'\\n", encoding="utf-8"
        )
        raise SystemExit(0)
        PY
        detect_platform() { echo macos; }
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        cat "$pip_argv_marker"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "retrying with" in completed.stdout
    assert "watchdog installed" in completed.stdout
    pip_calls = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(pip_calls) == 2
    assert "--index-url" not in pip_calls[0]
    assert "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple" in pip_calls[1]


def test_install_watchdog_retries_macos_dns_failure_with_fallback_index(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        marker = pathlib.Path(os.environ["PIP_ARGV_MARKER"])
        with marker.open("a", encoding="utf-8") as stream:
            stream.write(" ".join(sys.argv[1:]) + "\\n")
        fallback = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
        if fallback not in sys.argv:
            print(
                "NewConnectionError: [Errno 8] nodename nor servname provided, or not known"
            )
            raise SystemExit(1)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "watchdog.py").write_text(
            "__version__ = 'test'\\n", encoding="utf-8"
        )
        raise SystemExit(0)
        PY
        detect_platform() { echo macos; }
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "retrying with" in completed.stdout
    assert "watchdog installed" in completed.stdout
    pip_calls = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(pip_calls) == 2
    assert "--index-url" not in pip_calls[0]
    assert "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple" in pip_calls[1]


def test_pip_retries_interrupted_download_without_fallback_index(
    tmp_path: Path,
) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        attempt_marker="$HOME/pip-attempt.txt"
        pip_log="$HOME/pip.log"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        argv_marker = pathlib.Path(os.environ["PIP_ARGV_MARKER"])
        with argv_marker.open("a", encoding="utf-8") as stream:
            stream.write(" ".join(sys.argv[1:]) + "\\n")
        attempt_marker = pathlib.Path(os.environ["PIP_ATTEMPT_MARKER"])
        attempt = int(attempt_marker.read_text(encoding="utf-8") or "0") + 1
        attempt_marker.write_text(str(attempt), encoding="utf-8")
        if attempt == 1:
            print(
                "ProtocolError: Connection broken: "
                "IncompleteRead(1024 bytes read, 2048 more expected)"
            )
            raise SystemExit(1)
        raise SystemExit(0)
        PY
        echo 0 > "$attempt_marker"
        detect_platform() { echo linux; }
        PIP_ARGV_MARKER="$pip_argv_marker" \
        PIP_ATTEMPT_MARKER="$attempt_marker" \
        PYTHONPATH="$fake_modules" \
          pip_install_with_index_fallback \
            "$venv_dir/bin/python" "$pip_log" "example-package"
        cat "$pip_argv_marker"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "download was interrupted; retrying (2/3)" in completed.stdout
    pip_calls = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(pip_calls) == 2
    assert all("--index-url" not in call for call in pip_calls)


def test_install_pip_fallback_never_disables_https_verification() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "http://pypi.tuna.tsinghua.edu.cn" not in text
    assert "--trusted-host pypi.tuna.tsinghua.edu.cn" not in text
    assert "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple" in text


def test_install_watchdog_uses_configured_primary_index_without_retry(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        marker = pathlib.Path(os.environ["PIP_ARGV_MARKER"])
        with marker.open("a", encoding="utf-8") as stream:
            stream.write(" ".join(sys.argv[1:]) + "\\n")
        expected = "https://packages.example.test/simple"
        if expected not in sys.argv:
            raise SystemExit(9)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "watchdog.py").write_text(
            "__version__ = 'test'\\n", encoding="utf-8"
        )
        raise SystemExit(0)
        PY
        CCB_PIP_INDEX_URL="https://packages.example.test/simple" \
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "watchdog installed" in completed.stdout
    pip_calls = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(pip_calls) == 1
    assert "--index-url https://packages.example.test/simple" in pip_calls[0]


def test_install_watchdog_can_disable_macos_fallback_index(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        "$TEST_PYTHON" -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        marker = pathlib.Path(os.environ["PIP_ARGV_MARKER"])
        with marker.open("a", encoding="utf-8") as stream:
            stream.write(" ".join(sys.argv[1:]) + "\\n")
        print("SSL: CERTIFICATE_VERIFY_FAILED unable to get local issuer certificate")
        raise SystemExit(1)
        PY
        detect_platform() { echo macos; }
        CCB_PIP_FALLBACK_INDEX_URL=0 \
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "watchdog install failed" in completed.stdout
    assert "retrying with" not in completed.stdout
    pip_calls = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(pip_calls) == 1


def test_preserve_managed_venv_moves_it_into_staging_tree(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        staging="$HOME/staging"
        mkdir -p "$CODEX_INSTALL_PREFIX/.venv" "$staging"
        echo keep > "$CODEX_INSTALL_PREFIX/.venv/marker"
        preserve_managed_venv_in_staging "$staging"
        test ! -e "$CODEX_INSTALL_PREFIX/.venv"
        cat "$staging/.venv/marker"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Preserving managed Python venv" in completed.stdout
    assert "keep" in completed.stdout


def test_install_managed_venv_reuses_healthy_environment(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_TOMLI=0
        CCB_INSTALL_WATCHDOG=0
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        mkdir -p "$CODEX_INSTALL_PREFIX"
        "$TEST_PYTHON" -m venv "$CODEX_INSTALL_PREFIX/.venv"
        echo keep > "$CODEX_INSTALL_PREFIX/.venv/marker"
        pip_needs_system_trust_refresh() { return 1; }
        install_managed_venv
        cat "$CODEX_INSTALL_PREFIX/.venv/marker"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Reusing managed Python venv" in completed.stdout
    assert "keep" in completed.stdout


def test_install_managed_venv_refreshes_legacy_pip_when_reused(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_TOMLI=0
        CCB_INSTALL_WATCHDOG=0
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        pip_argv_marker="$HOME/pip-refresh-argv.txt"
        mkdir -p "$HOME" "$CODEX_INSTALL_PREFIX"
        "$TEST_PYTHON" -m venv "$CODEX_INSTALL_PREFIX/.venv"
        pip_needs_system_trust_refresh() { return 0; }
        pip_install_with_index_fallback() {
          shift 2
          printf '%s\\n' "$*" > "$pip_argv_marker"
          return 0
        }
        install_managed_venv
        cat "$pip_argv_marker"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Reusing managed Python venv" in completed.stdout
    assert "Refreshing managed Python pip for system certificate support" in completed.stdout
    pip_argv = (tmp_path / "home" / "pip-refresh-argv.txt").read_text(encoding="utf-8")
    assert "--upgrade pip>=24.2" in pip_argv


def test_release_managed_venv_wraps_installed_python_entrypoints(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        mkdir -p "$CODEX_INSTALL_PREFIX/bin"
        cat > "$CODEX_INSTALL_PREFIX/bin/_ccb-python" <<'SH'
        #!/usr/bin/env bash
        exec /usr/bin/env python3 "$@"
        SH
        cat > "$CODEX_INSTALL_PREFIX/ccb" <<'PY'
        #!/usr/bin/env python3
        print("ccb")
        PY
        cat > "$CODEX_INSTALL_PREFIX/bin/ask" <<'PY'
        #!/usr/bin/env python3
        print("ask")
        PY
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/codex-reconnect"
        chmod +x "$CODEX_INSTALL_PREFIX/bin/_ccb-python" "$CODEX_INSTALL_PREFIX/ccb" "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook" "$CODEX_INSTALL_PREFIX/bin/codex-reconnect"
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_WATCHDOG=0
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        require_python_version >/dev/null
        install_managed_venv
        install_bin_links
        "$CODEX_BIN_DIR/ccb"
        "$CODEX_BIN_DIR/ask"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "OK: Managed Python venv ready" in completed.stdout
    assert "ccb" in completed.stdout
    assert "ask" in completed.stdout

    wrapper = tmp_path / "bin" / "ccb"
    ask_wrapper = tmp_path / "bin" / "ask"
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert wrapper_text.startswith("#!/usr/bin/env bash")
    assert '[[ "${TERM:-}" == "xterm-ghostty" ]]' in wrapper_text
    assert "export TERM=xterm-256color" in wrapper_text
    assert str(tmp_path / "install" / ".venv" / "bin" / "python") in wrapper_text
    assert str(tmp_path / "install" / ".venv" / "bin" / "python") in ask_wrapper.read_text(encoding="utf-8")


def test_release_managed_venv_wrapper_uses_absolute_target_path(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        mkdir -p "$CODEX_INSTALL_PREFIX/bin"
        cat > "$CODEX_INSTALL_PREFIX/bin/_ccb-python" <<'SH'
        #!/usr/bin/env bash
        exec /usr/bin/env python3 "$@"
        SH
        cat > "$CODEX_INSTALL_PREFIX/ccb" <<'PY'
        #!/usr/bin/env python3
        print("ccb")
        PY
        cat > "$CODEX_INSTALL_PREFIX/bin/ask" <<'PY'
        #!/usr/bin/env python3
        print("ask")
        PY
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/codex-reconnect"
        chmod +x "$CODEX_INSTALL_PREFIX/bin/_ccb-python" "$CODEX_INSTALL_PREFIX/ccb" "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook" "$CODEX_INSTALL_PREFIX/bin/codex-reconnect"
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_WATCHDOG=0
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        install_managed_venv
        install_bin_links
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert str(tmp_path / "install" / "ccb") in (tmp_path / "bin" / "ccb").read_text(encoding="utf-8")


def test_install_managed_venv_selects_python_when_called_directly(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_WATCHDOG=0
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        install_managed_venv
        echo venv-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Managed Python venv ready" in completed.stdout
    assert "venv-ok" in completed.stdout


def test_runtime_bootstrap_forces_release_local_required_dependencies(
    tmp_path: Path,
) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=0
        CCB_INSTALL_TOMLI=0
        CCB_INSTALL_MOBILE_RELAY_DEPS=0
        canonical_existing_parent_path() { echo "$REPO_ROOT"; }
        install_managed_venv() {
          echo "managed:$CCB_USE_MANAGED_VENV:$CCB_INSTALL_TOMLI:$CCB_INSTALL_MOBILE_RELAY_DEPS"
        }
        managed_venv_python() { echo "$HOME/release-python"; }
        python_has_toml_reader() {
          echo "toml:$PYTHON_BIN"
          return 0
        }
        python_has_mobile_relay_dependencies() {
          echo "relay:$1"
          return 0
        }
        runtime_bootstrap
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "managed:1:1:1" in completed.stdout
    assert f"toml:{tmp_path / 'home' / 'release-python'}" in completed.stdout
    assert f"relay:{tmp_path / 'home' / 'release-python'}" in completed.stdout
    assert "Release-local Python runtime ready" in completed.stdout


def test_runtime_bootstrap_rejects_live_source_tree(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=source
        runtime_bootstrap
        """,
    )

    assert completed.returncode == 1
    assert "only supported for a packaged CCB release" in completed.stderr


def test_runtime_bootstrap_rejects_a_different_install_prefix(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        runtime_bootstrap
        """,
    )

    assert completed.returncode == 1
    assert "must target its own packaged release tree" in completed.stderr


def test_release_mode_uses_managed_venv_by_default(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        if ! use_managed_venv; then
          echo missing-managed-venv
          exit 1
        fi
        echo release-managed-venv
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "release-managed-venv" in completed.stdout


def test_release_mode_can_explicitly_disable_managed_venv(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=0
        if use_managed_venv; then
          echo unexpected-managed-venv
          exit 1
        fi
        echo release-managed-venv-disabled
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "release-managed-venv-disabled" in completed.stdout


def test_source_mode_stays_unmanaged_even_when_requested(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=source
        CCB_USE_MANAGED_VENV=1
        if use_managed_venv; then
          echo unexpected-managed-venv
          exit 1
        fi
        echo source-stays-unmanaged
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "source-stays-unmanaged" in completed.stdout


def test_source_dev_mode_does_not_use_managed_venv_by_default(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=source
        if use_managed_venv; then
          echo unexpected-managed-venv
          exit 1
        fi
        echo source-no-venv
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "source-no-venv" in completed.stdout
