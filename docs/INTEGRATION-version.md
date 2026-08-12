# 版本端点集成说明(/api/version)

版本端点实现在独立 router:`src/insar_agent/api/version_router.py`,
本分支不改 `app.py`,由集成分支在 `src/insar_agent/api/app.py` 挂载 —— 文件头
import 区加一行,`create_app()` 内 `app = FastAPI(...)` 之后加一行:

```python
from insar_agent.api.version_router import router as version_router

app.include_router(version_router)
```

## 挂载后生效的端点

| 端点 | 返回 |
|---|---|
| `GET /api/version` | `{version, git_head, python, platform, build: {frozen}}` |
| `GET /api/version/check` | 未配置 `INSAR_UPDATE_MANIFEST` 时 `{update_available: false, reason: "未配置更新源"}`;配置后拉清单做语义化版本比较,返回 `{update_available, current, latest, notes, url}` |

无外部依赖变化:语义化比较是纯标准库实现;拉清单优先 httpx(已有 dev 依赖),
未安装时自动回落 urllib。git 子进程带 `CREATE_NO_WINDOW` 与 5 秒超时,
失败降级为 `null`,不会弹窗、不会拖慢接口。

## 验证

```powershell
.venv\Scripts\python.exe -m insar_agent.api.app   # 起后端(默认 8873)
# 另一终端:
curl http://127.0.0.1:8873/api/version
curl http://127.0.0.1:8873/api/version/check      # 未配更新源 → update_available: false

# 端点级测试(只跑本文件,禁止全量 pytest):
.venv\Scripts\python.exe -m pytest tests/test_version_router.py -q
```

与桌面自动更新的分工见 `desktop/updater/UPDATER.md`。
