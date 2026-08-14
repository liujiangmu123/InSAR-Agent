"""InSAR Agent core - DeepSeek LLM integration with tool-use"""

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from openai import OpenAI

from .config import load_settings, load_accounts, save_settings, save_accounts, get_project_dir, get_output_dir
from .tools.query_slc import query_slc
from .tools.submit_insar import submit_insar_jobs
from .tools.check_status import check_job_status
from .tools.download import download_products
from .tools.unzip import unzip_products
from .tools.clip import clip_to_common_overlap
from .tools.geocode import resolve_location
from .tools.boundary import fetch_boundary
from .tools.mintpy import run_mintpy, generate_mintpy_config
from .tools.knowledge import search_knowledge
from .tools.check_credits import check_credits
from .tools.catalog import search_catalog
from .workflow import WorkflowRunner
from . import events

SYSTEM_PROMPT = """You are an InSAR processing agent. You help users process Sentinel-1 SAR data by calling tools. Your job is to execute, not to narrate.

## Rule 1: ABSOLUTE ANTI-FABRICATION
You are a tool executor, NOT a storyteller. You MUST ONLY report data that tools actually returned.

- NEVER fabricate ANY data: file paths, job IDs, task counts, statuses, scene names, dates, orbit directions, credit amounts, download sizes, elapsed times, pair counts, config content, chart URLs — any number, name, or structured text must come from a tool result.
- CRITICAL: You MUST call the tool to get its data. If you output config content from preview_mintpy_config without having called it, that is fabrication. The config text is generated server-side and you CANNOT know it without calling the tool.
- NEVER embellish or narrate. No "接下来将...", "预计...", "将会...". Tool results speak for themselves.
- NEVER speculate: If a tool fails, report the error text verbatim.
- NEVER describe what will happen. Only report what HAS happened.
- auto_pipeline runs in background. After calling it, reply ONLY: "已启动托管，右侧面板可查看进度。"
- If user asks to filter by year/date range ("17年", "2018到2020") and query_slc has NOT been called yet, call query_slc with the specified date range. If query_slc was already called and user wants a different range, calling query_slc again with new dates is ALLOWED — this produces fresh data + images.

## Rule 2: TOOL CALL DISCIPLINE
- You MUST call tools to get real data. NEVER fabricate, simulate, or guess tool outputs — even if you know the expected format. If you output config content, chart URLs, file lists, or any data without first calling the corresponding tool, that is a VIOLATION.
- preview_mintpy_config MUST be called before you can present ANY MintPy configuration. The config content is ONLY available via the tool response.
- save_mintpy_overrides MUST be called before you claim overrides were saved.
- After auto_pipeline completes, you MUST issue TWO tool calls: first save_mintpy_overrides(task_id, {}), then preview_mintpy_config(task_id=...). Only after BOTH return can you present the config.
- For these tools, output ONLY the tool's result, no commentary unless a user-facing message is required:
  `query_slc`, `submit_insar_jobs`, `check_job_status`, `download_products`, `unzip_products`, `clip_to_common_overlap`, `auto_pipeline`, `run_mintpy`, `preview_mintpy_config`, `save_mintpy_overrides`

## Rule 3: CONFIRMATION GATE
- query_slc is a LOOKUP tool only. After it returns results, you MUST present them and WAIT. NEVER call submit_insar_jobs unless the user explicitly confirms they want to submit.
- submit_insar_jobs requires explicit user intent: "提交", "处理", "跑", "开始处理", "确认提交". A bare "好的" is NOT consent to submit unless you just asked "是否确认提交？"

## Task ID — THE KEY IDENTIFIER
Every submission generates a `task_id`: a short string like `Project_user_20260720_120000`. You ONLY pass this task_id between tools — NEVER pass file paths.

- submit_insar_jobs returns task_id → pass it verbatim to auto_pipeline, check_job_status, download_products, unzip_products, clip_to_common_overlap, preview_mintpy_config, run_mintpy, save_mintpy_overrides
- All path-based tools (check_job_status, download_products, unzip_products, clip_to_common_overlap, auto_pipeline) accept task_id only. System resolves file paths internally.
- task_id NEVER contains slashes or directories. It's just a name.
- In a new conversation where you don't know the task_id, pass only project_name to auto_pipeline to auto-discover.

## Workflow (strict order, no deviation)
1. query_slc → present result (brief: "Path X Frame Y, N景, 时间范围X~Y, 预计M对, 费用C credits")
2. User confirms → check_credits → show accounts and balances. If no accounts, ask for credentials. Ask user which account to use.
3. User selects account → ask: "是否需要自定义 MintPy 参数？回复'默认'跳过". If user wants customization, save via save_mintpy_overrides. → submit_insar_jobs(account_username=..., scene_names=...)
3a. If submit returns insufficient credits → tell user exactly "账号X余额不足，需要Y，现在有Z". If user provides another account → save_credentials → check_credits → re-submit with new account_username. If user refuses, stop.
4. Submit succeeds → ask: "提交成功，是否托管后续流程？"
5. User agrees (says 托管/确认/好的/继续 etc.) → YOU MUST CALL auto_pipeline(task_id=...) as a tool call. Do NOT just output the text without calling the tool. After the tool call succeeds, reply ONLY: "已启动托管，右侧面板可查看进度。"
6. After auto_pipeline completes → YOU MUST call save_mintpy_overrides(task_id, {}) FIRST, then preview_mintpy_config(task_id=...) SECOND. NEVER fabricate config content. The config text ONLY comes from preview_mintpy_config's response. After BOTH calls succeed, present ONLY these core parameters extracted from the config (discard all other lines), then in ONE reply ask: "是否需要修改配置？以及是否需要 ERA5 大气校正（会增加5-15分钟）？默认不改且不加。"

   【核心可修改参数 — 每次必须列出当前值】
   | 参数 | 当前值 | 说明 |
   | mintpy.troposphericDelay.method | no | 大气校正: pyaps(ERA5)/height_correlation/gacos/no |
   | mintpy.networkInversion.minTempCoh | 0.4 | 时序相干性阈值 (0-1) |
   | mintpy.network.minCoherence | 0.4 | 干涉对相干性阈值 (0-1) |
   | mintpy.deramp | quadratic | 相位斜坡去除: no/linear/quadratic |
   | mintpy.unwrapError.method | no | 解缠误差校正: bridging/phase_closure/bridging+phase_closure/no |

   Example correct reply:
   「MintPy 核心配置如下：
   · 大气校正: no
   · 时序相干性阈值: 0.4
   · 干涉对相干性阈值: 0.4
   · 相位斜坡去除: quadratic
   · 解缠误差校正: no
   是否需要修改配置？以及是否需要 ERA5 大气校正（会增加5-15分钟）？默认不改且不加。」
   - User may reply: "改XX为YY" and/or "加/要/开 ERA5" and/or "不/默认/跳过"
   - Handle modifications via save_mintpy_overrides. If ERA5 requested: save {"mintpy.troposphericDelay.method": "pyaps"}. If user explicitly says "不加/不" for ERA5: save {"mintpy.troposphericDelay.method": "no"}.
   - After any override is saved, immediately preview_mintpy_config AGAIN so user sees updated config.
   - When user says "确认/跑/开始" → run_mintpy(task_id=...)
7. After MintPy completes (zip download link delivered by ui_update), YOU MUST ask in ONE sentence: "是否需要对该结果进行形变分析（沉降漏斗检测、累计形变、分区统计等）？"
   - This question is MANDATORY. Never skip it. The user may want a detailed analysis report.
   - User says "分析/要/好/可以/行" → call run_analysis(task_id=...) immediately. run_analysis runs in background, the user will get a zip download link when it finishes.
   - User says "不/跳过/不用" → reply "好的。" and do nothing.

## Intent → Tool Routing Table
When a user sends a natural language message, map their intent to the corresponding tool below.

| 用户意图 / 关键词 | 调用工具 | 备注 |
| 查询影像、查SLC、搜数据、有什么影像、看一下影像、看一下覆盖、看一下图、空间覆盖、有什么图 | query_slc | 先调 fetch_boundary 拿 geojson_path，直接传给 query_slc 的 vector 参数做精准空间过滤 |
| 查结果、搜索已有、看结果、有什么分析结果、有什么形变结果、库里有什么 | search_catalog → query_slc | 先调 fetch_boundary 拿 geojson_path，直接传给 search_catalog 的 geojson_path 参数；库里无→调 query_slc（同样传 geojson_path）。如果前面对话中已经搜过 catalog 且无结果，直接调 query_slc 不要再搜 catalog |
| 地理编码、某地在哪 | resolve_location | |
| 下载行政边界 | fetch_boundary | |
| 提交作业、跑InSAR | submit_insar_jobs | 需先 query_slc；可传 scene_names 或 scene_indices 筛选 |
| 查进度、状态、作业怎么样了 | check_job_status | 传 task_id，系统自动解析文件路径 |
| 下载数据、下载产品 | download_products | 传 task_id，系统自动解析路径和账号 |
| 解压、解压缩 | unzip_products | 传 task_id，系统自动解析路径 |
| 裁剪、统一范围 | clip_to_common_overlap | 传 task_id，系统自动解析路径 |
| 自动处理、托管、自动跑 | auto_pipeline | 传 task_id，系统自动解析全部路径 |
| 跑MintPy、时序分析 | preview_mintpy_config → run_mintpy | 需要 task_id |
| MintPy配置预览 | preview_mintpy_config | 需要 task_id |
| 库里有什么、搜索结果 | search_catalog | |
| InSAR原理、技术问题 | search_knowledge | |
| 查看结果、可视化 | view_result | 传 show + 当前project_name。如果对话中有 project_name/task_id，自动传 project_name |
| 累计形变、看形变图、看累计、交互形变图 | view_result | show="cumulative"，交互式累计形变图（点击任意像素查看该点时序曲线）。自动传对话中的 project_name |
| 形变速率、看速率、平均速率、速率图 | view_result | show="velocity"，静态平均形变速率图。自动传对话中的 project_name |
| 看时序曲线 | view_result | show="timeseries"，平均累计形变时序曲线 |
| 分析结果、统计值 | analyze_result | 自动传对话中的 project_name |
| 形变分析、沉降漏斗、形变评估、详细分析 | run_analysis | 后台线程，完成后自动推送zip下载链接 |
| 查余额、账号额度 | check_credits | |
| 自定义MintPy、修改参数 | save_mintpy_overrides | 需要 task_id |
| 大气校正、ERA5、开启ERA5、打开ERA5、加ERA5、启用大气校正、开大气校正 | save_mintpy_overrides | set {"mintpy.troposphericDelay.method": "pyaps"} |
| 关闭大气校正、取消ERA5、不用ERA5、不加大气校正 | save_mintpy_overrides | set {"mintpy.troposphericDelay.method": "no"} |
| 保存账号、ASF密码 | save_credentials | |

## Tool Call Rules
- 区分"查影像"和"查结果"：用户说查询影像/SLC/数据→query_slc；用户说查结果/搜索已有/有什么分析结果→先search_catalog，库里有则展示并问"是否查看详情"，库里无则调query_slc并回复"没有已有结果，这里有哪些影像...是否要处理？"
- query_slc: call ONCE per area per date range. scene_names stay in context for submit. If user changes the date filter ("17年", "只要2018-2020"), call query_slc again with the new dates — it will return fresh results + images. If user specifies a Chinese administrative division (province/city/district), call fetch_boundary first and pass its geojson_path as the `vector` parameter for precise spatial filtering.
- submit_insar_jobs: System validates scene names against query_slc cache. LLM can filter by scene_names (exact names) or scene_indices (0-based, date-sorted). Both optional — if omitted, all cached scenes are submitted. NEVER fabricate scene names — they must exist in query_slc result. If user selected account, pass account_username.
- auto_pipeline: MUST be called when user agrees to pipeline (says 托管/确认/继续). call ONCE per submission. Pass task_id only. System resolves all file paths internally. After calling, reply "已启动托管，右侧面板可查看进度。"
- check_job_status / download_products / unzip_products / clip_to_common_overlap: ALL accept task_id only. System resolves file paths internally — NEVER pass raw file paths.
- search_catalog: ONLY when user asks about existing results. Call fetch_boundary first, then pass the returned `geojson_path` value directly to search_catalog's `geojson_path` parameter. If previous search_catalog in this conversation returned 0 results, do NOT call it again — go directly to query_slc.
- search_knowledge: ONLY for technical questions.
- If user's intent does NOT match any row above, ask: "请明确您的需求，是想查影像、提交作业、查状态、下载数据，还是咨询技术问题？"

## Data Rules
- Credits: 10/pair (20x4), 15/pair (10x2). Defaults: 20x4, 2 neighbors.
- task_id: copy verbatim from submit result. It's a short string like "Path157_Frame127_user_20260720_120000".
- Scene names: full S1A_IW_SLC__... format, never abbreviate.

## Behavior
- Respond in Chinese, be brief.
- No unsolicited suggestions.
- If tool not available: "这个功能暂不支持".
- query_slc ONLY displays results — NEVER automatically submit, NEVER call submit_insar_jobs without explicit user confirmation. After presenting query_slc results, you MUST wait for the user to explicitly say they want to submit (e.g. "提交", "处理这个", "帮我跑").
- "确认" / "好的" / "开始" only triggers execution when the PREVIOUS assistant message explicitly asked a confirmation question (e.g. "是否提交?", "确认处理?", "是否托管?"). Otherwise, if no question was asked, ask the user what they want to do.
- NEVER skip steps in the workflow. Each step (query_slc → user confirms → check_credits → user selects account → submit → user agrees → auto_pipeline) must have explicit user agreement before proceeding.
- User says "确认/提交/好的": execute immediately ONLY if a confirmation question was just asked.
"""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_location",
            "description": "Convert a human-readable place name to geographic lon/lat coordinates. Call this BEFORE query_slc when the user specifies a location by name (e.g. '北京', '成都', 'Los Angeles'). Supports Chinese and English names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Place name (Chinese or English), e.g. '北京市', '成都', 'Tokyo'"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_boundary",
            "description": "Download precise administrative boundary GeoJSON from DataV GeoAtlas for Chinese divisions (province, city, district). Returns a geojson file path that can be used as the vector parameter in query_slc for exact spatial intersection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Place name, e.g. '北京市', '成都', '四川省'. Must be a Chinese administrative division name."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_slc",
            "description": "Search ASF for Sentinel-1 SLC data. Call fetch_boundary first, then pass the returned geojson_path directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vector": {"type": "string", "description": "The geojson_path value from fetch_boundary result."},
                    "start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "end": {"type": "string", "description": "End date YYYY-MM-DD"},
                    "flight_dir": {"type": "string", "enum": ["ASCENDING", "DESCENDING"], "description": "Orbit direction. ONLY set if user says 升轨/降轨."},
                    "path": {"type": "integer", "description": "Filter by specific relativeOrbit path number."},
                    "frame": {"type": "integer", "description": "Filter by specific frame number."}
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_insar_jobs",
            "description": "Submit SBAS InSAR interferogram jobs to ASF HyP3. Scene names are validated against query_slc cache — LLM can filter by name or index, but cannot fabricate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "integer", "description": "Path number (from query_slc result)"},
                    "frame": {"type": "integer", "description": "Frame number (from query_slc result)"},
                    "project_name": {"type": "string", "description": "Project name for tracking"},
                    "scene_names": {"type": "array", "items": {"type": "string"}, "description": "Specific full scene names to submit (must exist in query_slc cache). Use this when user picks specific scenes by name. Mutually exclusive with scene_indices — pick one."},
                    "scene_indices": {"type": "array", "items": {"type": "integer"}, "description": "Zero-based indices into the sorted-by-date scene list from query_slc. e.g. [0,1,2] for first 3 scenes. The scene list is sorted by date, so [0,1] = earliest 2 scenes. System validates against cache. Mutually exclusive with scene_names."},
                    "max_neighbors": {"type": "integer", "description": "SBAS neighbors (default 2). For N scenes, generates sum((N-i-1) for i in range(min(N-i-1, max_neighbors))) pairs. Use max_neighbors=1 for sequential pairs only."},
                    "looks": {"type": "string", "enum": ["20x4", "10x2"], "description": "Multi-looking: 20x4 (10 cr/pair) or 10x2 (15 cr/pair)"},
                    "account_username": {"type": "string", "description": "ASF username for billing. REQUIRED if user selected a specific account."},
                },
                "required": ["path", "frame", "project_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_job_status",
            "description": "Check HyP3 job completion status. System resolves job_file from task_id internally — just pass task_id from submit_insar_jobs result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. System resolves the .txt file path automatically."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_products",
            "description": "Download HyP3 products from ASF. System resolves url_file and save_dir from task_id internally — just pass task_id from submit_insar_jobs result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. System resolves url_file and save_dir automatically."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unzip_products",
            "description": "Extract all .zip files for a task in parallel. System resolves source_dir and output_dir from task_id internally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. System resolves source_dir and output_dir automatically."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clip_to_common_overlap",
            "description": "Clip all HyP3 GAMMA GeoTIFF products for a task to the common overlap area. System resolves data_dir and output_dir from task_id internally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. System resolves data_dir and output_dir automatically."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_workflow",
            "description": "Execute the full 6-step InSAR processing pipeline (query → submit → status → download → unzip → clip) with state persistence",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Project name"},
                    "params": {
                        "type": "object",
                        "description": "All processing parameters",
                        "properties": {
                            "lon": {"type": "number"}, "lat": {"type": "number"},
                            "start": {"type": "string"}, "end": {"type": "string"},
                            "flight_dir": {"type": "string"}, "polarization": {"type": "string"},
                            "path": {"type": "integer"}, "frame": {"type": "integer"},
                            "max_neighbors": {"type": "integer"}, "looks": {"type": "string"},
                        },
                    },
                    "start_from": {"type": "integer", "description": "Resume from step index (0-5)"},
                },
                "required": ["project_name", "params"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workflow_status",
            "description": "Check the current status of a project workflow pipeline",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Project name"},
                },
                "required": ["project_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_mintpy",
            "description": "Run MintPy smallbaselineApp.py time-series processing. Pass the task_id from submit. Outputs velocity.h5, timeseries.h5, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the InSAR knowledge base for technical documentation (MintPy, HyP3, ISCE, SAR theory). Use this when the user asks 'how to' questions or needs InSAR technical explanations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in Chinese or English"},
                    "top_k": {"type": "integer", "description": "Number of results (default 3)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search the results catalog. If user mentions a Chinese location (province/city/district), ALWAYS call fetch_boundary first, then pass its geojson_path directly. Uses precise polygon intersection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "geojson_path": {"type": "string", "description": "Path to boundary GeoJSON from fetch_boundary result. REQUIRED for all location-based queries."},
                    "name_query": {"type": "string", "description": "Keyword in result names"},
                    "date_start": {"type": "string"},
                    "date_end": {"type": "string"},
                    "top_k": {"type": "integer", "description": "Max results"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_credits",
            "description": "Query HyP3 credit balance for ALL saved ASF accounts. Call BEFORE submit_insar_jobs to let user choose which account to use.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_credentials",
            "description": "Save ASF/Earthdata credentials for subsequent InSAR processing steps. Call this when the user provides their username and password. These credentials will be used by submit_insar_jobs and download_products.",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "ASF Earthdata username"},
                    "password": {"type": "string", "description": "ASF Earthdata password"},
                },
                "required": ["username", "password"],
            },
        },
    },
        {
        "type": "function",
        "function": {
            "name": "auto_pipeline",
            "description": "Automatically run the mechanical pipeline steps after job submission: poll status → download → unzip → clip_to_common_overlap. System resolves all paths from task_id internally. Use this after submit_insar_jobs succeeded.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. System resolves all file paths automatically."},
                    "project_name": {"type": "string", "description": "Project name — used as fallback if task_id is unknown to auto-discover the latest task."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_result",
            "description": "Show InSAR result previews. cumulative=interactive displacement map (click pixel → time series curve). velocity=mean velocity map. If you don't know entry_id, pass project_name instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Catalog entry id, e.g. 'result_0001'. Optional if project_name is provided."},
                    "project_name": {"type": "string", "description": "Project name to auto-lookup the entry. Use when entry_id is unknown."},
                    "show": {"type": "string", "enum": ["cumulative", "velocity", "timeseries"], "description": "cumulative=interactive cum disp, velocity=velocity map, timeseries=TS curve"},
                },
                "required": ["show"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_result",
            "description": "Get statistical analysis (mean/min/max/std) of a result. Use when user asks about velocity values or displacement statistics. If you don't know entry_id, pass project_name instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Catalog entry id, e.g. 'result_0001'. Optional if project_name is provided."},
                    "project_name": {"type": "string", "description": "Project name to auto-lookup the entry."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_mintpy_overrides",
            "description": "Save custom MintPy parameter overrides. Pass task_id. Overrides auto-applied when preview_mintpy_config or run_mintpy runs later. Example: enable ERA5 correction → {'mintpy.troposphericDelay.method': 'pyaps'}",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result."},
                    "overrides": {"type": "object", "description": "Dict of key -> value, e.g. {'mintpy.deramp': 'linear'}"},
                },
                "required": ["task_id", "overrides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_mintpy_config",
            "description": "Generate and preview the MintPy smallbaselineApp.cfg. Pass the task_id from submit_insar_jobs. System resolves all paths automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. Short string like Project_user_20260720_120000."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": "Run comprehensive deformation analysis on completed MintPy results. Detects subsidence funnels, computes cumulative volume loss, strain rate, acceleration, and zone classification. Generates charts and packages everything into a downloadable zip. Runs in background.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from submit_insar_jobs result. The MintPy results must already exist for this task."},
                },
                "required": ["task_id"],
            },
        },
    },
]


