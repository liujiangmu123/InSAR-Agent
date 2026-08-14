# InSAR Agent

**AI-powered conversational InSAR processing** — natural language driven Sentinel-1 InSAR time-series analysis, powered by DeepSeek LLM + MintPy + HyP3.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-supported-2496ED.svg)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Features

- **Conversational NL Interface** — Interact with the entire InSAR pipeline via natural language chat
- **6-Step Automated Pipeline** — Query SLC → Submit InSAR → Check Status → Download → Unzip → Clip to Overlap
- **MintPy Integration** — Time-series SBAS processing with ERA5 atmospheric correction
- **Deformation Analysis** — Velocity maps, cumulative displacement, time-series, gradient analysis, zoning statistics
- **Result Catalog** — Spatial searchable archive with auto-import from processed outputs
- **Real-time SSE Streaming** — Live progress updates via Server-Sent Events
- **Docker Deployment** — One-command deployment with all dependencies (GDAL, MintPy, HyP3)

## Architecture

```
User (NL Chat)
    │
    ▼
┌──────────────────────────────────────┐
│  Web UI (FastAPI + SSE streaming)    │
├──────────────────────────────────────┤
│  InSAR Agent (DeepSeek LLM)          │
│  ├─ Tool-use: query / submit /       │
│  │  download / unzip / clip / mintpy │
│  ├─ Workflow Engine (pause/resume)   │
│  └─ Analysis Engine                  │
├──────────────────────────────────────┤
│  External Services                   │
│  ├─ ASF Search (SLC query)           │
│  ├─ HyP3 (InSAR job submission)      │
│  └─ CDS (ERA5 atmospheric data)      │
└──────────────────────────────────────┘
```

## Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/KevinTyn/InSAR_Agent.git
cd InSAR_Agent
cp .env.example .env
# Edit .env — set DEEPSEEK_API_KEY and optional credentials
```

### 2. Docker (Recommended)

```bash
# Development (with hot-reload)
docker compose up -d

# Production
docker compose -f docker-compose.prod.yml up -d
```

Open http://localhost:8866 in your browser.

### 3. Manual Install

```bash
pip install -e .
python -m uvicorn web.app:app --host 0.0.0.0 --port 8866 --reload
```

## Usage

Chat with the agent in natural language:

- **"查询北京地区2023年的Sentinel-1数据"** — Query SLC data
- **"提交处理"** — Submit InSAR jobs
- **"检查进度"** — Check job status
- **"下载并预处理"** — Download + unzip + clip
- **"运行MintPy时序分析，用ERA5大气校正"** — Run MintPy with atmospheric correction
- **"分析形变结果"** — Trigger deformation analysis

Or use the **auto_pipeline** mode — one command to run the entire 6-step workflow.

## Project Structure

```
InSAR_Agent/
├── src/insar_agent/
│   ├── agent.py              # LLM agent core (DeepSeek tool-use)
│   ├── workflow.py           # 6-step pipeline engine
│   ├── tools/                # Tool implementations
│   │   ├── query_slc.py      # ASF SLC search
│   │   ├── submit_insar.py   # HyP3 job submission
│   │   ├── check_status.py   # Job status monitoring
│   │   ├── download.py       # Product download
│   │   ├── unzip.py          # Archive extraction
│   │   ├── clip.py           # Overlap clipping
│   │   ├── mintpy.py         # MintPy time-series processing
│   │   ├── catalog.py        # Result catalog management
│   │   └── geocode.py        # Location resolution
│   ├── analysis/             # Deformation analysis modules
│   │   ├── timeseries.py     # Time-series analysis
│   │   ├── cumulative.py     # Cumulative displacement
│   │   ├── gradient.py       # Spatial gradient
│   │   ├── zoning.py         # Zonal statistics
│   │   ├── charts.py         # Visualization
│   │   └── report.py         # Markdown report generation
│   ├── config.py             # Settings management
│   └── events.py             # Event bus
├── web/
│   ├── app.py                # FastAPI application
│   ├── core.py               # Core utilities
│   ├── routers/              # API route handlers
│   └── static/               # Web UI
├── scripts/
│   └── batch_standardize.py  # MintPy file naming standardization
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
└── pyproject.toml
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | No | Custom API endpoint (default: `https://api.deepseek.com`) |
| `DEEPSEEK_MODEL` | No | Model name (default: `deepseek-v4-flash`) |
| `HYP3_USERNAME` | No | ASF/HyP3 username for job submission |
| `HYP3_PASSWORD` | No | ASF/HyP3 password |
| `CDSAPI_KEY` | No | CDS API key for ERA5 atmospheric correction |

## Dependencies

- **Python 3.10+**
- **GDAL** (system library)
- **MintPy** — InSAR time-series analysis
- **DeepSeek API** (OpenAI-compatible) — LLM orchestration
- **ASF Search** — SLC data discovery
- **HyP3 SDK** — InSAR job submission & management
- **FastAPI + Uvicorn** — Web server

## License

MIT
