# ROADMAP-team — 单机版 → 课题组团队版架构预研

> 性质:**预研文档,不含代码改动**。日期:2026-08-13。
> 场景假设:课题组共用一台工作站/服务器(当前硬件:24 核 32 线程 / 63.7 GB RAM /
> 单 NVMe SSD,WSL 规划配额 20 核 / 40 GB,AGENT-DESIGN §4.11),3–10 人各自跑任务,
> 内网(校园网/实验室局域网)访问,可能有校外访问的次生需求。
> 资料基础:仓库关键路径通读(api/app.py、core/db.py、core/store.py、schema.sql、
> loop/driver.py、runtime/{jobs,wsl,executor,backend_select}.py、AGENT-DESIGN §4)+
> 本机两个轻量实测(scripts/bench_store.py、scripts/stress_api.py,2026-08-13 复跑)+
> 业界方案调研(引用见附录 A)。
> 安全边界不变:**后端进程永远只绑 127.0.0.1,任何阶段都不暴露 0.0.0.0**。

---

## 0. 结论速览

| 决策点 | 一周内落地推荐 | 中期(1–2 月)演进 | 明确不推荐 |
|---|---|---|---|
| 鉴权层 | **Caddy 反代 + basic_auth**(纯内网);若允许装 Tailscale 则用 **Tailscale Serve + ACL**(送校外访问) | Caddy + **Authelia**(forward-auth,加 MFA/统一登录页) | Keycloak / Authentik(3–10 人场景过重);FastAPI 侧自实现登录(安全自担,竞品有前车之鉴) |
| 多用户数据模型 | **方案 B:每用户独立 INSAR_HOME + 端口**(进程隔离,零核心代码改动) | 需要组内协作/互看会话时再做 **方案 A:sessions.owner 多租户** | 多进程共享同一个 insar.db(违反 core/db.py 单进程纪律) |
| 计算仲裁 | 独立 **queue.db**(复用 leases 语义)做跨进程 `io_heavy` 互斥 + 排队提示 | 全局作业队列表 + 队列可见性面板(「你排第 N」) | 直接把现有 leases 表当队列用(互斥语义 ≠ 队列语义) |
| 存储 | **SQLite 原样保留**(10 人 × 10 run/日 压力 < 单写者能力 1%) | 命中 §5.4 触发指标才迁 PostgreSQL | 预防性迁库(无触发指标就迁是纯成本) |

一周落地包(合计约 **3–5.5 人日**):Caddy/Tailscale 鉴权 + 多实例 launcher +
`io_heavy` 全局互斥 + 双用户并发验收。核心代码零修改,可整体回滚。

---

## 1. 现状盘点:与多用户直接相关的十个架构事实

设计推导必须建立在代码事实上。以下每条都有出处。

| # | 事实 | 出处 | 对团队版的含义 |
|---|---|---|---|
| F1 | 服务默认绑 `127.0.0.1:8873`,`INSAR_HOST` 可覆盖但纪律禁止 | `api/app.py:730` | 多用户访问必须经反代/隧道,这恰好是鉴权的天然卡点(§2) |
| F2 | **无任何鉴权层**;`GET /api/sessions` 返回全部会话 | `api/app.py:267-269`;OPTIMIZATION.md P2「API 鉴权:跨机部署前必须」 | 多人接入前必须补,且注定在应用外层补(§2.3) |
| F3 | `sessions` 表**无 owner 列**,会话只有 id/name/mode | `core/schema.sql:8-15` | 方案 A 的最小改动点(§3.1) |
| F4 | `drivers: dict[str, Driver]` 每会话一个 Driver,EventBus/CancelToken/probe 缓存全是**进程内存态** | `api/app.py:172,192-200` | 一个 API 进程 = 一个完整代理宿主;多进程共享状态不可行,进程即隔离边界(§3.2) |
| F5 | `Database`「单进程访问 .db」+ RLock 串行化全部读写;`busy_timeout=5000` 只兜偶发第二连接(备份/巡检) | `core/db.py:45-57` | 多进程共享主库被设计明确排除;团队版要么单实例(A)要么每人一库(B),没有中间态 |
| F6 | run 级租约已接线:`run:{run_id}`,心跳 ~5s,60s 可接管;**资源池仲裁(cpu/mem/io)声明了但未接线** | `loop/driver.py:287-302`;OPTIMIZATION.md P1-2 | 单 run 防双驱已解决;跨 run/跨用户抢资源完全没有仲裁(§4) |
| F7 | 作业后端按引擎路由:isce2/snaphu → WSL 单发行版 `insar`,其余宿主进程 | `runtime/backend_select.py:26-28` | 重型链都挤同一个 WSL VM,它是资源竞争的主战场(§4.3) |
| F8 | 作业目录 `\.jobs/{run_id}/s{step:02d}/a{attempt}`,run_id 含 UUID 片段 | `runtime/executor.py:79-84`;`core/store.py:34-37` | 多实例共用 WSL 发行版时作业目录**天然不冲突**,方案 B 免改 |
| F9 | `runs.workspace`、`steps.log_path`、`steps.job_dir` 存**绝对路径** | `core/store.py:146-161`;`api/app.py:573` | 搬移 INSAR_HOME 会断历史 run 的图件/日志回看,迁移方案必须处理(§6.2) |
| F10 | 前端 fetch 用**根绝对路径** `/api/...` | `prototype/js/fileslive.js:43`、`gallery.js:38` 等 | 反代不能做路径前缀路由(`/u/alice/` → 404),必须**端口或子域**路由(§3.2) |

