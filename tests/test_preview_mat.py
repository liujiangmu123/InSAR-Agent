"""MATLAB .mat 预览:变量表来自 scipy/h5py;缺库诚实 unsupported;不编造数组。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from insar_agent.preview import mat_view
from insar_agent.preview.dispatch import preview_file
from insar_agent.preview.mat_view import preview_mat

HAS_SCIPY = importlib.util.find_spec("scipy") is not None
HAS_H5PY = importlib.util.find_spec("h5py") is not None
HAS_NUMPY = importlib.util.find_spec("numpy") is not None

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_missing_mat_lib_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mat_view, "_import_scipy_io", lambda: None)
    monkeypatch.setattr(mat_view, "_import_h5py", lambda: None)
    path = tmp_path / "a.mat"
    path.write_bytes(b"not-a-mat")
    result = preview_mat(path)
    assert result.kind == "unsupported"
    assert result.payload["reason"] == "missing_mat_lib"
    assert result.note == "未安装 scipy,无法预览 .mat"
    assert result.png is None
    assert "variables" not in result.payload


@pytest.mark.skipif(HAS_SCIPY, reason="scipy installed")
def test_scipy_missing_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "a.mat"
    path.write_bytes(b"MATLAB 5.0 MAT-file")
    result = preview_file(path)
    assert result.kind == "unsupported"
    assert result.payload["reason"] == "missing_mat_lib"
    assert result.note == "未安装 scipy,无法预览 .mat"


def test_dispatch_registers_mat(tmp_path: Path) -> None:
    path = tmp_path / "x.mat"
    path.write_bytes(b"not-mat")
    result = preview_file(path)
    assert result.payload.get("reason") != "no_handler"


def test_large_2d_does_not_fully_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "big.mat"
    path.write_bytes(b"x")

    class _FakeSio:
        @staticmethod
        def whosmat(_p: str):
            return [("velocity", (4000, 4000), "double")]

        @staticmethod
        def loadmat(*_a, **_k):
            raise AssertionError("must not fully load huge array")

    monkeypatch.setattr(mat_view, "_import_scipy_io", lambda: _FakeSio)
    monkeypatch.setattr(mat_view, "_import_h5py", lambda: None)
    monkeypatch.setattr(mat_view, "_looks_raw_hdf5", lambda _p: False)
    result = preview_mat(path)
    assert result.kind == "mat"
    assert result.png is None
    vel = result.payload["variables"][0]
    assert vel["name"] == "velocity"
    assert list(vel["shape"]) == [4000, 4000]
    assert vel["dtype"] == "double"


@pytest.mark.skipif(not HAS_SCIPY or not HAS_NUMPY, reason="scipy/numpy not installed")
def test_scipy_lists_variables_and_png(tmp_path: Path) -> None:
    import numpy as np
    from scipy.io import savemat

    path = tmp_path / "vel.mat"
    savemat(path, {"velocity": np.arange(16, dtype=np.float32).reshape(4, 4)})
    result = preview_file(path)
    assert result.kind == "mat"
    vel = next(v for v in result.payload["variables"] if v["name"] == "velocity")
    assert list(vel["shape"]) == [4, 4]
    assert vel["dtype"]
    assert result.png is not None
    assert result.png[:8] == _PNG_MAGIC


@pytest.mark.skipif(not HAS_SCIPY or not HAS_NUMPY, reason="scipy/numpy not installed")
def test_char_only_does_not_invent_png(tmp_path: Path) -> None:
    import numpy as np
    from scipy.io import savemat

    path = tmp_path / "label.mat"
    savemat(path, {"label": np.array(["ok"])})
    result = preview_file(path)
    assert result.kind == "mat"
    names = [v["name"] for v in result.payload["variables"]]
    assert "label" in names
    assert result.png is None


@pytest.mark.skipif(not HAS_SCIPY or not HAS_NUMPY, reason="scipy/numpy not installed")
def test_variable_list_capped_at_80(tmp_path: Path) -> None:
    import numpy as np
    from scipy.io import savemat

    path = tmp_path / "many.mat"
    savemat(path, {f"v{i}": np.array([[1.0]]) for i in range(81)})
    result = preview_file(path)
    assert result.kind == "mat"
    assert len(result.payload["variables"]) == 80
    assert result.truncated is True


@pytest.mark.skipif(not HAS_H5PY or not HAS_NUMPY, reason="h5py/numpy not installed")
def test_v73_hdf5_lists_and_png(tmp_path: Path) -> None:
    import h5py
    import numpy as np

    path = tmp_path / "v73.mat"
    with h5py.File(path, "w") as hf:
        hf.create_dataset("velocity", data=np.zeros((8, 8), dtype="float32"))
    result = preview_file(path)
    assert result.kind == "mat"
    vel = next(v for v in result.payload["variables"] if v["name"] == "velocity")
    assert list(vel["shape"]) == [8, 8]
    assert vel["dtype"]
    assert result.png is not None
    assert result.png[:8] == _PNG_MAGIC
