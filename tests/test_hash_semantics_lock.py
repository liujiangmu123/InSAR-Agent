"""哈希语义锁(金样):性能优化的安全网。

指纹是持久化契约 —— 同输入必须同哈希。本文件把一组刁钻输入在
**2026-08 优化前实现** 下的输出记录为字面量断言:

  - 任何优化若改变任一 pre-image 字节或十六进制摘要,这里立刻爆红;
  - 金样值一旦需要变更 = 全量缓存失效,必须显式决策并递增 RECORD_VERSION,
    绝不允许在性能优化 PR 里顺手改。

输入构造函数(_deep_nested 等)与金样字面量(_GOLDEN/_PRE_IMAGES)是
配对录制的:改动任一输入即金样作废,两者都不要动。
"""

import hashlib
import math
from pathlib import Path

import pytest

from insar_agent.core.filehash import (
    _stream_sha,
    fingerprint,
    fingerprint_dir,
    fingerprint_path,
)
from insar_agent.core.fingerprint import (
    RECORD_VERSION,
    args_hash,
    eval_hash,
    local_hash,
    step_hashes,
    task_hash,
)
from insar_agent.core.normalize import (
    UnrepresentableError,
    hash_struct,
    normalize_value,
)

# ---------------------------------------------------------------------------
# 刁钻输入(每次现造新对象,防测试间共享可变结构)
# ---------------------------------------------------------------------------


def _deep_nested() -> dict:
    """深嵌套 dict/list + 中文键 + 大整数 + None/bool + bytes + set。"""
    return {
        "干涉图": {
            "对": [
                {"主": "20240101", "副": "20240113", "垂直基线_m": -37.25,
                 "相干性": [0.85, 0.9, 0.77], "掩膜": None},
                {"主": "20240113", "副": "20240125", "垂直基线_m": 12.0,
                 "相干性": [], "掩膜": True},
            ],
            "参数": {"looks": {"range": 8, "azimuth": 2}, "filter": "goldstein",
                     "alpha": 0.5, "阈值": 1e-13, "偏置": -0.0},
        },
        "大整数": 2 ** 200 + 12345,
        "负大整数": -(2 ** 77),
        "字节": b"\x00\xff\xfe",
        "集合": {3, 1, 2},
        "冻结集": frozenset({"b", "a"}),
        "空": {"dict": {}, "list": [], "str": "", "bytes": b"", "none": None},
        "布尔对": [True, False, 1, 0],
    }


def _deep_list_100():
    """100 层纯嵌套 list(递归深度探底)。"""
    v: object = 42
    for _ in range(100):
        v = [v]
    return v


def _mixed_keys() -> dict:
    """key 类型混杂:int / str / bool / 中文,排序靠已规范化的 key 字节。"""
    return {2: "int键", "2": "str键", True: "bool键", "中文键": [1, 2]}


def _float_cases() -> dict[str, float]:
    """浮点归一的所有分支:零/负零/整值/极小/12位截断/2**53 边界/nan/inf。"""
    return {
        "zero": 0.0,
        "neg_zero": -0.0,
        "int_like": 2.0,
        "sci_int_like": 4e15,
        "tiny": 1e-13,
        "neg_tiny": -1e-13,
        "third": 1 / 3,
        "sum_point3": 0.1 + 0.2,
        "big": 1.23456789012345e300,
        "nan": float("nan"),
        "inf": math.inf,
        "neg_inf": -math.inf,
        "at_2_53": 2.0 ** 53,        # abs < 2**53 为 False → 走 float 格式化
        "below_2_53": 2.0 ** 53 - 1,  # 整值且 < 2**53 → 归一为 int
        "neg_2_53": -(2.0 ** 53),
    }


_UPSTREAM_A = "a" * 64
_UPSTREAM_B = "1f" * 32


def _step_kwargs() -> dict:
    return dict(
        capability="insar.unwrap",
        version="2.1.0",
        tool_versions={"snaphu": "2.0.7", "gdal": "3.8.3"},
        method="snaphu_mcf",
        params={"tiles": 4, "coherence_threshold": 0.35, "cpu": 16,
                "colormap": "viridis", "初相位": -0.0, "缩放": 1e-13},
        param_kinds={"cpu": "resource", "colormap": "presentation"},
        upstream_eval_hashes=[_UPSTREAM_B, _UPSTREAM_A],  # 故意逆序,锁排序语义
    )


