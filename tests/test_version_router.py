r"""version_router 独立验收:单独起一个 FastAPI 挂 router,不经 create_app。

只跑本文件(其余测试会起子进程,禁止全量 pytest):
    .venv\Scripts\python.exe -m pytest tests/test_version_router.py -q
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import insar_agent
from insar_agent.api import version_router
from insar_agent.api.version_router import compare_versions


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(version_router.router)
    return TestClient(app)


# ---------------- GET /api/version:响应形状 ----------------


def test_version_shape(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"version", "git_head", "python", "platform", "build"}
    assert data["version"] == insar_agent.__version__
    assert isinstance(data["python"], str) and data["python"].count(".") >= 1
    assert isinstance(data["platform"], str) and data["platform"]
    assert data["build"] == {"frozen": False}  # 测试环境非 PyInstaller 冻结


def test_version_git_head_short_hash_or_null(client):
    head = client.get("/api/version").json()["git_head"]
    assert head is None or re.fullmatch(r"[0-9a-f]{7,40}", head)


def test_version_git_head_degrades_to_null(client, monkeypatch):
    """git 不可用(OSError/超时)时 git_head 必须降级为 null,端点仍 200。"""

    def boom(*args, **kwargs):
        raise OSError("git 不存在")

    monkeypatch.setattr(version_router.subprocess, "run", boom)
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert resp.json()["git_head"] is None


# ---------------- compare_versions:语义化比较边界 ----------------


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # 逐段数值比较,不是字符串比较
        ("1.0.10", "1.0.9", 1),
        ("1.0.9", "1.0.10", -1),
        ("1.2.3", "1.2.3", 0),
        ("2.0.0", "1.99.99", 1),
        # 段数不足按 0 补齐
        ("1.2", "1.2.0", 0),
        ("1.2.1", "1.2", 1),
        # 预发布 < 正式
        ("1.0.0-alpha", "1.0.0", -1),
        ("1.0.0", "1.0.0-rc.1", 1),
        # SemVer 2.0.0 §11 预发布优先级链
        ("1.0.0-alpha", "1.0.0-alpha.1", -1),
        ("1.0.0-alpha.1", "1.0.0-alpha.beta", -1),
        ("1.0.0-alpha.beta", "1.0.0-beta", -1),
        ("1.0.0-beta", "1.0.0-beta.2", -1),
        ("1.0.0-beta.2", "1.0.0-beta.11", -1),
        ("1.0.0-beta.11", "1.0.0-rc.1", -1),
        # 构建元数据忽略;v 前缀容忍
        ("1.0.0+build.5", "1.0.0", 0),
        ("v1.2.3", "1.2.3", 0),
    ],
)
def test_compare_versions(left, right, expected):
    assert compare_versions(left, right) == expected
    assert compare_versions(right, left) == -expected  # 反对称


@pytest.mark.parametrize("bad", ["", "abc", "1.0.x", "1..0", "1.0.0-"])
def test_compare_versions_rejects_garbage(bad):
    with pytest.raises(ValueError):
        compare_versions(bad, "1.0.0")


# ---------------- GET /api/version/check ----------------


def test_check_without_manifest_env(client, monkeypatch):
    monkeypatch.delenv(version_router.MANIFEST_ENV, raising=False)
    data = client.get("/api/version/check").json()
    assert data == {"update_available": False, "reason": "未配置更新源"}


def test_check_update_available(client, monkeypatch):
    monkeypatch.setenv(version_router.MANIFEST_ENV, "https://updates.invalid/latest.json")
    monkeypatch.setattr(
        version_router,
        "_fetch_manifest",
        lambda url, timeout=5.0: {
            "latest": "999.0.0",
            "notes": "大版本",
            "url": "https://updates.invalid/pkg.exe",
        },
    )
    data = client.get("/api/version/check").json()
    assert data["update_available"] is True
    assert data["current"] == insar_agent.__version__
    assert data["latest"] == "999.0.0"
    assert data["notes"] == "大版本"
    assert data["url"] == "https://updates.invalid/pkg.exe"


def test_check_already_latest(client, monkeypatch):
    """清单版本 == 本地版本 → 无更新(严格大于才算有新版)。"""
    monkeypatch.setenv(version_router.MANIFEST_ENV, "https://updates.invalid/latest.json")
    monkeypatch.setattr(
        version_router, "_fetch_manifest",
        lambda url, timeout=5.0: {"latest": insar_agent.__version__},
    )
    data = client.get("/api/version/check").json()
    assert data["update_available"] is False
    assert data["latest"] == insar_agent.__version__


def test_check_prerelease_semantics(client, monkeypatch):
    """预发布段参与端点比较:同号预发布不算更新,更高版本的预发布算更新。"""
    monkeypatch.setenv(version_router.MANIFEST_ENV, "https://updates.invalid/latest.json")

    # 本地正式版 > 同号预发布(0.1.0 > 0.1.0-rc.1)→ 不算更新
    monkeypatch.setattr(
        version_router, "_fetch_manifest",
        lambda url, timeout=5.0: {"latest": f"{insar_agent.__version__}-rc.1"},
    )
    assert client.get("/api/version/check").json()["update_available"] is False

    # 更高版本的预发布((major+1).0.0-rc.1 > 本地)→ 算更新
    next_major = int(insar_agent.__version__.split(".")[0]) + 1
    monkeypatch.setattr(
        version_router, "_fetch_manifest",
        lambda url, timeout=5.0: {"latest": f"{next_major}.0.0-rc.1"},
    )
    assert client.get("/api/version/check").json()["update_available"] is True


def test_check_fetch_failure_degrades(client, monkeypatch):
    monkeypatch.setenv(version_router.MANIFEST_ENV, "https://updates.invalid/latest.json")

    def boom(url, timeout=5.0):
        raise RuntimeError("连接超时")

    monkeypatch.setattr(version_router, "_fetch_manifest", boom)
    data = client.get("/api/version/check").json()
    assert data["update_available"] is False
    assert "获取更新清单失败" in data["reason"]


def test_check_manifest_missing_latest(client, monkeypatch):
    monkeypatch.setenv(version_router.MANIFEST_ENV, "https://updates.invalid/latest.json")
    monkeypatch.setattr(version_router, "_fetch_manifest", lambda url, timeout=5.0: {"notes": "x"})
    data = client.get("/api/version/check").json()
    assert data == {"update_available": False, "reason": "更新清单缺少 latest 字段"}


def test_check_unparseable_latest_degrades(client, monkeypatch):
    monkeypatch.setenv(version_router.MANIFEST_ENV, "https://updates.invalid/latest.json")
    monkeypatch.setattr(
        version_router, "_fetch_manifest", lambda url, timeout=5.0: {"latest": "not-a-version"},
    )
    data = client.get("/api/version/check").json()
    assert data["update_available"] is False
    assert "版本号无法比较" in data["reason"]
