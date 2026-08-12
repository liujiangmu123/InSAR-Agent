-- insar-agent 唯一真相源(AGENT-DESIGN §6.1)
-- 写入原则:每个阶段推进一个事务;文件写走 tmp+replace;UNIQUE 保幂等插入。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 会话(对话层;一个会话可发起多个 run)
CREATE TABLE IF NOT EXISTS sessions (
  session_id  TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  created_at  REAL NOT NULL,
  mode        TEXT NOT NULL DEFAULT 'expert',     -- expert | guide
  scenario    TEXT,
  meta        TEXT                                 -- JSON
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(session_id),
  created_at  REAL NOT NULL,
  role        TEXT NOT NULL,                       -- user | agent | note
  content     TEXT NOT NULL,
  meta        TEXT
);

-- 运行(一次用户发起的完整处理)
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,                  -- UUID+单调时间戳(DESIGN.md:639)
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  parent_run_id TEXT,                              -- run fork(PI_FRAMEWORK absorb-E5)
  created_at    REAL NOT NULL,
  status        TEXT NOT NULL DEFAULT 'planning',  -- planning|running|paused|done|failed|interrupted
  intent        TEXT,                              -- JSON 结构化意图
  scenario      TEXT,
  workspace     TEXT NOT NULL,
  simulated     INTEGER NOT NULL DEFAULT 0,        -- 演示模式:引擎缺失时的合成执行(证据封顶 runnable)
  env_hash      TEXT,
  tool_versions TEXT,                              -- JSON {engine: version}
  git_head      TEXT,
  git_dirty     INTEGER,
  agent_hash    TEXT                               -- prompt+capabilities schema 哈希(DESIGN.md:644)
);

-- 步骤(DAG 节点)
CREATE TABLE IF NOT EXISTS steps (
  run_id       TEXT NOT NULL REFERENCES runs(run_id),
  step_id      INTEGER NOT NULL,
  capability   TEXT NOT NULL,
  name         TEXT NOT NULL,
  method       TEXT NOT NULL,
  params       TEXT NOT NULL,                      -- JSON(全量参数)
  task_hash    TEXT NOT NULL,
  args_hash    TEXT NOT NULL,
  local_hash   TEXT NOT NULL,
  eval_hash    TEXT NOT NULL,
  -- 指纹记录格式版本:低于当前值 → 判不可比(stale_reason='outdated_metadata'),
  -- 防指纹算法升级后旧记录被误判可复用(对齐 snakemake RECORD_FORMAT_VERSION
  -- persistence/__init__.py:32,660-696 的门控与 dvc schema 版本,absorb-M)
  record_version INTEGER NOT NULL DEFAULT 1,
  -- stage 是单调高水位(幂等守卫依据),state 是面向 UI 的当前态
  stage        TEXT NOT NULL DEFAULT 'PENDING',    -- PENDING|PREPARED|LAUNCHED|RUNNING|COLLECTED|VERIFIED
  state        TEXT NOT NULL DEFAULT 'pending',    -- pending|running|done|failed|interrupted|orphaned|stale|skipped
  stale        INTEGER NOT NULL DEFAULT 0,
  stale_reason TEXT,                               -- method_changed|param_changed|upstream_changed|tool_upgraded|artifact_missing
  failure_class TEXT,                              -- §4.12 闭集
  replay       TEXT NOT NULL DEFAULT 'never',      -- never | safe(absorb-E1)
  job_dir      TEXT,
  command_id   INTEGER,
  log_path     TEXT,
  log_offset   INTEGER NOT NULL DEFAULT 0,
  exit_code    INTEGER,
  run_ok       INTEGER,                            -- 双判定结果(≠ exit_code)
  qa           TEXT,                               -- JSON 检查明细
  config_hash  TEXT,
  started_at   REAL,
  ended_at     REAL,
  PRIMARY KEY (run_id, step_id)
);

-- 依赖边(显式 DAG,供级联标脏)
CREATE TABLE IF NOT EXISTS edges (
  run_id TEXT NOT NULL,
  parent INTEGER NOT NULL,
  child  INTEGER NOT NULL,
  PRIMARY KEY (run_id, parent, child)
);

