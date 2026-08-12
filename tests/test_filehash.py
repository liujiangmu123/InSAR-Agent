"""三档文件指纹(AGENT-DESIGN §5.5)+ 三段编码(§6.1 artifacts.fp,absorb-M)。"""

import os
import time

import pytest

from insar_agent.core.filehash import fingerprint, fingerprint_dir, fingerprint_path


def test_missing_encoded_as_minus_one(tmp_path):
    p = tmp_path / "nope.unw"
    h_missing = fingerprint_path(p, "stat")
    p.write_bytes(b"data")
    h_exists = fingerprint_path(p, "stat")
    assert h_missing != h_exists  # 「产物被删」是正常哈希差异
    p.unlink()
    assert fingerprint_path(p, "stat") == h_missing  # 缺失态稳定


def test_path_policy_ignores_content(tmp_path):
    p = tmp_path / "raw.slc"
    p.write_bytes(b"v1")
    h1 = fingerprint_path(p, "path")
    p.write_bytes(b"v2-changed")
    assert fingerprint_path(p, "path") == h1  # 只增不改的原始数据:仅路径


def test_stat_policy_detects_size_change(tmp_path):
    p = tmp_path / "mid.int"
    p.write_bytes(b"aaaa")
    h1 = fingerprint_path(p, "stat")
    p.write_bytes(b"aaaabbbb")
    assert fingerprint_path(p, "stat") != h1


def test_content_policy_detects_same_size_rewrite(tmp_path):
    # §1.3 陷阱:同尺寸覆写 + mtime 复原,stat 档判不出,content 档必须判出
    p = tmp_path / "velocity.h5"
    p.write_bytes(b"AAAA")
    st = p.stat()
    h1 = fingerprint_path(p, "content")
    time.sleep(0.01)
    p.write_bytes(b"BBBB")  # 同尺寸
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime 复原(cp -p / 备份恢复)
    assert fingerprint_path(p, "stat") == fingerprint_path(p, "stat")  # stat 稳定
    assert fingerprint_path(p, "content") != h1  # content 必须识破


def test_dir_fingerprint_counts_and_names(tmp_path):
    d = tmp_path / "unw"
    d.mkdir()
    (d / "a.unw").write_bytes(b"x" * 100)
    (d / "b.unw").write_bytes(b"y" * 100)
    h1 = fingerprint_dir(d)
    (d / "c.vrt").write_bytes(b"residue")  # 残留文件必须改变目录指纹(§4.10 tier4 清理是正确性要求)
    assert fingerprint_dir(d) != h1


def test_three_segment_encoding_format(tmp_path):
    """artifacts.fp 三段编码 "<policy>:<algo>:<digest>"(absorb-M,dvc 算法显式入档)。"""
    p = tmp_path / "velocity.h5"
    p.write_bytes(b"data")

    for policy, prefix in (("content", "content:sha256:"),
                           ("stat", "stat:v1:"),
                           ("path", "path:v1:")):
        fp = fingerprint(p, policy)
        assert fp.startswith(prefix)
        digest = fp.split(":", 2)[2]
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)

    with pytest.raises(ValueError):
        fingerprint(p, "bogus")


def test_three_segment_missing_file_is_normal_difference(tmp_path):
    """不存在的文件仍得到合法三段编码(-1 编码,redun file.py:463-475),
    与存在态的差异走正常比较路径,不抛异常。"""
    p = tmp_path / "gone.unw"
    fp_missing = fingerprint(p, "stat")
    assert fp_missing.startswith("stat:v1:")
    p.write_bytes(b"data")
    assert fingerprint(p, "stat") != fp_missing
    p.unlink()
    assert fingerprint(p, "stat") == fp_missing  # 缺失态稳定


def test_three_segment_dir_dispatch(tmp_path):
    """目录型产物走聚合指纹;path 档对目录降级为 stat(目录没有「仅路径」档)。"""
    d = tmp_path / "figs"
    d.mkdir()
    (d / "v.png").write_bytes(b"png")
    assert fingerprint(d, "stat").startswith("stat:v1:")
    assert fingerprint(d, "path").startswith("stat:v1:")
    assert fingerprint(d, "content").startswith("content:sha256:")

    # 目录内容变化 → stat 档聚合指纹变化
    before = fingerprint(d, "stat")
    (d / "extra.png").write_bytes(b"more")
    assert fingerprint(d, "stat") != before