另有两个运行时事实,直接决定 §4/§5 的结论:

- **WSL2 是单 utility VM**:同一 Windows 账号下所有发行版共享一个 VM、一份
  `.wslconfig` 全局 memory/processors 配额,没有每发行版限额(microsoft/WSL#8570
  至今未实现);服务以一个 Windows 账号跑,所以方案 A/B 的全部 WSL 作业都进同一个
  资源池。多 Windows 账号各起一个 VM 的路线被否:资源不聚合、mirrored 网络只允许
  一个实例(WSL#11015)、DACL 管理混乱(WSL#13096)。
- **单 SSD 是 IO 咽喉**:AGENT-DESIGN §4.11 已定纪律 `io='heavy'` 最多 1 并发,
  两个重 IO 步骤并行总吞吐反降。这条纪律在团队版从「建议」升级为「必须接线」。

---

## 2. 鉴权层选型

### 2.1 候选盘点

前提:F1/F2 决定了鉴权在**应用外层**做是顺势而为——后端保持 127.0.0.1 + 零鉴权代码,
外层组件负责「谁能连进来」和「他是谁」。这也符合仓库一贯的零基础设施纪律。

| 方案 | 形态 | 资源占用 | 上手/运维 | 能给应用「他是谁」吗 | MFA |
|---|---|---|---|---|---|
| ① Caddy/nginx **basic_auth** | 反代指令,bcrypt 口令表 | Caddy 单二进制 ~30-50 MB | Caddyfile 约 10 行;改密码 = 重算 hash + reload | 能(`Authorization` 头可透传用户名,或按端口即身份) | 无 |
| ② **Authelia**(forward-auth) | 单 Go 二进制挂在反代旁 | **~25-50 MB** RAM,可用 SQLite 做后端 | YAML 配置;首次上手 1–2 天 | 能(`Remote-User` 头) | TOTP/WebAuthn 内建 |
| ③ **Authentik**(完整 IdP) | server+worker+PostgreSQL 多容器 | **~250 MB–1.2 GB** RAM | Docker Compose 15–20 分钟起步,UI-first,运维面大 | 能(OIDC/代理 outpost) | 有 |
| ④ **Keycloak**(企业 IdP) | Java/Quarkus + PostgreSQL | **~1.25–4 GB** RAM(Red Hat 文档下限 1.25 GB) | 概念重(realm/client/flow),为百人以上组织设计 | 能(OIDC) | 有 |
| ⑤ FastAPI 侧**自实现** session token | 改应用代码:登录页+cookie+口令库 | 0 额外进程 | 开发 3–4 人日起;密码哈希/CSRF/限速全自担 | 天然能 | 自己写 |
| ⑥ **Tailscale**(网络层) | WireGuard mesh;`tailscale serve` 反代 | 客户端常驻 ~30-80 MB | 每人装客户端登录即可;ACL 是一份 JSON | **能**:Serve 对 tailnet 流量注入 `Tailscale-User-Login` 身份头(v1.44+),并主动剥除入站伪造头 | 依托 Google/MS/GitHub 账号侧 |

关于②③④的重量数据,多个独立评测口径一致(附录 A):Authelia ~25 MB 单二进制;
Authentik 全栈 250 MB 起、官方建议 2 vCPU/2 GB;Keycloak 空载即 1.25 GB 上下。
**结论:Authentik/Keycloak 对 3–10 人是「用航母送快递」——回答任务书原问:
Authentik/Keycloak 确实重,重在常驻内存、独立数据库和概念负担,收益(SAML/LDAP/
复杂 flow)在本场景全部用不上。**

方案⑤单独说:竞品 InSAR_Agent 恰好是反面教材——`session.py` 写了完整多用户体系但
是死代码,密码裸 sha256 无 salt(DESIGN.md:547-551)。我们主打可复现与数据不出域,
自实现鉴权一旦出漏洞是论文级污点;且它把鉴权逻辑焊死进应用,后续换外层方案还要拆。
**不做。**(AGENT-DESIGN §8.4「未鉴权的文件读接口」同一条纪律的延伸。)

### 2.2 Tailscale 方案的特殊契合点

值得单独指出:Tailscale Serve 的官方最佳实践是**后端只监听 localhost,由 serve
代理进来并注入身份头**——这与本项目「不得暴露 0.0.0.0」的既有安全边界逐字吻合,
零改动即合规。ACL 还能做到端口级授权(`dst: ["workstation:8874"]`),正好给
方案 B 的「每用户一端口」当访问控制(§3.2)。额外收益:讲师/学生居家访问天然解决,
不需要校园 VPN。

限制:免费 Personal 档覆盖 **3 用户**(定价以官网为准),3 人以上需付费档
(Starter 约 $6/用户/月)或自托管 Headscale 控制面;部分校园网络策略禁装 VPN 类
软件,需先确认。

### 2.3 决策矩阵与推荐

打分:●=好 ◐=中 ○=差。权重按本场景(3–10 人、一台机器、讲师兼职运维)。

| 维度(权重) | ①Caddy basic_auth | ②Authelia | ③Authentik | ④Keycloak | ⑤自实现 | ⑥Tailscale |
|---|---|---|---|---|---|---|
| 一周内可落地(×3) | ● 0.5 人日 | ◐ 1–2 天 | ○ | ○ | ○ 3–4 人日起 | ● 0.5–1 人日 |
| 运维负担(×3) | ● 一份口令表 | ◐ YAML+升级 | ○ 多容器+PG | ○ | ◐ 自己的代码自己养 | ● 客户端自更新 |
| 常驻资源(×2) | ● ~40 MB | ● ~50 MB | ○ 0.5–1.2 GB | ○ 1.5 GB+ | ● 0 | ● ~80 MB |
| 身份传递给应用(×2) | ◐ 可透传/按端口 | ● Remote-User 头 | ● | ● | ● | ● 身份头 |
| MFA/口令安全(×1) | ○ 无 MFA | ● | ● | ● | ○ 自担 | ● 依托 IdP |
| 校外访问(×1) | ○ 需另配 | ○ | ○ | ○ | ○ | ● 天然 |
| 不改后端代码(×2) | ● | ● | ● | ● | ○ | ● |

**推荐(一周内)**:
- 纯内网、想零成本:**① Caddy + basic_auth**。Caddyfile 形如
  `basic_auth { alice <bcrypt> }` + `reverse_proxy 127.0.0.1:8874`,每用户一个
  站点块;内网建议 `tls internal`(自签 CA)避免 BasicAuth 凭据明文过线。
- 允许装 Tailscale(或本来就要校外访问):**⑥ Tailscale Serve + ACL**,身份头
  白送,将来方案 A 需要 owner 归属时直接读 `Tailscale-User-Login`,不用再换鉴权层。

**中期**:若出现「要 MFA」「要统一登录页」「要踢人/审计登录」的需求,在 Caddy 前面
加 **② Authelia**(forward-auth 模式),后端依旧零改动。它同时是 OIDC Provider,
为将来任何 SSO 需求留了口。

**两条路线共同的关键收益**:鉴权组件注入的用户名头(`Remote-User` /
`Tailscale-User-Login` / 透传的 basic_auth 用户名)就是 §3 方案 A 的 owner 来源——
先落地①或⑥,方案 A 的鉴权前置条件已经就绪,不返工。

---

## 3. 多用户数据模型

### 3.1 方案 A:`sessions.owner` 最小改动(单实例多租户)

一个 API 进程服务所有人,会话按 owner 归属过滤。

**改动面清单**(具体到文件):

| 改动点 | 文件/位置 | 工作量 |
|---|---|---|
| A1 schema:`sessions` 加 `owner TEXT`;`_COLUMN_MIGRATIONS` 加一条 ALTER(幂等迁移机制现成,见 `core/db.py:19-26`) | `core/schema.sql`、`core/db.py` | 0.5 人日(含 A2) |
| A2 store:`create_session(owner=)`、`list_sessions(owner=)`、`get_session` 返回 owner | `core/store.py:106-125` | (同上) |
| A3 身份解析:从反代注入头(`Remote-User`/`Tailscale-User-Login`)读用户名的 FastAPI 依赖;**信任边界 = 只信 127.0.0.1 来源 + 反代已剥除客户端伪造头** | `api/app.py` 新增 ~40 行 | 1–1.5 人日(含 A4) |
| A4 归属校验:校验点天然收敛——所有会话入口都过 `driver_of()`(`api/app.py:192-200`)或先查 `store.get_session`;在这两处加 owner 比对,25+ 端点一次覆盖;跨会话访问按 404 口径(与 `resolve_run` 现有「不泄露存在性」一致,`api/app.py:241-255`) | `api/app.py` | (同上) |
| A5 admin 角色:`INSAR_ADMINS` 环境变量名单;`/api/admin/*` 与 `/api/setup/*` 仅 admin | `api/admin_router.py`、`setup_router.py` | 0.5 人日 |
| A6 测试:归属矩阵(自己/别人/匿名 × 会话/run/产物/日志/SSE)+ 现有 215 项回归 | `tests/` | 1–1.5 人日 |

合计 **≈ 3.5–4.5 人日**。前端基本零改(后端过滤后,会话列表自然只剩自己的)。

**隔离性评估**:
- 逻辑隔离:一个进程、一个 DB、一个事件循环。任何一个 500/死锁/内存泄漏全组同炸;
  一个用户触发的 `probe(refresh=True)`(`api/app.py:317`)或大量 SSE 订阅影响所有人。
- 归属校验是**新增的安全关键代码**:漏一个端点就是越权(图件/日志/provenance 都是
  数据)。测试矩阵必须全覆盖,这是 A6 占一半工作量的原因。
- 资源竞争:所有用户共享一个 RLock(F5)。§5 的实测说明 3–10 人下这不是性能问题,
  但**故障域是全组**。

### 3.2 方案 B:每用户独立 INSAR_HOME + 端口(进程隔离)

每用户一个完整后端实例:`INSAR_HOME=E:\insar-team\{user}`、`INSAR_PORT=8873+i`,
全部绑 127.0.0.1,由反代/Tailscale 按「用户 → 端口」路由。

**这正是 JupyterHub 的标准形态**(Hub + configurable-http-proxy + 每用户
single-user server 于 `127.0.0.1:{随机端口}`),是多用户科学计算最成熟的单机模式,
我们相当于用 Caddy/Tailscale 顶替 Hub+Proxy 的角色、用静态配置顶替动态 spawn。

**改动面清单**:

| 改动点 | 内容 | 工作量 |
|---|---|---|
| B1 launcher | PowerShell/Python 脚本:读用户表(user→port→home),spawn N 个 `python -m insar_agent.api.app`,`/api/health` 健康检查,失败重拉;注册为计划任务/NSSM 服务开机自启 | 1–1.5 人日 |
| B2 路由+鉴权 | Caddy:每用户一个端口的 `basic_auth`+`reverse_proxy`;或 Tailscale:每用户一个 serve 监听 + ACL 端口授权 | 0.5–1 人日 |
| B3 验收 | 两个用户并发跑模拟链 + 一条真实 MintPy 链,互看不可达、互不干扰 | 0.5 人日 |

**核心代码改动:0**。合计 **≈ 2–3 人日**(不含 §4 仲裁)。

**必须知道的四个事实**:
1. **路由只能按端口/子域**,不能按路径前缀——前端是根绝对路径 `/api/...`(F10)。
   端口直连最省事;要美观域名得配内网 DNS 或 Tailscale MagicDNS。
2. **WSL 发行版共享安全**:作业目录按 run_id(含 UUID)分目录(F8),多实例并发
   launch 不冲突;conda 引擎环境只读共享。每实例 run 期间各持一个
   `wsl.exe sleep infinity` keepalive(`runtime/wsl.py:117-140`),几个空转进程,
   可忽略。
3. **每实例内存**:uvicorn+FastAPI+代理宿主 ≈ 100–150 MB,10 用户 ≈ 1–1.5 GB
   宿主 RAM,64 GB 机器无压力。
4. **资源竞争移到了计算层**:N 个进程各自独立决策「现在 launch」,没有跨进程仲裁
   ——这是方案 B 唯一新增的硬问题,§4 专门解决。

### 3.3 对比与推荐

| 维度 | 方案 A(owner 列) | 方案 B(每用户实例) |
|---|---|---|
| 核心代码改动 | ~200 行 + 迁移 + 测试矩阵(安全关键) | **0** |
| 落地人日 | 3.5–4.5 | 2–3 |
| 故障隔离 | ○ 全组一个进程 | ● 崩一个只影响一人 |
| 数据隔离 | ◐ 逻辑隔离,靠校验代码正确性 | ● 文件系统级,目录即边界(配额/备份/删除按目录) |
| 越权风险 | 漏校验一个端点即越权 | 反代路由错配才可能,面小得多 |
| 组内协作(互看会话/交接 run) | ● 天然支持(加共享语义即可) | ○ 不支持(库都不同) |
| 全局视图(谁在跑什么) | ● 一个 DB 全知道 | ○ 需要聚合层(§4 的 queue.db 恰好提供) |
| 资源仲裁 | 进程内即可 | 必须跨进程(§4) |
| 升级/重启 | 全组停机 | 滚动重启,单人不受他人影响 |
| 回滚 | 迁移后有 owner 列(无害) | 删实例即回滚,零残留 |
| SQLite 写压力 | 全组汇聚一个单写者(§5:仍余量巨大) | 天然分片,每库压力 = 单机版 |

**推荐:B 起步,A 延后到真实协作需求出现时。**理由:
1. 改动面与风险不对称:B 用 2–3 人日和零代码换来最强隔离;A 的 4 人日里一半在
   给安全关键路径写测试。
2. 课题组当下需求是「各跑各的、别打架」,不是「共享工作台」;协作需求(讲师查看
   学生 run、交接会话)出现后再做 A,且届时鉴权头、身份体系已由阶段 1 备好,
   A 的改动清单不变、不返工。
3. B→A 有清晰迁移路(§6.2),反向 A→B 则要拆库,难得多——**先 B 保留了两个方向
   的选择权**。

顺带回答一个隐含问题:能否「多进程 + 共享一个 insar.db」折中?**不能**。F4/F5
说明进程内存态(drivers/EventBus/token)与 DB 行强耦合,WAL 虽然物理上支持多进程,
但两个进程各持一份互不知晓的内存态会双驱同一 run(租约接管逻辑假设持有者已死)。
这条路是设计明确排除的,不作为选项。

---

## 4. 计算资源仲裁

### 4.1 现有 leases 表能否扩展为全局作业队列?

先看语义。`leases` 是**互斥锁表**(`resource TEXT PRIMARY KEY` + holder + 心跳,
`core/store.py:644-675`),已接线的用法是 run 级运行锁(`run:{run_id}`,防同 run
双执行回合,`loop/driver.py:287-302`)。它提供的是「占有/释放/心跳/死者可抢占」。

队列需要的是另一组语义:**FIFO/优先级顺序、排队可见性(我排第几)、公平性
(先到先得而非碰运气抢锁)、资源声明匹配(cpu/mem/io)**。让 N 个等待者对同一个
lease 行做轮询抢占,谁抢到看时机,重型链一跑 6 小时,后来者可能饿死——
互斥表当队列用是语义错配。

**结论:leases 不扩展为队列,但完整保留为「池占用」记账原语**(`pool:io_heavy`、
`pool:cpu` 的持有与心跳恰是它的本职,AGENT-DESIGN §4.11 本来就是这么设计的);
**排队语义由新增的 `job_queue` 表承担**,两者配合:

```sql
-- 新表:全局作业队列(排队可见性 + 公平性;与 leases 配合使用)
CREATE TABLE job_queue (
  id           INTEGER PRIMARY KEY,          -- 自增即 FIFO 序
  enqueued_at  REAL NOT NULL,
  owner        TEXT NOT NULL,                -- 用户(来自鉴权头 / 实例身份)
  run_id       TEXT NOT NULL,
  step_id      INTEGER,
  need_cpu     INTEGER NOT NULL,             -- capability 已声明(§4.11)
  need_mem_gb  REAL NOT NULL,
  need_io      TEXT NOT NULL,                -- light|medium|heavy
  state        TEXT NOT NULL DEFAULT 'queued',  -- queued|admitted|done|cancelled
  admitted_at  REAL, done_at REAL
);
```

准入规则沿用 §4.11 的三条(CPU ≤ 20、内存 ≤ 32 GB、`io=heavy` ≤ 1),准入点放在
执行器 LAUNCHED 阶段之前(`loop/driver.py` 每步启动前):队首且资源够 → 拿
`pool:*` 租约 → admitted;否则事件流发一条 note「排队中,前面还有 N 个作业,
预计等待 X(基于 duration_history,样本 <3 显示未知,§7.5 纪律)」。
心跳/死者抢占逻辑照搬 leases 现有实现——崩溃的持有者 90 秒后释放池位。

### 4.2 两种进程拓扑下的落法

**方案 A(单实例)**:`job_queue` 建在主库,调度就是进程内一段准入代码。零新基础
设施,SQLite 事务天然原子。

**方案 B(多实例)**:队列必须跨进程。三个选项:

| 选项 | 形态 | 评估 |
|---|---|---|
| ① 共享 **queue.db**(独立小库) | 仅 `job_queue`+`leases` 两表;各实例 WAL+`busy_timeout` 短事务读写 | **推荐起步**。事务全部 <1 ms(§5.1 实测),十个进程的争用可忽略。与 F5 纪律的关系要诚实说清:「单进程访问」的动机是主状态库与进程内存态强耦合;queue.db **无任何进程内缓存、行即真相**,是 SQLite 官方文档明确支持的多进程场景。风险可控,代价最低 |
| ② 微型 broker 服务 | 一个独享 queue.db 的小 HTTP 服务(等价 JupyterHub 的 Hub 角色) | 语义最干净、天然出全局视图 API;多一个常驻进程。**中期升级目标**:做队列面板时顺势升级 |
| ③ 文件锁目录 | 原子 mkdir/文件当锁 | 否。AGENT-DESIGN §4.11 已论证过文件锁在本环境不可靠(9p),且排队可见性还是要有个账本 |

**最小可落地版(一周内,1–2 人日)**:只做 `io_heavy` 一个池——它是单 SSD 上
唯一会「并行反而更慢」的硬约束。各实例在 WSL 后端 launch 前对 queue.db 抢
`pool:io_heavy` 租约,抢不到就带 note 等待。CPU/内存池、完整 job_queue 表、
排队面板放阶段 2。

### 4.3 WSL 单发行版的并发上限

分三层回答:

1. **WSL 机制层:无硬上限。**同一发行版的「多会话」只是往同一个 VM 里再起进程
   (每次 `wsl.exe --exec` 如此),不存在会话数配额;所有发行版共享单 uVM 与
   `.wslconfig` 全局配额(memory 默认 50% 宿主 RAM、processors 默认全部逻辑核;
   本项目规划显式设 20 核/40 GB)。**并发瓶颈从来不是 WSL,是分给它的资源。**
   一个次要注意点:宿主经 `\\wsl.localhost`(9p)读作业契约小文件,VM 高压时
   延迟会涨,现有代码已用 10s 超时 + unknown 态兜底(`runtime/wsl.py:219-223`)。
2. **资源账层:2–3 条链是实际上限。**按 capability 声明(§4.11 示例:ISCE2 干涉
   `cpu=8, mem=12GB, io=heavy`):`io=heavy` ≤1 就把重型 ISCE2 链压到**同时 1 条**;
   剩余 12 核/20 GB 可再容 1–2 条 CPU 型步骤(MintPy 反演走宿主 LocalBackend,
   不占 WSL 配额,但占同一块 SSD 与宿主 12 核)。结论:**全组同时在跑的重型链
   1 条 + 中型 1–2 条**,第 4 人起必然排队。
3. **产品层:排队可见性是刚需。**10 人 × 每日 10 run 的到达率(≈100 run/日)对
   2–3 并发的服务台,高峰期(上课前一晚)队列深度轻松到 5+。用户看不到「我排第几、
   预计多久」就会重复点运行/杀任务/找讲师,所以 §4.1 的 note 与阶段 2 的队列面板
   不是锦上添花。顺带:HyP3 云端路线(2–6 步交 ASF)不占本机资源,值得在 UI 引导
   高峰期用户走云端链,这是零成本的「扩容」。

---

## 5. SQLite 极限评估

### 5.1 本机实测基线(2026-08-13 复跑,i9 级 CPU + NVMe,WAL + synchronous=NORMAL)

`scripts/bench_store.py --runs 200`(200 run × 11 步 ≈ 2.3 万行,与 store.py
逐字一致的 SQL):

| 指标 | 实测值 | 含义 |
|---|---|---|
| 写路径「1 run 7 事务推进」 | 均值 **0.94 ms / 7 事务 ≈ 0.13 ms/事务** | 理论单线程写吞吐 ≈ **7,000+ 事务/秒** |
| load run 全景(6 查询) | 0.82 ms | 面板刷新的典型读 |
| due_actions(100 行命中) | 0.49 ms | 干预队列每秒轮询 |
| latest_run / latest_unsettled_command | 0.016 / 0.014 ms | 恢复路径点查 |
| EXPLAIN QUERY PLAN | **全部走索引,零全表扫描** | 规模增长下退化风险低 |

`scripts/stress_api.py`(真实 uvicorn + socket):

| 场景 | 实测值 |
|---|---|
| 20 线程并发 × 200 请求(4000 混合读+入队) | 223 req/s,p50=87 ms,p95=150 ms,p99=190 ms,**5xx=0** |
| 同会话 8 并发 turn | 1.24 s 全部完成,无死锁 |

(p50 87 ms 的主要成分是 FastAPI 同步端点线程池 + RLock 串行,不是 SQLite;
对 3–10 人的交互式使用完全够用。)

### 5.2 团队场景写压力推算(10 人 × 每日 10 run)

每 run 的写事务构成(按 store.py 的调用点数):11 步 × (upsert 1 + advance 5 +
reserve/settle 2 + artifacts ≈2 + metrics ≈3 + log_offset 更新 ≈5–20) + run 状态
+ chat + trace ≈ **200–400 事务/run**。

| 口径 | 数值 | 对照单写者能力 |
|---|---|---|
| 日总量:100 run × ~300 事务 | ≈ 3–4 万事务/日 | — |
| 全天均值 | **< 0.5 事务/秒** | 能力的 0.007% |
| 峰值:10 run 并发,每 run 日志高频期 ≈2–3 事务/秒 | **≈ 20–30 事务/秒** | 能力的 **< 0.5%**;业界「<100 TPS 零调优」带的下沿 |
| SSE/面板读放大(10 人 × 每秒若干读) | 单读 <1 ms,走 RLock | stress 实测 20 并发 p95=150 ms,远未饱和 |

**结论:该场景对 SQLite 的压力可以忽略,余量在两个数量级以上。**业界参照带
(附录 A):WAL 单写者在 NVMe 上 5,000–50,000 写/秒;<100 TPS 无需任何调优;
1,000–5,000 TPS 才进入极限区。我们的峰值 30 TPS 距离第一道坎还有 30 倍。

方案 B 下更宽裕:每用户一库,单库压力回落到单机版水平(10 run/日/库)。

### 5.3 真正的约束不是 SQLite 文件,是单进程架构

必须把两件事分开:
- **SQLite 引擎极限**:如上,此场景根本摸不到。
- **本仓库的架构选择**:「单进程访问 + RLock 串行一切读写」(F5)。它换来了零运维
  与简单的事务模型,代价是(a)读也过全局锁——但读 <1 ms,10 人场景无感;
  (b)禁止多进程共享主库——这是方案取舍(§3.3)的根源,而非性能问题。

所以「SQLite 会不会顶不住」在 3–10 人场景是伪问题;真命题是「什么时候需要
多写进程/跨机」,那是下一节的触发指标。

### 5.4 何时必须换 PostgreSQL(可测量的触发指标)

命中**任一硬触发**才启动迁移(阶段 3),否则不动:

| # | 触发指标 | 类型 | 检测手段 |
|---|---|---|---|
| T1 | 需要**多写进程**(如执行 worker 与 API 分进程、或多机执行节点写同一账本) | 硬·架构 | 需求评审 |
| T2 | 需要**跨机部署**(DB 与执行不在一台机器;SQLite 无网络协议) | 硬·架构 | 需求评审 |
| T3 | 写负载持续 **>500–1,000 事务/秒**(≈现峰值的 20–30 倍,对应 ~300 人日活或高频遥测) | 硬·容量 | 新增 tx/s 计数指标 |
| T4 | API p95 > 500 ms 且剖析显示 `db._lock` 等待占比 >30% | 软·体验 | stress_api 定期跑 + py-spy |
| T5 | 日志出现 `database is locked` / busy_timeout 重试(单进程下理论不发生,出现即说明有第二进程违规接入) | 软·纪律哨兵 | 日志告警 |
| T6 | 需要 DB 级行权限(RLS)/在线物理备份/时间点恢复等合规要求 | 硬·功能 | 需求评审 |

迁移工作量预估(届时):`Database` 类是唯一 SQL 入口,但 store.py 有 SQLite 方言
(`INSERT OR IGNORE`→`ON CONFLICT DO NOTHING`、PRAGMA、RLock→连接池),加全量回归
≈ **5–8 人日**(§6.1 阶段 3)。在 T1–T6 全未命中前做这件事是纯成本:换来的是
多写者能力(用不上)+ 一个常驻服务的运维负担(违背零基础设施纪律)。

### 5.5 一个便宜的中期加固(可选)

`trace`/`chat_messages` 是唯二无界增长表(长字段已硬截断,`core/store.py:684-689`)。
按上面推算一年 ≈ 千万行级,SQLite 仍可用但备份/VACUUM 变慢。给阶段 2 排一个
0.5 人日的「按 run 归档导出 + 定期清理」即可,不构成换库理由。

---

## 6. 升级路径:单机版 → 团队版的兼容迁移

### 6.1 分阶段路线与人日估

**阶段 0(现状)**:单人单机,127.0.0.1,桌面壳或源码起服务。

**阶段 1「合租」(推荐,一周内,合计 3–5.5 人日)**——方案 B + 外层鉴权 + 最小仲裁:

| 项 | 内容 | 人日 |
|---|---|---|
| 1.1 | 鉴权/路由:Caddy basic_auth(或 Tailscale Serve+ACL),每用户→端口 | 0.5–1 |
| 1.2 | 多实例 launcher:用户表(user/port/home)、spawn、健康检查、开机自启 | 1–1.5 |
| 1.3 | `io_heavy` 全局互斥最小版:独立 queue.db,WSL/重 IO 步骤 launch 前抢 `pool:io_heavy` 租约,等待时发排队 note | 1–2 |
| 1.4 | 验收(双用户并发:互看不可达、模拟链并发、真实链排队生效)+ 一页使用说明 | 0.5–1 |

验收标准:两个浏览器分别登录 alice/bob,各自只见自己的会话;同时提交两条含
WSL 步骤的 run,第二条在 `io_heavy` 步骤处显示排队并在第一条释放后自动继续;
杀掉 alice 的实例,bob 无感。

**阶段 2「有序」(需求驱动;全做 7–9.5 人日,2.3 可选、不做则 3.5–5 人日)**
——全局队列完整版 + (可选)多租户:

| 项 | 内容 | 人日 |
|---|---|---|
| 2.1 | `job_queue` 表 + CPU/内存池准入 + 排队可见性(「排第 N,预计 X」) | 2–3 |
| 2.2 | 队列/全局视图面板(谁在跑什么,管理员可终止;吃 queue.db 或升级为微型 broker) | 1–1.5 |
| 2.3 | (若协作需求出现)方案 A:owner 列 + 归属校验 + admin 角色 + 测试矩阵(§3.1 清单) | 3.5–4.5 |
| 2.4 | trace/chat 归档导出 | 0.5 |

**阶段 3「换库」(仅当 §5.4 触发)**:

| 项 | 内容 | 人日 |
|---|---|---|
| 3.1 | Database/store 方言改造(ON CONFLICT/事务模型/连接池) | 3–5 |
| 3.2 | 数据迁移脚本(行数校验+指纹抽样)+ 215 项全量回归 + 并发压测 | 2–3 |

### 6.2 数据与配置的兼容迁移细节

**单机 → 阶段 1(B)**:
- **讲师本人:零迁移**。现有 `INSAR_HOME`(默认 `./workspace`)原地不动,launcher
  把它登记为讲师的实例即可。**不要搬目录**——F9:`runs.workspace`、
  `steps.log_path`、`job_dir` 均为绝对路径,搬移会断历史 run 的图件/日志回看
  (新 run 不受影响)。确需搬移时,附带一次性 SQL 重写:
  `UPDATE runs SET workspace=REPLACE(workspace,'旧根','新根')`(steps/commands 同理),
  执行前备份 `insar.db`。
- **新成员:全新 HOME**,首启自动建库建目录(`create_app` 现成逻辑,
  `api/app.py:166-170`),无迁移。
- **配置**:`settings.json` 与 DB 同目录(setup_router),天然随 HOME 隔离;
  conda 引擎环境、WSL 发行版全组只读共享,零复制。
- **回滚**:删掉 launcher/反代,讲师实例回到单机版原样。数据零变更。

**阶段 1 → 2.3(B→A 合并,若做)**:
- run_id 含 UUID 片段(`core/store.py:34-37`),跨库合并**不碰撞**;
- `session_id` 是用户自起名("demo"),跨用户**必撞** → 合并时前缀化
  `{user}.{session}`,同时写 `sessions.owner={user}`;会话工作区目录同名搬移
  并做 F9 的路径重写;
- `pending_actions/leases` 只迁未消费/未过期行(通常为空);
- 合并脚本 + provenance 完整性抽验(随机 10 个 run 导出比对)≈ 1–2 人日
  (在 2.3 之外另计)。

**阶段 2 → 3(SQLite→PostgreSQL,若触发)**:
- schema 无 SQLite 独有类型(TEXT/INTEGER/REAL),表结构平移;
- 方言点集中:`INSERT OR IGNORE`(3 处)、`ON CONFLICT DO UPDATE`(4 处)、
  PRAGMA(连接初始化)、`executescript`;
- 迁移窗口:全组停机 <1 小时(库体积按年增长估 <5 GB);
- 迁后 `record_version`/指纹语义不变,历史 run 可比性不受影响。

### 6.3 全程不变量(每阶段验收都要复查)

1. 后端进程只绑 127.0.0.1(F1 纪律);对外入口只有反代/Tailscale。
2. `insar.db` 单进程访问(F5);跨进程共享的只有 queue.db(两表、短事务、无内存态)。
3. WSL 发行版/conda 环境只读共享;作业目录按 run_id 隔离(F8)。
4. 证据/provenance 语义不因多用户改变:owner 只是过滤维度,不进指纹。

---

## 7. 风险与开放问题

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 磁盘竞争先于 CPU 爆:10 人的中间产物挤 E: 483 GB,一条 ISCE2 链就要 300–400 GB(AGENT-DESIGN §4.10) | 阶段 1 就给每用户 HOME 划软配额并接线磁盘三级闸门(P1-1 本来就在清单上);引导高峰走 HyP3 云端链 |
| R2 | queue.db 成为新的单点(锁死/损坏) | 两表结构简单,损坏即删除重建(队列是瞬态,不是账本);监控 T5 哨兵 |
| R3 | basic_auth 凭据在纯 HTTP 内网可被嗅探 | Caddy `tls internal` 或直接选 Tailscale(端到端 WireGuard) |
| R4 | launcher 成为影子基础设施(没人维护) | 保持一页脚本 + 用户表 CSV 的极简形态;阶段 2 若上 broker 再吸收 launcher 职责 |
| R5 | 教学高峰(截止日前夜)队列深度 >10,体验崩 | 队列面板 + 预约语义(next_run 投递已有,`deliver_as` 三语义现成);极端时讲师用 admin 终止权 |
| R6 | Tailscale 免费档 3 用户不够且学校禁 VPN | 回退 Caddy 路线(决策矩阵已并列给出,二者可随时互换,后端零感知) |

开放问题(留待阶段 2 前决策):
- 组内「共享只读会话」(教学演示:全组围观讲师的 run)走 A 的共享语义还是
  SSE 只读代理?
- GPU(若未来添置)进资源池的声明方式——capability 的 `gpu=1` 声明与
  `pool:gpu` 租约,机制现成,留桩即可。

---

## 附录 A:引用来源

**仓库内(代码/文档/实测)**
- `api/app.py`(drivers 字典 172、driver_of 192-200、resolve_run 241-255、
  main/绑定 726-731)
- `core/db.py`(单进程纪律 45-51、busy_timeout 54-57、幂等迁移 19-42)
- `core/store.py`(run_id 构成 34-37、leases 644-675、trace 截断 684-689)
- `core/schema.sql`(sessions 8-15、leases 148-154、索引 177-185)
- `loop/driver.py`(run 租约 287-302、执行回合 261-374)
- `runtime/executor.py`(作业目录命名 79-84)、`runtime/wsl.py`(keepalive 117-140、
  state 超时 195-228)、`runtime/backend_select.py`(WSL_ENGINES 26-28)
- `docs/AGENT-DESIGN.md` §4.7-§4.13(作业契约/资源仲裁/失败闭集)、
  `docs/OPTIMIZATION.md`(P1-2 租约未接线、P2 鉴权待办)
- 实测:`scripts/bench_store.py --runs 200` 与 `scripts/stress_api.py`
  (2026-08-13 本机复跑,数字见 §5.1)

**业界(2026-08-13 检索)**
- 鉴权重量对比:bigiron.cc《Authelia vs Authentik vs Keycloak》;
  authhost.de《SSO Comparison for Self-Hosters 2026》(Authelia ~25 MB /
  Authentik 250 MB–1.2 GB / Keycloak 1.25–4 GB);ossalt.com 同题
- Tailscale Serve 身份头与 localhost 最佳实践:tailscale.com/docs/features/tailscale-serve;
  tailscale/tailscale#6954(v1.44 引入身份头);tailscale.com/blog/app-capabilities(ACL→HTTP 头)
- SQLite 并发带:toolbox365.net(<100/100–1000/1000–5000/>5000 TPS 分带);
  queryplane.com《SQLite in Production》;cr0x.net《PostgreSQL vs SQLite Concurrent
  Writers》;tinybird.co《Postgres vs SQLite》(迁移信号 = 多写进程压力)
- WSL2 单 VM 与配额:learn.microsoft.com/windows/wsl/wsl-config(memory 默认 50%、
  processors 默认全部);microsoft/WSL#6912(单 VM 单内核)、#8570(无每发行版限额)、
  #13096(多 uVM 未支持)、#11015(mirrored 网络单实例)
- 每用户进程先例:jupyterhub.readthedocs.io Technical Overview(Hub + configurable-
  http-proxy + 127.0.0.1 随机端口的 single-user server;LocalProcessSpawner)