QUERY_CACHE_DIR = Path(__file__).parent / 'data' / 'query_cache'


def _cache_query_result(result):
    """Save scene names from query_slc result to cache files, keyed by (path, frame).
    LLM cannot fabricate scene names during submit — the system reads from this cache.
    Stores both names and dates to support index-based selection (e.g. 'first 3 scenes')."""
    QUERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for s in result.stacks:
        cache_file = QUERY_CACHE_DIR / f'p{s.path}_f{s.frame}.json'
        sorted_scenes = sorted(s.scenes, key=lambda sc: sc['date'])
        cache_data = {
            'path': s.path,
            'frame': s.frame,
            'start_date': s.start_date,
            'end_date': s.end_date,
            'flight_direction': s.flight_direction,
            'scene_names': [sc['name'] for sc in sorted_scenes],
            'scene_dates': [sc['date'] for sc in sorted_scenes],
            'queried_at': datetime.now().isoformat(),
        }
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_cached_scene_names(path: int, frame: int) -> list[str]:
    """Load scene names from query cache (sorted by date). Returns empty list if cache miss."""
    cache_file = QUERY_CACHE_DIR / f'p{path}_f{frame}.json'
    if cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding='utf-8'))
            return data.get('scene_names', [])
        except Exception:
            pass
    return []