# ---------------------------------------------------------------------------
# 金样字面量(由优化前实现录制,scripts/bench_hash.py 之前生成;勿手改)
# ---------------------------------------------------------------------------

_GOLDEN: dict[str, str] = {
    "deep_nested":
        "46986193ee7d31e28c43671f9da34a6d0b444d1be041f738a2325cd1b4add2d7",
    "deep_list_100":
        "0c5cf82f66098acc4a4bdb9a780f37bdb68519fdefb8102206f759e949a76a74",
    "mixed_keys":
        "57e3b35558b07861d510e481818a0bce8fb76db8fcb72b323cf6dec00b6bb502",
    "float:zero":
        "bc9e34c1df51acf2c849b487f237da6bfc0b9914d76cdb023dcd0161c55ed440",
    "float:neg_zero":
        "bc9e34c1df51acf2c849b487f237da6bfc0b9914d76cdb023dcd0161c55ed440",
    "float:int_like":
        "5995b713ae5393ab0deb76ac65ed2f5335d480f5849c33dd52dd7e98b0e07edd",
    "float:sci_int_like":
        "0b66ed466bc321484ed07b5bffb85ae6b084cf31d801de2bc612598aa2db0733",
    "float:tiny":
        "05bb4c39d3ca9d5c7f8f191dc3cb37f21649aee6943ecd308e7410a4962356be",
    "float:neg_tiny":
        "2917e5031bd4946ddf562247767e9c156d6df2a928c32b6724ce01ab8aa2d02c",
    "float:third":
        "93a350dedf9c14d9ca95a9b8db4f06692eba0bacd4399c90055e459b6abe99c7",
    "float:sum_point3":
        "04a94959ba830a4cb2a0370b66decfc0681244832d38d1862b2b075d48548733",
    "float:big":
        "4ea29401cc9ef984fd48245b3cb8cccda55f968ed1a53e8c4be1708a4715acde",
    "float:nan":
        "13bc92c22a4a8d1685447d12b5125d1bf8f55d6a369bdd7ba703f779362c09b8",
    "float:inf":
        "c5dd47d673083476f17da39a3e7de980f5b9ec9dd476b49ccb44274d83d7c99a",
    "float:neg_inf":
        "18a39b1f8b79306d27a2c80cd6f62d42346e41cfe5b633fc6a1aa0312e75a152",
    "float:at_2_53":
        "ab1c5c4e9a0dbc8fb54c156c31bc042ace85b590cfe9f2051c2245585cece68c",
    "float:below_2_53":
        "40b2be2c19c905b72b4d2036300b22f45ed094c31f8ee38ce38d632837650d0c",
    "float:neg_2_53":
        "4098105b76fe8261fd7666af1091ad6371ff8ee6eccb7441776c5a9af8768e7c",
    "big_int":
        "258b461e35b8a1339b7637a3395ac4866850475ab7b91a7239e324f88e085af4",
    "neg_big_int":
        "d20c16f40276b95c9148a9856bb389d3f604311afc4f998b9037c842f73894ea",
    "task_hash":
        "d3ee7bff331a791ecb8b94777d391cf5a78058885af774c681b2d171161c58e8",
    "args_hash":
        "e5ebc0ec2076cb4396c65f0738a9e12a04ba4dfd88849345cbe760d25536eeed",
    "local_hash":
        "ec53f8cefa59a1fc55d196173768580e6d1144213778e62a5f872638f0e3b260",
    "eval_hash":
        "e2f26dc8f6056fcec15137522b4ead23451f9777c010f4e924231bd3e6c32681",
    "step_args_hash":
        "34bd0247727773327fc043241f18c67822db9e4e9570a7228255ddce740afd4d",
    "step_local_hash":
        "449d8e23057d4793484ccd4836ef3c3aa3b42f1e12382e5d0ab48a5efadc2b59",
    "step_eval_hash":
        "5acbc40b7a1f9a078294bd6f29e2a323061e566257ab457ea8c8ccd3a38e2e01",
    "fp_path":
        "30d4175abde1b3060e0d397fc4fdfc976426ffa6d4b3b664fd055c8c2fc4ed00",
    "fp_stat_missing":
        "df81aa993c4e037e360257829ac0c2c390d0dd71ae59cd57d61288e1e4a39276",
    "fp_content_missing":
        "a56183b7d834526fcb3129b1bfcd664494c318653751d6e05e39312c96004ee7",
    "fp_path_missing":
        "cc25a6e8ed5024c9d6a9b5ed86aef5c4213710edd3ef5fbcca1f912e56725d9a",
    "fp_dir_missing":
        "677465dbd078e4cf6e7f6bd877fc38dff081fc87969be75903900608165c2fb1",
}