-- 产物
CREATE TABLE IF NOT EXISTS artifacts (
  run_id   TEXT NOT NULL,
  step_id  INTEGER NOT NULL,
  art_id   TEXT NOT NULL,
  path     TEXT NOT NULL,
  kind     TEXT,
  layout   TEXT,
  policy   TEXT NOT NULL,                            -- path|stat|content(§5.5 三档)
  fp       TEXT NOT NULL,                            -- 三段编码 "<policy>:<algo>:<digest>"(absorb-M)
  record_version INTEGER NOT NULL DEFAULT 1,         -- 同 steps.record_version:指纹算法升级门控
  size     INTEGER,
  mtime_ns INTEGER,
  PRIMARY KEY (run_id, step_id, art_id)
);

-- 命令轨迹(意图/结算两段式:INSERT 即意图,settle 补 exit_code —— absorb-E2 预留 id)
CREATE TABLE IF NOT EXISTS commands (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT NOT NULL,
  step_id     INTEGER NOT NULL,
  argv        TEXT NOT NULL,                       -- JSON 数组
  cwd         TEXT,
  env_delta   TEXT,
  cmd_path    TEXT,                                -- 渲染出的 cmd 脚本(等价裸命令,§4.7 副产品)
  exit_code   INTEGER,                             -- NULL = 意图已落盘,尚未结算
  duration    REAL,
  stdout_path TEXT,
  attempt     INTEGER NOT NULL DEFAULT 1,
  created_at  REAL NOT NULL
);

-- 指标 + 来源契约校验(DESIGN §7.1)
CREATE TABLE IF NOT EXISTS metrics (
  run_id          TEXT NOT NULL,
  name            TEXT NOT NULL,
  value           REAL,
  unit            TEXT,
  source_artifact TEXT,
  source_field    TEXT,
  reparsed_ok     INTEGER,
  PRIMARY KEY (run_id, name)
);

-- 干预队列(§4.5;deliver_as 双投递语义,absorb-J,取值沿用 pi 三队列 absorb-E4)
CREATE TABLE IF NOT EXISTS pending_actions (
  id          INTEGER PRIMARY KEY,
  created_at  REAL NOT NULL,
  scope       TEXT NOT NULL,                       -- 'step' | 'run'
  target      TEXT NOT NULL,                       -- step_id 或 run_id
  action      TEXT NOT NULL,                       -- RESET|PAUSE|PLAY|SKIP|KILL|SET_METHOD|SET_PARAMS|USER_MESSAGE
  payload     TEXT,                                -- JSON
  deliver_as  TEXT NOT NULL DEFAULT 'steer',       -- steer(当前步结束后) | follow_up(run 结束后) | next_run(下次规划)
  consumed_at REAL                                 -- NULL = 待消费;未消费即可编辑/撤回(§4.5)
);

-- 租约(§4.11:SQLite 锁,心跳抢占)
CREATE TABLE IF NOT EXISTS leases (
  resource  TEXT PRIMARY KEY,
  holder    TEXT NOT NULL,
  acquired  REAL NOT NULL,
  heartbeat REAL NOT NULL,
  ttl       REAL NOT NULL
);

-- 轨迹(schema 对齐 OpenDiscoveryTrace 10 字段,§7.2 面板7)
CREATE TABLE IF NOT EXISTS trace (
  id               INTEGER PRIMARY KEY,
  run_id           TEXT,
  session_id       TEXT,
  step_no          INTEGER,
  ts               REAL NOT NULL,
  phase            TEXT,
  thought          TEXT,
  action           TEXT,                           -- JSON {type,tool,input,output}
  observation      TEXT,
  error_occurred   INTEGER NOT NULL DEFAULT 0,
  error_type       TEXT,
  error_message    TEXT,
  revision_trigger TEXT,
  confidence       REAL,
  raw_response     TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id);
CREATE INDEX IF NOT EXISTS idx_actions_due ON pending_actions(consumed_at, deliver_as);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace(run_id);
CREATE INDEX IF NOT EXISTS idx_commands_step ON commands(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);