def _resolve_scene_names(path: int, frame: int, llm_names: list[str] | None = None, llm_indices: list[int] | None = None) -> list[str] | tuple[list[str], str]:
    """Resolve scene names for submission from cache, with optional LLM filtering.
    
    Returns (scene_names, error_message_or_empty_string).
    - If llm_indices provided: extract by index from sorted cached names
    - If llm_names provided: validate against cache, use as subset
    - If neither: return all cached names
    On validation failure, returns (empty_list, error_message).
    """
    cache_file = QUERY_CACHE_DIR / f'p{path}_f{frame}.json'
    if not cache_file.is_file():
        return [], f'未找到 Path {path} Frame {frame} 的查询缓存。请先用 query_slc 查询该区域的数据。'
    
    try:
        data = json.loads(cache_file.read_text(encoding='utf-8'))
        cached_names = data.get('scene_names', [])
    except Exception:
        return [], f'读取缓存失败: Path {path} Frame {frame}'
    
    if not cached_names:
        return [], f'缓存为空: Path {path} Frame {frame}'
    
    if llm_indices is not None and len(llm_indices) > 0:
        for idx in llm_indices:
            if idx < 0 or idx >= len(cached_names):
                return [], f'序号 {idx} 超出范围 (共 {len(cached_names)} 景, 序号 0~{len(cached_names)-1})'
        selected = [cached_names[i] for i in llm_indices]
        if len(selected) < 2:
            return [], f'选中的场景数 ({len(selected)}) 不足, 至少需要 2 景才能生成干涉对'
        return selected, ''
    
    if llm_names is not None and len(llm_names) > 0:
        cached_set = set(cached_names)
        invalid = [n for n in llm_names if n not in cached_set]
        if invalid:
            return [], f'以下场景不在缓存中: {invalid[:3]}{"..." if len(invalid) > 3 else ""}'
        if len(llm_names) < 2:
            return [], f'选中的场景数 ({len(llm_names)}) 不足, 至少需要 2 景才能生成干涉对'
        return llm_names, ''
    
    if len(cached_names) < 2:
        return [], f'缓存中只有 {len(cached_names)} 景, 至少需要 2 景才能生成干涉对'
    return cached_names, ''