_PRE_IMAGES: dict[str, bytes] = {
    "small_dict": b'DS1:aLI1:1F3:2.5NEE',
    "bool_pair": b'LB1B0E',
    "set_sorted": b'LI1:1I1:2I1:3E',
    "chinese": b'S3:\xe4\xb8\xad',
    "bytes_nul": b'Y1:\x00',
    "neg_zero": b'I1:0',
    "tiny_float": b'F5:1e-13',
    "nan": b'F3:nan',
    "inf": b'F3:inf',
    "neg_inf": b'F4:-inf',
    "int_2_64": b'I20:18446744073709551616',
    "empty_dict": b'DE',
    "empty_list": b'LE',
    "empty_str": b'S0:',
    "empty_bytes": b'Y0:',
    "none": b'N',
    "nested_11_vs": b'LLI1:1ELI1:2EE',
    "nested_12": b'LLI1:1I1:2EE',
}


# ---------------------------------------------------------------------------
# hash_struct 金样
# ---------------------------------------------------------------------------


def test_deep_nested_golden():
    assert hash_struct(_deep_nested()) == _GOLDEN["deep_nested"]
    # 幂等:同一结构两次现造,逐字节同像
    assert normalize_value(_deep_nested()) == normalize_value(_deep_nested())


def test_deep_list_100_golden():
    assert hash_struct(_deep_list_100()) == _GOLDEN["deep_list_100"]


def test_mixed_keys_golden():
    assert hash_struct(_mixed_keys()) == _GOLDEN["mixed_keys"]


def test_float_cases_golden():
    for name, value in _float_cases().items():
        assert hash_struct(value) == _GOLDEN[f"float:{name}"], f"float case {name!r} 漂移"


def test_big_int_golden():
    assert hash_struct(2 ** 200 + 12345) == _GOLDEN["big_int"]
    assert hash_struct(-(2 ** 77)) == _GOLDEN["neg_big_int"]


# ---------------------------------------------------------------------------
# pre-image 逐字节锁(比摘要更强:直接钉死规范化字节序列)
# ---------------------------------------------------------------------------


def test_pre_images_byte_exact():
    cases: dict[str, object] = {
        "small_dict": {"a": [1, 2.5, None]},
        "bool_pair": (True, False),
        "set_sorted": {3, 1, 2},
        "chinese": "中",
        "bytes_nul": b"\x00",
        "neg_zero": -0.0,
        "tiny_float": 1e-13,
        "nan": float("nan"),
        "inf": math.inf,
        "neg_inf": -math.inf,
        "int_2_64": 2 ** 64,
        "empty_dict": {},
        "empty_list": [],
        "empty_str": "",
        "empty_bytes": b"",
        "none": None,
        "nested_11_vs": [[1], [2]],
        "nested_12": [[1, 2]],
    }
    for name, value in cases.items():
        assert normalize_value(value) == _PRE_IMAGES[name], f"pre-image {name!r} 漂移"


# ---------------------------------------------------------------------------
# 域区分与设计内等价(性质锁,防「顺手统一」)
# ---------------------------------------------------------------------------


def test_domain_separation_locked():
    assert hash_struct(True) != hash_struct(1)          # bool ≠ int
    assert hash_struct(False) != hash_struct(0)
    assert hash_struct("1") != hash_struct(1)           # str ≠ int
    assert hash_struct("abc") != hash_struct(b"abc")    # str ≠ bytes
    assert hash_struct(None) != hash_struct("")         # None ≠ 空串
    assert hash_struct([]) != hash_struct({})           # 空 list ≠ 空 dict
    assert hash_struct([[1], [2]]) != hash_struct([[1, 2]])  # 闭合标记有效


