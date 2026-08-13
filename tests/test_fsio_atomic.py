"""core/fsio.atomic_write_text:成功不留 tmp;失败清理 tmp 再抛(REVIEW P2-3)。

统一收口前 ledger/report.script/runtime.render/runtime.jobs 各自手写的
tmp+replace:旧写法在写入与替换之间抛异常(磁盘满/编码错)会残留 *.tmp,
且 render 的固定 tmp 名并发写同一目标互踩。
"""

from __future__ import annotations

import pytest

from insar_agent.core import fsio
from insar_agent.core.fsio import atomic_write_text


def _tmp_leftovers(directory):
    return [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]


def test_atomic_write_success_leaves_no_tmp(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert _tmp_leftovers(tmp_path) == []
    atomic_write_text(target, "v2")  # 覆写同样原子,不留旧 tmp
    assert target.read_text(encoding="utf-8") == "v2"
    assert _tmp_leftovers(tmp_path) == []


def test_atomic_write_newline_control(tmp_path):
    """newline='\\n' 直通(cmd.sh/run.sh 用):CRLF 会让 bash 报错(实测教训)。"""
    target = tmp_path / "run.sh"
    atomic_write_text(target, "a\nb\n", newline="\n")
    assert target.read_bytes() == b"a\nb\n"


def test_atomic_write_replace_failure_cleans_tmp_and_raises(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsio.os, "replace", boom)
    target = tmp_path / "out.json"
    with pytest.raises(OSError):
        atomic_write_text(target, "data")
    assert not target.exists()
    assert _tmp_leftovers(tmp_path) == []  # 失败不残留 *.tmp


def test_atomic_write_encode_failure_cleans_tmp_and_raises(tmp_path):
    """写入阶段失败(编码错)同样清理:ASCII 编码写中文必炸。"""
    target = tmp_path / "out.txt"
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "中文内容", encoding="ascii")
    assert not target.exists()
    assert _tmp_leftovers(tmp_path) == []


def test_render_plan_files_failure_leaves_no_tmp(tmp_path, monkeypatch):
    """接线复核:render_plan_files 经由公共原子写,失败不在工作区残留 tmp。"""
    from insar_agent.runtime.jobs import CommandPlan
    from insar_agent.runtime.render import render_plan_files

    plan = CommandPlan(argv=["x"], cwd=".", env={}, files={"cfg/a.cfg": "hello"})

    def boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsio.os, "replace", boom)
    with pytest.raises(OSError):
        render_plan_files(tmp_path, plan)
    assert list((tmp_path / "cfg").iterdir()) == []  # 目标与 tmp 都不残留


def test_render_plan_files_success_hash_stable(tmp_path):
    """成功路径回归:内容落盘 + 聚合哈希与内容一致(与执行器既有用法对齐)。"""
    from insar_agent.runtime.jobs import CommandPlan
    from insar_agent.runtime.render import render_plan_files

    plan = CommandPlan(argv=["run", "-x"], cwd=".", env={},
                       files={"cfg/a.cfg": "hello", "cfg/b.cfg": "world"})
    h1 = render_plan_files(tmp_path, plan)
    assert (tmp_path / "cfg/a.cfg").read_text(encoding="utf-8") == "hello"
    assert (tmp_path / "cfg/b.cfg").read_text(encoding="utf-8") == "world"
    assert _tmp_leftovers(tmp_path / "cfg") == []
    assert render_plan_files(tmp_path, plan) == h1  # 同输入同哈希(配置指纹稳定)