class InsarAgent:
    """NL-driven InSAR processing agent powered by DeepSeek."""

    def __init__(self, callback: Optional[Callable[[str], None]] = None, config: Optional[dict] = None, pipeline_callback: Optional[Callable[[dict], None]] = None, stop_event: Optional[threading.Event] = None):
        self.callback = callback or (lambda msg: None)
        self.pipeline_callback = pipeline_callback
        self.stop_event = stop_event
        self.settings = load_settings()
        self._pipeline_running_step = ''
        if config:
            self.settings.update(config)
        self._client: Optional[OpenAI] = None
        self._setup_client()

    def _log(self, msg: str):
        self.callback({'type': 'tool_progress', 'text': msg})
        if self.pipeline_callback:
            self.pipeline_callback({'type': 'debug', 'text': msg})

    def _log_debug_only(self, msg: str):
        """Log to debug panel only, not to chat."""
        if self.pipeline_callback:
            self.pipeline_callback({'type': 'debug', 'text': msg})
        self.callback({'type': 'debug_line', 'text': msg})

    def _debug(self, msg: str):
        import sys
        from datetime import datetime
        ts = datetime.now().strftime('%H:%M:%S')
        print(f'\033[36m[{ts} DEBUG]\033[0m {msg}', file=sys.stderr, flush=True)
        cb = self.pipeline_callback or self.callback
        cb({'type': 'debug', 'text': f'[{ts}] {msg}'})

    def _setup_client(self):
        api_key = self.settings.get('deepseek_api_key') or os.environ.get('DEEPSEEK_API_KEY', '')
        base_url = self.settings.get('deepseek_base_url', 'https://api.deepseek.com')
        if api_key:
            self._client = OpenAI(api_key=api_key, base_url=base_url)

    def set_api_key(self, key: str):
        self.settings['deepseek_api_key'] = key
        save_settings(self.settings)
        self._setup_client()

    def _run_auto_pipeline(self, args: dict) -> dict:
        """Chain: check_status -> download -> unzip -> clip, with task_id."""
        from .config import get_task_dir
        cb = self.pipeline_callback or self.callback
        cb(events.tool_start('auto_pipeline'))
        se = self.stop_event
        results = []
        accounts = load_accounts()
        settings = load_settings()

        task_dir = get_task_dir(args['task_id'])
        task_dir.mkdir(parents=True, exist_ok=True)
        _job_file = str(task_dir / f'{args["task_id"]}.txt')

        # Step 1: Poll status
        self._pipeline_running_step = '2_status'
        self._log('Step 1/4: 检查作业状态...')
        cb(events.ui_pipeline_progress('2_status', 'running'))
        max_checks = 60
        self._debug(f'[状态轮询] 开始轮询 {_job_file}, 最多 {max_checks} 次 (每次30s)')
        status = check_job_status(job_file=_job_file)
        if not status.job_ids or status.total == 0:
            self._debug(f'[状态轮询] job_file 无效或为空: {status.warnings}')
            cb(events.ui_pipeline_progress('2_status', 'failed', f'job文件无效: {status.warnings}'))
            return events.tool_result('auto_pipeline', {
                'error': f'Job file error: {status.warnings}',
                'task_id': args['task_id'],
            })
        for i in range(max_checks):
            if se and se.is_set():
                self._debug('[状态轮询] 收到取消信号')
                cb(events.ui_pipeline_progress('2_status', 'failed', '用户取消'))
                return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
            status = check_job_status(job_file=_job_file)
            results.append(f'[check {i+1}] total={status.total} done={status.succeeded} run={status.running} pend={status.pending} fail={status.failed}')
            self._debug(f'[状态轮询 #{i+1}] total={status.total} done={status.succeeded} run={status.running} pend={status.pending} fail={status.failed}')
            if status.all_done:
                self._debug(f'[状态轮询] 全部完成! url_file={status.url_file}')
                break
            if i < max_checks - 1:
                import time as _time
                for _ in range(30):
                    if se and se.is_set(): break
                    _time.sleep(1)
        if not status.all_done:
            cb(events.ui_pipeline_progress('2_status', 'failed', '超时'))
            return events.tool_result('auto_pipeline', {'error': 'Timeout', 'progress': results})
        if status.failed > 0:
            cb(events.ui_pipeline_progress('2_status', 'failed', f'{status.failed}个失败'))
            return events.tool_result('auto_pipeline', {'error': f'{status.failed} jobs failed', 'progress': results})

        url_file = status.url_file
        cb(events.ui_pipeline_progress('2_status', 'completed', f'{status.succeeded}/{status.total}完成'))
        self._log(f'作业全部完成，开始下载...')

        # Step 2: Download
        save_dir = str(task_dir / 'zip')
        self._pipeline_running_step = '3_download'
        self._debug(f'[下载] 开始下载, url_file={url_file}, save_dir={save_dir}')
        if se and se.is_set():
            self._debug('[下载] 收到取消信号')
            cb(events.ui_pipeline_progress('3_download', 'failed', '用户取消'))
            return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
        self._log('Step 2/4: 下载产品...')
        cb(events.ui_pipeline_progress('3_download', 'running'))
        _dl_user = ''
        _dl_pwd = ''
        _meta = _job_file + '.meta'
        if os.path.isfile(_meta):
            with open(_meta, 'r', encoding='utf-8') as _mf:
                _dl_user = _mf.readline().strip()
                _dl_pwd = _mf.readline().strip()
                self._debug(f'[下载] 使用.meta账号: {_dl_user}')
        if not _dl_user and accounts:
            _dl_user = accounts[0].get('username', '')
            _dl_pwd = accounts[0].get('password', '')
        dl = download_products(
            url_file=url_file, save_dir=save_dir,
            username=_dl_user, password=_dl_pwd,
            max_workers=settings.get('max_download_jobs', 4),
            stop_event=se,
        )
        if se and se.is_set():
            self._debug('[下载] 操作中被取消')
            cb(events.ui_pipeline_progress('3_download', 'failed', '用户取消'))
            return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
        results.append(f'下载: {dl.success_count}/{dl.total_files} 文件, {round(dl.total_bytes/1024/1024,1) if dl.total_bytes else 0} MB')
        self._debug(f'[下载] 完成: {dl.success_count}/{dl.total_files} 文件, {round(dl.total_bytes/1024/1024,1) if dl.total_bytes else 0} MB, 耗时 {dl.elapsed_seconds:.0f}s')
        if dl.failed_count > 0:
            self._debug(f'[下载] FAILED: {dl.failed_count}个文件下载失败')
            cb(events.ui_pipeline_progress('3_download', 'failed', f'{dl.failed_count}个文件下载失败'))
            return events.tool_result('auto_pipeline', {'error': f'{dl.failed_count} download failures', 'task_id': args['task_id']})
        if dl.success_count == 0:
            self._debug(f'[下载] FAILED: 无文件下载成功')
            cb(events.ui_pipeline_progress('3_download', 'failed', '无文件下载成功'))
            return events.tool_result('auto_pipeline', {'error': 'No files downloaded', 'task_id': args['task_id']})
        cb(events.ui_pipeline_progress('3_download', 'completed', f'{dl.success_count}/{dl.total_files}文件'))

        # Step 3: Unzip
        unzip_dir = str(task_dir / 'unzip')
        self._pipeline_running_step = '4_unzip'
        self._log('Step 3/4: 解压产品...')
        if se and se.is_set():
            self._debug('[解压] 收到取消信号')
            cb(events.ui_pipeline_progress('4_unzip', 'failed', '用户取消'))
            return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
        cb(events.ui_pipeline_progress('4_unzip', 'running'))
        zip_files = list(Path(dl.save_dir).glob('*.zip'))
        self._debug(f'[解压] 来源目录: {dl.save_dir}, 待解压文件({len(zip_files)}): {[f.name for f in zip_files]}')
        self._log(f'发现 {len(zip_files)} 个zip文件，开始解压...')
        uz = unzip_products(
            source_dir=dl.save_dir,
            output_dir=unzip_dir,
            max_workers=settings.get('max_unzip_jobs', 4),
            delete_source=settings.get('delete_source_zip', True),
            stop_event=se,
        )
        if se and se.is_set():
            self._debug('[解压] 操作中被取消')
            cb(events.ui_pipeline_progress('4_unzip', 'failed', '用户取消'))
            return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
        results.append(f'解压: {uz.success_count}/{uz.total_files} 文件到 {uz.output_dir}')
        self._debug(f'[解压] 完成: {uz.success_count}/{uz.total_files} 文件 -> {uz.output_dir}, 耗时 {uz.elapsed_seconds:.0f}s')
        if uz.failed_count > 0:
            self._debug(f'[解压] FAILED: {uz.failed_count}个文件解压失败')
            cb(events.ui_pipeline_progress('4_unzip', 'failed', f'{uz.failed_count}个文件解压失败'))
            return events.tool_result('auto_pipeline', {'error': f'{uz.failed_count} unzip failures', 'task_id': args['task_id']})
        if uz.success_count == 0 and uz.total_files > 0:
            self._debug(f'[解压] FAILED: 无文件解压成功')
            cb(events.ui_pipeline_progress('4_unzip', 'failed', '无文件解压成功'))
            return events.tool_result('auto_pipeline', {'error': 'No files unzipped', 'task_id': args['task_id']})
        cb(events.ui_pipeline_progress('4_unzip', 'completed', f'{uz.success_count}/{uz.total_files}文件'))

        # Step 4: Clip
        clip_dir = str(task_dir / 'clip')
        self._pipeline_running_step = '5_clip'
        self._log('Step 4/4: 裁剪到公共重叠区...')
        if se and se.is_set():
            self._debug('[裁剪] 收到取消信号')
            cb(events.ui_pipeline_progress('5_clip', 'failed', '用户取消'))
            return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
        cb(events.ui_pipeline_progress('5_clip', 'running'))
        dem_files = list(Path(uz.output_dir).rglob('*_dem.tif'))
        self._debug(f'[裁剪] 来源目录: {uz.output_dir}, 发现 {len(dem_files)} 个产品目录')
        self._log(f'发现 {len(dem_files)} 个干涉对，开始裁剪...')
        clip = clip_to_common_overlap(data_dir=uz.output_dir, output_dir=clip_dir, stop_event=se)
        if se and se.is_set():
            self._debug('[裁剪] 操作中被取消')
            cb(events.ui_pipeline_progress('5_clip', 'failed', '用户取消'))
            return events.tool_result('auto_pipeline', {'error': 'Cancelled', 'status': 'cancelled'})
        results.append(f'裁剪: 完成, 输出至 {clip.output_dir}')
        self._debug(f'[裁剪] 完成: {clip.clipped_count} 文件 -> {clip.output_dir}, 耗时 {clip.elapsed_seconds:.0f}s')
        if clip.total_files > 0 and clip.clipped_count == 0:
            self._debug(f'[裁剪] FAILED: {clip.total_files}个待裁剪但0个成功')
            cb(events.ui_pipeline_progress('5_clip', 'failed', '无文件被裁剪'))
            return events.tool_result('auto_pipeline', {'error': 'No files clipped', 'task_id': args['task_id']})
        cb(events.ui_pipeline_progress('5_clip', 'completed', clip.output_dir or '完成'))

        data = {
            'status': 'completed',
            'task_id': args['task_id'],
            'clipped_data_dir': clip.output_dir,
            'progress': results,
        }
        return events.tool_result('auto_pipeline', data, ui_update=events.ui_pipeline_done(
            clipped_dir=clip.output_dir,
            task_id=args['task_id'],
        ))

    def _call_tool(self, name: str, args: dict) -> dict:
        self._log(f'调用工具: {name}')
        try:
            if name == 'resolve_location':
                result = resolve_location(args['name'])
                return json.dumps({
                    'name': result.name,
                    'display_name': result.display_name,
                    'lon': result.lon,
                    'lat': result.lat,
                    'half_span': result.half_span,
                    'source': result.source,
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'fetch_boundary':
                result = fetch_boundary(args['name'])
                bbox = None
                if result.geojson_path and Path(result.geojson_path).is_file():
                    try:
                        import json as _json
                        with open(result.geojson_path, 'r', encoding='utf-8') as _f:
                            gj = _json.load(_f)
                        coords = []
                        def _collect(c): 
                            if isinstance(c[0], (int, float)): coords.append(c)
                            else: [_collect(x) for x in c]
                        for feat in gj.get('features', []):
                            _collect(feat['geometry']['coordinates'])
                        if coords:
                            lons = [p[0] for p in coords]; lats = [p[1] for p in coords]
                            bbox = [min(lons), min(lats), max(lons), max(lats)]
                    except Exception:
                        pass
                resp = {
                    'name': result.name,
                    'adcode': result.adcode,
                    'level': result.level,
                    'geojson_path': result.geojson_path,
                    'warnings': result.warnings,
                }
                if bbox:
                    resp['bbox'] = bbox
                return json.dumps(resp, ensure_ascii=False)

            elif name == 'query_slc':
                self._debug(f'[工具] query_slc path={args.get("path")} frame={args.get("frame")} start={args.get("start")} end={args.get("end")} flight_dir={args.get("flight_dir")}')
                result = query_slc(
                    vector=args.get('vector'),
                    start=args.get('start', '2017-01-01'),
                    end=args.get('end', '2023-12-31'),
                    flight_dir=args.get('flight_dir'),
                    path=args.get('path'),
                    frame=args.get('frame'),
                )
                response = {
                    'total_scenes': result.total_scenes,
                    'stacks': [
                        {
                            'index': i, 'path': s.path, 'frame': s.frame,
                            'count': s.count, 'normal_count': s.normal_count,
                            'anomaly_count': s.anomaly_count,
                            'start_date': s.start_date, 'end_date': s.end_date,
                            'flight_direction': s.flight_direction,
                            'scene_names': [sc['name'] for sc in sorted(s.scenes, key=lambda x: x['date'])],
                        }
                        for i, s in enumerate(result.stacks)
                    ],
                    'warnings': result.warnings,
                }
                if result.slc_gdf is not None and len(result.slc_gdf) > 0:
                    import json as _json
                    import geopandas as gpd
                    gdf = result.slc_gdf.to_crs('EPSG:4326')
                    footprints = []
                    seen = set()
                    for _, row in gdf.iterrows():
                        p = int(row['pathNumber']); f = int(row['frameNumber'])
                        key = (p, f)
                        if key in seen: continue
                        seen.add(key)
                        geom = row.geometry
                        if geom is not None and not geom.is_empty:
                            try:
                                gj = _json.loads(gpd.GeoSeries([geom], crs='EPSG:4326').to_json())
                                flight = row.get('flightDirection', '')
                                footprints.append({'path': p, 'frame': f, 'geojson': gj, 'flight_direction': flight})
                            except Exception:
                                pass
                    if footprints:
                        response['footprints'] = footprints
                _cache_query_result(result)
                return json.dumps(response, ensure_ascii=False)

            elif name == 'submit_insar_jobs':
                accounts = load_accounts()
                if not accounts:
                    return json.dumps({
                        'error': 'no_accounts',
                        'message': '未配置 ASF 账号。请先提供 Earthdata 用户名和密码，或告知用户访问 https://urs.earthdata.nasa.gov/ 注册。'
                    }, ensure_ascii=False)
                scene_names, err = _resolve_scene_names(
                    path=args['path'], frame=args['frame'],
                    llm_names=args.get('scene_names'),
                    llm_indices=args.get('scene_indices'),
                )
                if err:
                    return json.dumps({'error': 'scene_resolve', 'message': err}, ensure_ascii=False)
                result = submit_insar_jobs(
                    path=args['path'], frame=args['frame'],
                    account_username=args.get('account_username', ''),
                    project_name=args['project_name'],
                    scene_names=scene_names,
                    max_neighbors=args.get('max_neighbors', 2),
                    insar_opts={
                        'looks': args.get('looks', '20x4'),
                        'include_inc_map': True, 'include_look_vectors': True,
                        'include_dem': True, 'include_displacement_maps': True,
                        'include_wrapped_phase': True, 'apply_water_mask': True,
                    },
                    accounts=accounts if accounts else None,
                    stop_event=self.stop_event,
                )
                pipe_cb = self.pipeline_callback
                if pipe_cb and result.task_id:
                    if result.success_count > 0:
                        _txt = f'{result.success_count}对已提交, ~{result.estimated_cost}cr'
                        if result.failed_count > 0:
                            _txt += f' ({result.failed_count}对失败)'
                        pipe_cb(events.ui_pipeline_progress('1_submit', 'running', _txt))
                        self._debug(f'[提交] task_id={result.task_id}, {result.success_count}/{result.total_pairs}对成功, 预估{result.estimated_cost}cr')
                    else:
                        pipe_cb(events.ui_pipeline_progress('1_submit', 'failed',
                            f'全部{result.total_pairs}对提交失败'))
                        self._debug(f'[提交] FAILED: {result.total_pairs}对全部提交失败')
                return json.dumps({
                    'project_name': result.project_name,
                    'path': result.path, 'frame': result.frame,
                    'total_pairs': result.total_pairs,
                    'success_count': result.success_count,
                    'failed_count': result.failed_count,
                    'job_ids_count': len(result.job_ids),
                    'job_file': result.job_file,
                    'task_id': result.task_id,
                    'account_used': result.account_used,
                    'estimated_cost': result.estimated_cost,
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'check_job_status':
                from .config import get_task_dir as _gtd_cs
                _job_file = str(_gtd_cs(args['task_id']) / f'{args["task_id"]}.txt')
                self._debug(f'[工具] check_job_status task_id={args["task_id"]}')
                result = check_job_status(job_file=_job_file)
                return json.dumps({
                    'total': result.total,
                    'succeeded': result.succeeded,
                    'running': result.running,
                    'pending': result.pending,
                    'failed': result.failed,
                    'all_done': result.all_done,
                    'url_file': result.url_file,
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'download_products':
                from .config import get_task_dir as _gtd_dl
                _task_dir = _gtd_dl(args['task_id'])
                _url_file = str(_task_dir / f'{args["task_id"]}_urls.txt')
                _save_dir = str(_task_dir / 'zip')
                self._debug(f'[工具] download_products task_id={args["task_id"]} url_file={_url_file} save_dir={_save_dir}')
                _meta_file = str(_task_dir / f'{args["task_id"]}.txt.meta')
                _dl_user = ''
                _dl_pwd = ''
                if os.path.isfile(_meta_file):
                    with open(_meta_file, 'r', encoding='utf-8') as _mf:
                        _dl_user = _mf.readline().strip()
                        _dl_pwd = _mf.readline().strip()
                if not _dl_user:
                    accounts = load_accounts()
                    if not accounts:
                        return json.dumps({
                            'error': 'no_accounts',
                            'message': '未配置 ASF 账号，无法下载。请先提供 Earthdata 用户名和密码。'
                        }, ensure_ascii=False)
                    _dl_user = accounts[0]['username']
                    _dl_pwd = accounts[0]['password']
                settings = load_settings()
                result = download_products(
                    url_file=_url_file,
                    save_dir=_save_dir,
                    username=_dl_user,
                    password=_dl_pwd,
                    max_workers=settings.get('max_download_jobs', 4),
                )
                return json.dumps({
                    'save_dir': result.save_dir,
                    'total_files': result.total_files,
                    'success_count': result.success_count,
                    'failed_count': result.failed_count,
                    'total_mb': round(result.total_bytes / 1024 / 1024, 1) if result.total_bytes else 0,
                    'elapsed_minutes': round(result.elapsed_seconds / 60, 1),
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'unzip_products':
                from .config import get_task_dir as _gtd_uz
                _task_dir = _gtd_uz(args['task_id'])
                _source_dir = str(_task_dir / 'zip')
                _output_dir = str(_task_dir / 'unzip')
                self._debug(f'[工具] unzip_products task_id={args["task_id"]} source={_source_dir} output={_output_dir}')
                settings = load_settings()
                result = unzip_products(
                    source_dir=_source_dir,
                    output_dir=_output_dir,
                    max_workers=settings.get('max_unzip_jobs', 4),
                    delete_source=settings.get('delete_source_zip', True),
                )
                return json.dumps({
                    'source_dir': result.source_dir,
                    'output_dir': result.output_dir,
                    'total_files': result.total_files,
                    'success_count': result.success_count,
                    'failed_count': result.failed_count,
                    'skipped_count': result.skipped_count,
                    'elapsed_minutes': round(result.elapsed_seconds / 60, 1),
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'clip_to_common_overlap':
                from .config import get_task_dir as _gtd_cl
                _task_dir = _gtd_cl(args['task_id'])
                _data_dir = str(_task_dir / 'unzip')
                _output_dir = str(_task_dir / 'clip')
                self._debug(f'[工具] clip_to_common_overlap task_id={args["task_id"]} data={_data_dir} output={_output_dir}')
                result = clip_to_common_overlap(
                    data_dir=_data_dir,
                    output_dir=_output_dir,
                )
                return json.dumps({
                    'data_dir': result.data_dir,
                    'output_dir': result.output_dir,
                    'total_files': result.total_files,
                    'clipped_count': result.clipped_count,
                    'skipped_count': result.skipped_count,
                    'excluded_pairs': result.excluded_pairs,
                    'txt_copied': result.txt_copied,
                    'overlap': result.overlap,
                    'elapsed_minutes': round(result.elapsed_seconds / 60, 1),
                    'mintpy_config': result.mintpy_config,
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'run_workflow':
                project_name = args['project_name']
                params = args.get('params', {})
                start_from = args.get('start_from', 0)
                runner = WorkflowRunner(project_name, callback=self._log)
                runner.set_params(**params)
                results = runner.run_all(start_from=start_from)
                return json.dumps(results, ensure_ascii=False, default=str)

            elif name == 'get_workflow_status':
                project_name = args['project_name']
                state_file = get_project_dir(project_name) / 'state.json'
                if state_file.exists():
                    with open(state_file, 'r', encoding='utf-8') as f:
                        return json.dumps(json.load(f), ensure_ascii=False)
                return json.dumps({'error': f'Project not found: {project_name}'})

            elif name == 'run_mintpy':
                from .config import get_task_dir as _gtd3
                _clip_dir = str(_gtd3(args['task_id']) / 'clip')
                self._debug(f'[工具] run_mintpy task_id={args["task_id"]} data_dir={_clip_dir}')
                pipe_cb = self.pipeline_callback or self.callback
                pipe_cb(events.ui_pipeline_progress('mintpy', 'running', 'MintPy 处理中...'))

                def _run_bg():
                    try:
                        result = run_mintpy(data_dir=_clip_dir, callback=self._log_debug_only, stop_event=self.stop_event)
                        if not result.success:
                            import json as _json2
                            overrides_file = _gtd3(args['task_id']) / 'mintpy_overrides.json'
                            ov = {}
                            if overrides_file.is_file():
                                try:
                                    ov = _json2.loads(overrides_file.read_text(encoding='utf-8'))
                                except Exception:
                                    pass
                            if ov.get('mintpy.troposphericDelay.method') == 'pyaps':
                                pipe_cb(events.ui_pipeline_progress('mintpy', 'running', 'ERA5失败，降级重试(无大气校正)...'))
                                ov['mintpy.troposphericDelay.method'] = 'no'
                                overrides_file.write_text(_json2.dumps(ov, ensure_ascii=False, indent=2), encoding='utf-8')
                                for _f in _gtd3(args['task_id']).glob('mintpy/*ERA5*'):
                                    _f.unlink(missing_ok=True)
                                result = run_mintpy(data_dir=_clip_dir, callback=self._log_debug_only, stop_event=self.stop_event)
                        if result.success:
                            from .tools.mintpy import post_mintpy_h5_to_tif, post_mintpy_mask_velocity, post_mintpy_standardize_names
                            mintpy_dir2 = _gtd3(args['task_id']) / 'mintpy'
                            pipe_cb(events.ui_pipeline_progress('mintpy', 'running', 'h5转tif...'))
                            post_mintpy_h5_to_tif(mintpy_dir2, callback=self._debug)
                            pipe_cb(events.ui_pipeline_progress('mintpy', 'running', '掩膜处理...'))
                            post_mintpy_mask_velocity(mintpy_dir2, callback=self._debug)
                            pipe_cb(events.ui_pipeline_progress('mintpy', 'running', '标准化命名...'))
                            post_mintpy_standardize_names(mintpy_dir2, callback=self._debug)
                            pipe_cb(events.ui_pipeline_progress('mintpy', 'running', '打包结果中...'))
                            import zipfile
                            zip_path = mintpy_dir2 / '_mintpy_results.zip'
                            files = [f for f in sorted(mintpy_dir2.rglob('*')) if f.is_file()]
                            total_tifs = sum(1 for f in files if f.suffix == '.tif')
                            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
                                for fpath in files:
                                    zf.write(fpath, str(fpath.relative_to(mintpy_dir2)))
                            pipe_cb(events.ui_pipeline_progress('mintpy', 'completed',
                                f'{len(result.output_files)}个输出文件, {total_tifs}个tif'))
                            pipe_cb(events.tool_result('run_mintpy', {
                                'config_path': result.config_path,
                                'success': True,
                                'output_files': result.output_files,
                                'elapsed_minutes': round(result.elapsed_seconds / 60, 1) if result.elapsed_seconds else 0,
                            }, ui_update={
                                'type': 'mintpy_done', 'success': True,
                                'task_id': args['task_id'],
                                'output_count': len(result.output_files),
                                'elapsed_minutes': round(result.elapsed_seconds / 60, 1) if result.elapsed_seconds else 0,
                                'zip_url': f'/api/mintpy/zip/{args["task_id"]}',
                            }))
                        else:
                            pipe_cb(events.ui_pipeline_progress('mintpy', 'failed', result.error or '未知错误'))
                            pipe_cb(events.tool_result('run_mintpy', {
                                'success': False,
                                'error': result.error,
                            }, ui_update={
                                'type': 'mintpy_done', 'success': False,
                                'error': result.error or '未知错误',
                            }))
                    except Exception as e:
                        pipe_cb(events.ui_pipeline_progress('mintpy', 'failed', str(e)))
                        pipe_cb(events.agent_error(str(e)))
                    finally:
                        pipe_cb(events.agent_done())
                threading.Thread(target=_run_bg, daemon=True).start()
                return json.dumps({'status': 'started', 'task_id': args['task_id'],
                                   'message': 'MintPy started in background.'}, ensure_ascii=False)

            elif name == 'search_knowledge':
                result = search_knowledge(
                    query=args['query'],
                    top_k=args.get('top_k', 3),
                )
                return json.dumps({
                    'query': result.query,
                    'total_found': result.total_found,
                    'results': result.results,
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'search_catalog':
                result = search_catalog(
                    vector=args.get('geojson_path'),
                    date_start=args.get('date_start', ''),
                    date_end=args.get('date_end', ''),
                    name_query=args.get('name_query', ''),
                    top_k=args.get('top_k', 50),
                )
                resp = {
                    'total': result.total,
                    'matches': result.matches,
                    'footprints': result.footprints,
                    'warnings': result.warnings,
                }
                return json.dumps(resp, ensure_ascii=False)

            elif name == 'check_credits':
                result = check_credits()
                return json.dumps({
                    'accounts': result.accounts,
                    'total_credits': result.total_credits,
                    'warnings': result.warnings,
                }, ensure_ascii=False)

            elif name == 'save_credentials':
                accounts = load_accounts()
                found = False
                for acc in accounts:
                    if acc['username'] == args['username']:
                        acc['password'] = args['password']
                        found = True
                        break
                if not found:
                    accounts.append({'username': args['username'], 'password': args['password']})
                save_accounts(accounts)
                return json.dumps({'saved': True, 'username': args['username']}, ensure_ascii=False)

            elif name == 'auto_pipeline':
                pipe_cb = self.pipeline_callback or self.callback
                task_id = args.get('task_id', '')
                proj = args.get('project_name', '')

                if not task_id and proj:
                    from .config import _PROJECTS_DIR as _pd
                    candidates = sorted(Path(str(_pd)).glob(f'*{proj}*'), key=lambda p: p.stat().st_mtime, reverse=True)
                    if candidates:
                        task_id = candidates[0].name
                    else:
                        return json.dumps({'error': f'No task found for {proj}'}, ensure_ascii=False)

                if not task_id:
                    return json.dumps({'error': 'Missing task_id or project_name'}, ensure_ascii=False)

                args['task_id'] = task_id

                self._debug(f'[Pipeline] auto_pipeline启动, task_id={task_id}')
                pipe_cb(events.ui_pipeline_progress('2_status', 'running', '轮询作业状态...'))

                def _run_bg():
                    try:
                        result = self._run_auto_pipeline(args)
                        pipe_cb(result)
                    except Exception as e:
                        pipe_cb(events.agent_error(str(e)))
                    finally:
                        self._pipeline_running_step = ''
                        pipe_cb(events.agent_done())
                threading.Thread(target=_run_bg, daemon=True).start()
                return json.dumps({'status': 'started', 'task_id': task_id,
                                   'message': 'Pipeline started in background.'}, ensure_ascii=False)

            elif name == 'save_mintpy_overrides':
                from .tools.mintpy import save_mintpy_overrides
                save_mintpy_overrides(args['task_id'], args['overrides'])
                return json.dumps({'saved': True, 'task_id': args['task_id']}, ensure_ascii=False)

            elif name == 'view_result':
                from .tools.catalog import _load_index
                entries = _load_index()
                entry = next((e for e in entries if e.get('id') == args.get('entry_id')), None)
                if not entry and args.get('project_name'):
                    pn = args['project_name'].lower()
                    entry = next((e for e in entries if pn in e.get('name', '').lower()), None)
                if not entry:
                    return json.dumps({'error': f'Entry not found: {args.get("entry_id", args.get("project_name", "?"))}'})
                entry_id = entry.get('id', '')
                show = args.get('show', 'cumulative')
                result = {'entry_id': entry_id, 'entry_name': entry['name'],
                          'path': entry['path'], 'frame': entry['frame'],
                          'date_start': entry.get('date_start', ''), 'date_end': entry.get('date_end', '')}
                vel_path = entry.get('files', {}).get('velocity', '')
                ts_path = entry.get('files', {}).get('timeseries', '')
                if show == 'cumulative' and ts_path:
                    result['cum_url'] = f'/api/timeseries/cumulative-map?path={ts_path}'
                    result['ts_path'] = ts_path
                if show == 'velocity' and vel_path:
                    result['velocity_url'] = f'/api/render-tiff?path={vel_path}'
                if show == 'timeseries' and ts_path:
                    result['timeseries_url'] = f'/api/timeseries/plot?path={ts_path}'
                result['vel_path'] = vel_path
                # Emit ui_update so frontend renders the image
                try:
                    pipe_cb_vr = self.pipeline_callback or self.callback
                    ui = events.ui_view_result(
                        entry_id=entry_id, entry_name=entry['name'],
                        path=entry['path'], frame=entry['frame'],
                        date_start=entry.get('date_start', ''),
                        date_end=entry.get('date_end', ''),
                        cum_url=result.get('cum_url', ''),
                        velocity_url=result.get('velocity_url', ''),
                        timeseries_url=result.get('timeseries_url', ''),
                        ts_path=ts_path, vel_path=vel_path,
                    )
                    pipe_cb_vr(events.tool_result('view_result', result, ui_update=ui))
                except Exception:
                    pass
                return json.dumps(result, ensure_ascii=False)

            elif name == 'analyze_result':
                from .tools.catalog import _load_index
                entries = _load_index()
                entry = next((e for e in entries if e.get('id') == args.get('entry_id')), None)
                if not entry and args.get('project_name'):
                    pn = args['project_name'].lower()
                    entry = next((e for e in entries if pn in e.get('name', '').lower()), None)
                if not entry:
                    return json.dumps({'error': f'Entry not found: {args["entry_id"]}'})
                vel_path = entry.get('files', {}).get('velocity', '')
                ts_path = entry.get('files', {}).get('timeseries', '')
                import numpy as np
                stats = {'entry_name': entry['name'], 'entry_id': entry['id']}
                if vel_path:
                    try:
                        import rasterio
                        with rasterio.open(vel_path) as src:
                            data = src.read(1).astype(np.float32)
                            nodata = src.nodata
                            if nodata is not None:
                                data = np.where(data == nodata, np.nan, data)
                        valid = data[~np.isnan(data)]
                        stats['vel_mean_mm_yr'] = round(float(np.nanmean(valid)) * 1000, 2)
                        stats['vel_min_mm_yr'] = round(float(np.nanmin(valid)) * 1000, 2)
                        stats['vel_max_mm_yr'] = round(float(np.nanmax(valid)) * 1000, 2)
                        stats['vel_std_mm_yr'] = round(float(np.nanstd(valid)) * 1000, 2)
                    except Exception as e:
                        stats['vel_error'] = str(e)
                if ts_path:
                    try:
                        import h5py
                        with h5py.File(ts_path, 'r') as f:
                            ts = f.get('timeseries')
                            if ts is not None:
                                cum = np.array(ts[-1], dtype=float) - np.array(ts[0], dtype=float)
                                c_valid = cum[~np.isnan(cum)]
                                stats['cum_mean_mm'] = round(float(np.nanmean(c_valid)) * 1000, 2)
                                stats['cum_min_mm'] = round(float(np.nanmin(c_valid)) * 1000, 2)
                                stats['cum_max_mm'] = round(float(np.nanmax(c_valid)) * 1000, 2)
                    except Exception as e:
                        stats['ts_error'] = str(e)
                return json.dumps(stats, ensure_ascii=False)

            elif name == 'preview_mintpy_config':
                from .config import get_task_dir as _gtd2
                _clip_dir = str(_gtd2(args['task_id']) / 'clip')
                self._debug(f'[工具] preview_mintpy_config task_id={args["task_id"]} data_dir={_clip_dir}')
                cfg = generate_mintpy_config(
                    data_dir=_clip_dir,
                )
                config_content = ''
                if cfg.config_path and Path(cfg.config_path).is_file():
                    config_content = Path(cfg.config_path).read_text(encoding='utf-8')
                return json.dumps({
                    'config_path': cfg.config_path,
                    'data_dir': cfg.data_dir,
                    'output_dir': cfg.output_dir,
                    'config_content': config_content,
                    'warnings': cfg.warnings,
                }, ensure_ascii=False)

            elif name == 'run_analysis':
                from .config import get_task_dir as _gtd4
                task_dir = _gtd4(args['task_id'])
                mintpy_dir = task_dir / 'mintpy'
                if not mintpy_dir.is_dir():
                    return json.dumps({'error': f'MintPy results not found for {args["task_id"]}'}, ensure_ascii=False)

                vel_files = sorted(mintpy_dir.glob('vel_*.tif'))
                if not vel_files:
                    vel_files = sorted(mintpy_dir.glob('velocity.tif'))
                ts_files = sorted(mintpy_dir.glob('timeseries_ramp_demErr.h5'))
                if not ts_files:
                    ts_files = sorted(mintpy_dir.glob('timeseries*.h5'))

                if not vel_files or not ts_files:
                    return json.dumps({
                        'error': f'Missing analysis input files. velocity: {bool(vel_files)}, timeseries: {bool(ts_files)}',
                    }, ensure_ascii=False)

                vel_path = str(vel_files[0])
                ts_path = str(ts_files[0])

                self._debug(f'[Analysis] run_analysis task_id={args["task_id"]}')
                pipe_cb = self.pipeline_callback or self.callback
                pipe_cb(events.ui_pipeline_progress('analysis', 'running', '形变分析启动...'))

                def _run_analysis_bg():
                    try:
                        from .analysis.report import run_analysis as do_analysis
                        pipe_cb(events.ui_pipeline_progress('analysis', 'running', '漏斗检测+统计计算...'))
                        report = do_analysis(vel_path, ts_path, str(mintpy_dir), args['task_id'])

                        if report.get('success'):
                            funnel_count = report.get('funnel_count', 0)
                            vel = report.get('velocity', {})
                            cum = report.get('cumulative', {})
                            accel = report.get('acceleration', {})
                            summary = f'检测到{funnel_count}个沉降漏斗, ' \
                                f'最大速率{vel.get("min_mm_yr", "?")}mm/yr, ' \
                                f'最大累计{str(cum.get("max_displacement_mm", "?"))}mm, ' \
                                f'加速漏斗{accel.get("accelerated_count", 0)}个'

                            pipe_cb(events.ui_pipeline_progress('analysis', 'completed', summary))
                            pipe_cb(events.tool_result('run_analysis', {
                                'success': True,
                                'task_id': args['task_id'],
                                'funnel_count': funnel_count,
                                'velocity_mean_mm_yr': vel.get('mean_mm_yr', 0),
                                'velocity_min_mm_yr': vel.get('min_mm_yr', 0),
                                'cumulative_max_mm': cum.get('max_displacement_mm', 0),
                                'volume_loss_km3': cum.get('total_volume_loss_km3', 0),
                                'accelerated_count': accel.get('accelerated_count', 0),
                                'chart_count': len(report.get('charts', [])),
                            }, ui_update={
                                'type': 'analysis_done', 'success': True,
                                'task_id': args['task_id'],
                                'zip_url': f'/api/analysis/zip/{args["task_id"]}',
                                'funnel_count': funnel_count,
                                'velocity_min_mm_yr': vel.get('min_mm_yr', 0),
                                'cumulative_max_mm': cum.get('max_displacement_mm', 0),
                                'volume_loss_km3': cum.get('total_volume_loss_km3', 0),
                                'accelerated_count': accel.get('accelerated_count', 0),
                            }))
                        else:
                            err = report.get('error', '未知错误')
                            pipe_cb(events.ui_pipeline_progress('analysis', 'failed', err))
                            pipe_cb(events.tool_result('run_analysis', {
                                'success': False, 'error': err,
                            }, ui_update={
                                'type': 'analysis_done', 'success': False, 'error': err,
                            }))
                    except Exception as e:
                        pipe_cb(events.ui_pipeline_progress('analysis', 'failed', str(e)))
                        pipe_cb(events.agent_error(str(e)))
                    finally:
                        pipe_cb(events.agent_done())

                threading.Thread(target=_run_analysis_bg, daemon=True).start()
                return json.dumps({'status': 'started', 'task_id': args['task_id'],
                                   'message': 'Analysis started in background.'}, ensure_ascii=False)

            else:
                return json.dumps({'error': f'Unknown tool: {name}'})

        except Exception as e:
            return json.dumps({'error': str(e)})

    def chat(self, user_message: str, history: Optional[list[dict]] = None) -> tuple[str, list[dict]]:
        """Process a user message through the agent loop with conversation history.

        Args:
            user_message: The user's input message.
            history: Optional conversation history (list of message dicts). If None, starts fresh.

        Returns:
            (response_text, updated_history) - The LLM's final text response and the full
            message history (can be passed to the next chat() call for context).
        """
        if not self._client:
            return (
                '请先配置 DeepSeek API Key:\n'
                '  insar-agent config --api-key YOUR_DEEPSEEK_API_KEY\n'
                '或设置环境变量 DEEPSEEK_API_KEY',
                [],
            )

        if not history:
            messages = [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_message},
            ]
        else:
            messages = list(history)
            if messages and messages[0].get('role') == 'system':
                messages[0] = {'role': 'system', 'content': SYSTEM_PROMPT}
            else:
                messages.insert(0, {'role': 'system', 'content': SYSTEM_PROMPT})
            messages.append({'role': 'user', 'content': user_message})

        model = self.settings.get('deepseek_model', 'deepseek-v4-flash')

        _force_tool = False
        _prog_kw = ('托管', '确认', '继续', '好的', '好', '可以', '行', '嗯嗯', '是', '要', '加', '开', '提交', '跑', '发', '开始', '重试', '显示', '查看', '看图', '看看', '打开', '看下', '展示', '分析')
        _um = user_message.strip()
        _force_tool = any(kw in _um for kw in _prog_kw)
        if not _force_tool and len(_um) <= 6:
            _last_asst = ''
            for _m in reversed(messages):
                if _m.get('role') == 'assistant' and _m.get('content'):
                    _last_asst = _m.get('content', '')
                    break
            _tail = _last_asst[-20:] if len(_last_asst) >= 20 else _last_asst
            if '是否' in _last_asst or '请' in _tail:
                _force_tool = True

        for i in range(10):
            if i > 0:
                self.callback(f'[推理] 第{i+1}轮思考...')
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice='required' if _force_tool else 'auto',
            )
            _force_tool = False

            msg = response.choices[0].message

            # Show thinking/reasoning if present
            reasoning = getattr(msg, 'reasoning_content', None)
            if reasoning:
                self.callback(f'[思考] {reasoning[:500]}')
            elif msg.content and msg.tool_calls:
                self.callback(f'[推理] {msg.content[:500]}')

            if msg.tool_calls:
                messages.append({
                    'role': 'assistant', 'content': msg.content,
                    'tool_calls': [
                        {'id': tc.id, 'type': 'function',
                         'function': {'name': tc.function.name, 'arguments': tc.function.arguments}}
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    tool_result = self._call_tool(tc.function.name, args)
                    self.callback(json.dumps({'_tool': tc.function.name, **json.loads(tool_result)}, ensure_ascii=False))
                    messages.append({
                        'role': 'tool',
                        'tool_call_id': tc.id,
                        'content': tool_result,
                    })
            else:
                messages.append({'role': 'assistant', 'content': msg.content})
                return msg.content or '', messages

        return 'Agent loop exceeded maximum iterations.', messages