def test_by_design_equivalences_locked():
    assert hash_struct(-0.0) == hash_struct(0.0) == hash_struct(0)   # 零归一
    assert hash_struct(2.0) == hash_struct(2)                        # 整值浮点归 int
    assert hash_struct((1, 2)) == hash_struct([1, 2])                # tuple 视同 list
    assert hash_struct({2, 1}) == hash_struct([1, 2])                # set = 排序后 list 像
    assert hash_struct(frozenset({2, 1})) == hash_struct({1, 2})
    assert hash_struct(0.1 + 0.2) == hash_struct(0.3)                # 12 位有效数字归一
    assert hash_struct({"b": 2, "a": 1}) == hash_struct({"a": 1, "b": 2})


# ---------------------------------------------------------------------------
# 拒绝路径:不可 canonical 化必须显式失败(且保持 TypeError 子类)
# ---------------------------------------------------------------------------


def test_rejection_paths_locked():
    class Opaque:
        pass

    for bad in (object(), complex(2, 3), Opaque(), type, lambda: None):
        with pytest.raises(UnrepresentableError):
            hash_struct(bad)
    # 深埋嵌套同样炸出来,绝不静默丢弃
    with pytest.raises(UnrepresentableError):
        hash_struct([{"x": {"y": object()}}])
    # 捕获语义:UnrepresentableError 必须仍是 TypeError
    assert issubclass(UnrepresentableError, TypeError)


# ---------------------------------------------------------------------------
# fingerprint.py 三段指纹金样
# ---------------------------------------------------------------------------


def test_task_args_local_eval_golden():
    t = task_hash("insar.unwrap", "2.1.0", {"snaphu": "2.0.7", "gdal": "3.8.3"})
    a = args_hash("snaphu_mcf", {"tiles": 4, "coherence_threshold": 0.35, "缩放": 1e-13})
    lo = local_hash("snaphu_mcf",
                    {"tiles": 4, "coherence_threshold": 0.35, "缩放": 1e-13},
                    {"colormap": "viridis"})
    ev = eval_hash(t, a, [_UPSTREAM_B, _UPSTREAM_A])
    assert t == _GOLDEN["task_hash"]
    assert a == _GOLDEN["args_hash"]
    assert lo == _GOLDEN["local_hash"]
    assert ev == _GOLDEN["eval_hash"]
    # 上游是集合语义:声明顺序不影响 eval_hash
    assert ev == eval_hash(t, a, [_UPSTREAM_A, _UPSTREAM_B])


def test_step_hashes_golden():
    out = step_hashes(**_step_kwargs())
    assert out["task_hash"] == _GOLDEN["task_hash"]
    assert out["args_hash"] == _GOLDEN["step_args_hash"]
    assert out["local_hash"] == _GOLDEN["step_local_hash"]
    assert out["eval_hash"] == _GOLDEN["step_eval_hash"]
    # 参数三分类的归属不许漂移(未声明默认 science,保守失效)
    assert out["science_params"] == {"tiles": 4, "coherence_threshold": 0.35,
                                     "初相位": -0.0, "缩放": 1e-13}
    assert out["resource_params"] == {"cpu": 16}
    assert out["presentation_params"] == {"colormap": "viridis"}


def test_record_version_locked():
    # 金样对应的记录格式版本;算法变更必须显式递增此值并重录金样
    assert RECORD_VERSION == 1


# ---------------------------------------------------------------------------
# filehash.py 金样(不碰真实文件系统的纯结构部分)
# ---------------------------------------------------------------------------

_MISS = "语义锁_绝不存在目录/绝不存在.h5"


def test_fingerprint_path_goldens():
    assert fingerprint_path("data/slc/scene_升轨.slc", "path") == _GOLDEN["fp_path"]
    # str 与 Path 入参同像
    assert fingerprint_path(Path("data/slc/scene_升轨.slc"), "path") == _GOLDEN["fp_path"]
    # 不存在 = -1 编码,是正常哈希差异而非异常
    assert fingerprint_path(_MISS, "stat") == _GOLDEN["fp_stat_missing"]
    assert fingerprint_path(_MISS, "content") == _GOLDEN["fp_content_missing"]
    with pytest.raises(ValueError):
        fingerprint_path("x", "checksum")


