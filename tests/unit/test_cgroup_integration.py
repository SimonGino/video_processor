"""Tests for cgroup PID assignment in uploader."""
import os
from pathlib import Path
from unittest.mock import patch, mock_open

from douyu2bilibili import uploader


def test_assign_pid_skips_when_cgroup_dir_missing():
    """When cgroup directory does not exist, function silently returns."""
    with patch.object(os.path, "exists", return_value=False):
        # Should not raise
        uploader._assign_pid_to_cgroup(12345)


def test_assign_pid_writes_when_cgroup_exists(tmp_path: Path):
    """When cgroup exists, PID is written to cgroup.procs."""
    cgroup_dir = tmp_path / "biliup-limit"
    cgroup_dir.mkdir()
    procs_file = cgroup_dir / "cgroup.procs"
    procs_file.write_text("")

    with patch.object(uploader, "_CGROUP_PROCS_PATH", str(procs_file)):
        uploader._assign_pid_to_cgroup(42)

    assert procs_file.read_text() == "42"


def test_assign_pid_warns_on_permission_error(tmp_path: Path, caplog):
    """When write fails (e.g., permission denied), log warning but don't raise."""
    cgroup_dir = tmp_path / "biliup-limit"
    cgroup_dir.mkdir()
    procs_path = str(cgroup_dir / "cgroup.procs")

    with patch.object(uploader, "_CGROUP_PROCS_PATH", procs_path):
        with patch("builtins.open", side_effect=PermissionError("denied")):
            uploader._assign_pid_to_cgroup(42)

    assert any("无法将 PID" in r.message for r in caplog.records)
