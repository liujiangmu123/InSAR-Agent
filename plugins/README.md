# 外置插件约定

后人加技能或可视化按下面四条落盘,不必猜目录。仓库根 `plugins/` 只放**人类约定**和**示例副本**(拷到 `INSAR_HOME/plugins/` 用)。运行时装载器在 `src/insar_agent/plugins/`(内核属地);本目录不被 Python 执行。

## 1. 步骤技能

复制 [`skills/11-crossval-qa/SKILL.md`](../skills/11-crossval-qa/SKILL.md) 的结构到 `skills/<NN>-<name>/SKILL.md`。

- 目录名:`<两位步骤号>-<短名>`,例如 `06-unwrap`。`NN` 必须已在 `registry/capabilities.py` 存在(核心链 1–11;12–19 预留,不能只建目录就冒充新步)。
- frontmatter 闭集:`name` / `description` / `capability` / `version` / `applies_to`。`capability` 以字段为准,须与目录前缀一致;`applies_to` 为 `"all"` 或场景 key 列表。
- 正文五章缺一不可:适用判据 / 参数启发式 / 常见失败与处置 / QA 依据 / 参考文献。
- drop-in,无需改 Python。同一步骤号只能有一份技能。

## 2. 场景包

在 `src/insar_agent/registry/scenario_packs/<key>/` 放两份文件(对照 `quake/` 或 `landslide/`):

- `SKILL.md`:`name` 必须等于目录名 `<key>`;`metadata` 必填 `label` / `match` / `chain` / `model` / `pick_7`。
- `overrides.yaml`:机器读,顶层只有 `step_overrides` 与 `cloud_completed`。

场景知识写在包里,不要写进 `capabilities.py`。

## 3. 查看器

声明式 YAML,字段闭集与内置 catalog 相同:

`id` / `kind` / `status` / `title` / `version` / `match` / `render` / `sidebar` / `note`

运行时落盘:`INSAR_HOME/plugins/<id>/plugin.yaml`(必须是 `home/plugins` 的直接子目录,禁止 `..`)。本仓库 `plugins/<id>/` 是给拷过去的示例,不要在本目录放 `.py` 执行入口(只读 YAML;exec 即 RCE)。

| 字段 | 取值 |
|---|---|
| `kind` | `viewer` \| `skill` \| `engine` |
| `status` | `ready` \| `reserved` |
| `match.suffixes` | 可选,小写后缀 |
| `match.name_contains` | 可选,大小写不敏感子串 |
| `render` | `png_gallery` \| `timeseries_point` \| `external` \| `none` |
| `sidebar` | `figures` \| `files` \| `none` |

`suffixes` 与 `name_contains` 同时声明则 **AND**;只声明一项则该项即可。`reserved` 也会出现在侧栏(显示预留,不假装已实现)。

内置 catalog 已登记期刊 PNG、点位时序、KMZ 等。**偏移时间序列查看器已标 `reserved`**,示例副本见 [`offset-timeseries/`](offset-timeseries/)。

## 4. 新引擎方法

**不能只写 YAML。** 必须同时改:

1. `src/insar_agent/registry/capabilities.py`(科学闭集:方法 id、参数、产物、`run_ok`)
2. `src/insar_agent/engines/`(`resolve_builder` 接到真实 `build()`,禁止静默假产物)

`kind: engine` 的 catalog 条目只是预留告示,改 YAML 不会长出新科学能力。

## 5. 示例

把 [`offset-timeseries/`](offset-timeseries/) 整目录拷到 `$INSAR_HOME/plugins/offset-timeseries/`。实现查看器时改 `status=ready`,并在 `engines/figures.py` 或 overlay / `/api/timeseries-point` 兼容层补出图与取样。