def test_fingerprint_three_segment_golden():
    assert fingerprint(_MISS, "content") == "content:sha256:" + _GOLDEN["fp_content_missing"]
    assert fingerprint(_MISS, "path") == "path:v1:" + _GOLDEN["fp_path_missing"]
    assert fingerprint_dir("语义锁_绝不存在目录", "stat") == _GOLDEN["fp_dir_missing"]


# ---------------------------------------------------------------------------
# fingerprint_dir 参照实现等价(mtime/绝对路径随机,无法金样;
# 用优化前实现的逐行拷贝作参照,锁遍历顺序 + stat 采集 + 结构编码)
# ---------------------------------------------------------------------------


def _reference_fingerprint_dir(path: Path | str, policy: str = "stat") -> str:
    """2026-08 优化前 fingerprint_dir 的逐行拷贝。勿改。"""
    p = Path(path)
    key = str(p).replace("\\", "/")
    if not p.exists():
        return hash_struct(["Dir", policy, key, -1])
    entries: list[list] = []
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(p)).replace("\\", "/")
        st = f.stat()
        if policy == "content":
            entries.append([rel, st.st_size, _stream_sha(f)])
        else:
            entries.append([rel, st.st_size, st.st_mtime_ns])
    return hash_struct(["Dir", policy, key, len(entries), entries])


def _build_tricky_tree(root: Path) -> None:
    """构造排序陷阱树:点号 vs 分隔符、大小写、中文、空目录、零字节文件。

    'ab.txt' 与 'ab/c.txt':按路径「部件元组」排序时 ab/c.txt 在前
    ('ab' < 'ab.txt'),按整串排序时 ab.txt 在前('.' < '/')——
    优化实现必须复刻 pathlib 的部件元组序,此树专门钉死这一点。
    """
    (root / "ab").mkdir()
    (root / "ab" / "c.txt").write_bytes(b"c")
    (root / "ab.txt").write_bytes(b"ab")
    (root / "B.txt").write_bytes(b"B" * 100)
    (root / "alpha" / "内嵌").mkdir(parents=True)
    (root / "alpha" / "内嵌" / "深_数据.h5").write_bytes(b"\x89HDF" + b"\x00" * 64)
    (root / "alpha" / "zero.bin").write_bytes(b"")
    (root / "空目录").mkdir()
    (root / "z last.dat").write_bytes(b"tail")


@pytest.mark.parametrize("policy", ["stat", "content"])
def test_fingerprint_dir_matches_reference(tmp_path: Path, policy: str):
    root = tmp_path / "tree"
    root.mkdir()
    _build_tricky_tree(root)
    assert fingerprint_dir(root, policy) == _reference_fingerprint_dir(root, policy)


def test_fingerprint_dir_empty_and_str_path(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert fingerprint_dir(empty, "stat") == _reference_fingerprint_dir(empty, "stat")
    # str 入参与 Path 入参同像
    assert fingerprint_dir(str(empty), "stat") == fingerprint_dir(empty, "stat")


def test_fingerprint_entry_on_dir_and_file(tmp_path: Path):
    root = tmp_path / "prod"
    root.mkdir()
    f = root / "velocity.h5"
    data = b"insar" * 1000
    f.write_bytes(data)
    # 目录没有「仅路径」档:path 落到 stat
    assert fingerprint(root, "path") == "stat:v1:" + _reference_fingerprint_dir(root, "stat")
    # 文件 content 档:三段编码 + 结构可重建
    st = f.stat()
    expect = hash_struct(["File", "content", str(f).replace("\\", "/"),
                          st.st_size, hashlib.sha256(data).hexdigest()])
    assert fingerprint(f, "content") == "content:sha256:" + expect


def test_stream_sha_chunking_invariant(tmp_path: Path):
    # 分块大小绝不影响摘要(4MB 流式与整读同值)
    f = tmp_path / "blob.bin"
    data = bytes(range(256)) * 41  # 10496 B,非块整数倍
    f.write_bytes(data)
    whole = hashlib.sha256(data).hexdigest()
    assert _stream_sha(f) == whole
    assert _stream_sha(f, block=7) == whole
