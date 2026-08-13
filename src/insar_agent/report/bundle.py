"""复现包导出:run 的可复现记录一键打包(给同行/审稿人的交付出口)。

项目的核心卖点是「可复现」——provenance.json / run.sh / methods.md / 图件
本就存在或可现生成,但散落在工作区;本模块把它们收进一个内存 zip:

  provenance.json  账本导出(export_provenance,内嵌阈值台账,打包时现生成)
  run.sh           等价裸命令脚本(真实执行过的 cmd.sh 按序拼接,现生成)
  methods.md       方法章节草稿(确定性模板 methods_markdown,不经 LLM,现生成)
  qa.json          质检指标产物(取账本里 basename 为 qa.json 的产物;缺失如实记录)
  figures/*        FIGURE 产物的 PNG 与同名 .json sidecar(缺就跳过)
  MANIFEST.txt     打包时间 / git head / agent_hash / run 状态 / 证据级别 /
                   文件清单(逐个 sha256)与跳过清单

纪律:
  - 纯函数式:只读 store 与工作区,绝不往工作区写任何文件;
  - 大小护栏:单文件 > MAX_FILE_BYTES(50MB)不入包并在 MANIFEST 注明 ——
    复现包交付的是记录与图件,不是大栅格(大栅格走工作区/产物面板);
  - 路径安全:产物路径与 api.app.resolve_artifact_file 同口径复核,
    绝对路径 / 盘符 / ../ 越界的坏 DB 行一律不入包。
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

from insar_agent.audit.contract import Threshold, load_contract
from insar_agent.core.ledger import export_provenance
from insar_agent.core.store import Store
from insar_agent.report.methods import methods_markdown
from insar_agent.report.script import export_run_script

#: 单文件大小护栏(字节):超过即跳过并在 MANIFEST 注明
MAX_FILE_BYTES = 50 * 1024 * 1024


def _resolve_inside(workspace: Path, rel_path: str) -> Path | None:
    """产物相对路径 → 工作区内绝对路径;越界一律 None(同 api.app 的边界口径)。"""
    base = workspace.resolve()
    rel = Path(rel_path)
    if rel.is_absolute() or rel.drive:
        return None
    target = (base / rel).resolve()
    if target == base or not target.is_relative_to(base):
        return None
    return target


def _figure_pngs(store: Store, run_id: str, workspace: Path) -> list[Path]:
    """FIGURE 产物 → 待打包 PNG 列表(文件型直取,目录型枚举顶层 PNG)。"""
    out: list[Path] = []
    for art in store.artifacts_of(run_id):
        if art["kind"] != "FIGURE":
            continue
        target = _resolve_inside(workspace, art["path"])
        if target is None:
            continue
        if target.is_file() and target.suffix.lower() == ".png":
            out.append(target)
        elif target.is_dir():
            out.extend(p for p in sorted(target.iterdir())
                       if p.is_file() and p.suffix.lower() == ".png")
    return out


def _find_qa(store: Store, run_id: str, workspace: Path) -> Path | None:
    """账本里 basename 为 qa.json 的产物(engines/qa.py 与注册表第 11 步的约定)。"""
    for art in store.artifacts_of(run_id):
        if Path(art["path"]).name != "qa.json":
            continue
        target = _resolve_inside(workspace, art["path"])
        if target is not None and target.is_file():
            return target
    return None


def _manifest_text(run: dict, evidence_level: str, entries: list[tuple[str, str]],
                   skipped: list[str]) -> str:
    lines = [
        "insar-agent 复现包 MANIFEST",
        "==========================",
        f"run_id: {run['run_id']}",
        f"打包时间(UTC): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"run 状态: {run['status']}",
        f"git_head: {run['git_head'] or '未记录'}",
        f"agent_hash: {run['agent_hash'] or '未记录'}",
        f"evidence 级别: {evidence_level or '未知'}",
        f"simulated: {bool(run['simulated'])}",
        "",
        f"包含文件({len(entries)} 个,sha256):",
        *(f"  {name}  sha256={digest}" for name, digest in entries),
        "",
    ]
    if skipped:
        lines.append(f"跳过文件({len(skipped)} 个):")
        lines.extend(f"  {item}" for item in skipped)
    else:
        lines.append("跳过文件:无")
    lines += [
        "",
        "> 校验方法:对 zip 内各文件计算 sha256,应与上表逐一一致;",
        "> methods.md 由 provenance.json 确定生成,正文数字均可在账本反查;",
        "> 复现包交付的是记录与图件,不是大栅格 —— 超限文件在跳过清单如实注明。",
    ]
    return "\n".join(lines) + "\n"


def build_repro_bundle(store: Store, run_id: str, workspace: Path, *,
                       contract: dict[str, Threshold] | None = None,
                       max_file_bytes: int = MAX_FILE_BYTES) -> io.BytesIO:
    """run 的复现包 → 内存 zip(BytesIO,指针已回 0)。

    纯函数式:不写工作区。run 不存在抛 KeyError(与 export_run_script 同口径);
    run 状态不做限制 —— 「非 done 不出包」是 API 层的交付纪律,函数层保持可测。
    """
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    contract = contract if contract is not None else load_contract()
    workspace = Path(workspace)

    prov = export_provenance(store, run_id, contract=contract, workspace=workspace)
    entries: list[tuple[str, str]] = []   # (zip 内路径, sha256)
    skipped: list[str] = []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        def add_text(arcname: str, text: str) -> None:
            data = text.encode("utf-8")
            zf.writestr(arcname, data)
            entries.append((arcname, hashlib.sha256(data).hexdigest()))

        def add_disk_file(arcname: str, path: Path) -> bool:
            try:
                size = path.stat().st_size
                if size > max_file_bytes:
                    skipped.append(
                        f"{arcname}  跳过:{size / 1048576:.1f} MB 超过单文件 "
                        f"{max_file_bytes / 1048576:.0f} MB 上限"
                        "(复现包交付记录与图件,不含大栅格)")
                    return False
                data = path.read_bytes()
            except OSError as exc:  # 枚举与读取之间文件可能消失/不可读:如实记录
                skipped.append(f"{arcname}  跳过:读取失败({exc.__class__.__name__})")
                return False
            zf.writestr(arcname, data)
            entries.append((arcname, hashlib.sha256(data).hexdigest()))
            return True

        # 三份现生成的记录:与 /api/provenance、/api/run.sh 的导出口径一致;
        # methods.md 固定走确定性模板(不经 LLM),保证包内数字可在账本反查
        add_text("provenance.json", json.dumps(prov, ensure_ascii=False, indent=1))
        add_text("run.sh", export_run_script(store, run_id, workspace))
        add_text("methods.md", methods_markdown(prov))

        qa_path = _find_qa(store, run_id, workspace)
        if qa_path is not None:
            add_disk_file("qa.json", qa_path)
        else:
            skipped.append("qa.json  跳过:账本无 qa.json 产物或文件缺失")

        packed: set[str] = set()
        for png in _figure_pngs(store, run_id, workspace):
            arcname = f"figures/{png.name}"
            if arcname in packed:  # 多个 FIGURE 产物目录撞名:先到先得,不覆写
                continue
            packed.add(arcname)
            if not add_disk_file(arcname, png):
                continue  # 图没入包(超限/读取失败),sidecar 不留孤儿
            sidecar = png.with_suffix(".json")
            if sidecar.is_file() and f"figures/{sidecar.name}" not in packed:
                packed.add(f"figures/{sidecar.name}")
                add_disk_file(f"figures/{sidecar.name}", sidecar)

        zf.writestr("MANIFEST.txt",
                    _manifest_text(run, str(prov.get("evidence_level") or ""),
                                   entries, skipped).encode("utf-8"))

    buf.seek(0)
    return buf
