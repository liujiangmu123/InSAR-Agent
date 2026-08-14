"""InSAR Agent Web Chat - FastAPI + SSE streaming + results + visualization"""
import json
import os
import queue
import re
import sys
import threading
import hashlib
import secrets
import time
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

_src = Path(__file__).resolve().parent.parent / 'src'
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from insar_agent.agent import InsarAgent
from insar_agent.tools.catalog import search_catalog as _catalog_search
from insar_agent.tools.catalog import register_result as _catalog_register
from insar_agent.tools.catalog import auto_import as _catalog_auto_import


from web.core import (
    translate_path as _translate_path,
    translate_files as _translate_files,
    load_history as _load_history,
    load_session_title as _load_session_title,
    save_history as _save_history,
    load_credentials as _load_credentials,
    save_credentials as _save_credentials,
    sse_event as _sse_event,
)


# ── Simple session: cookie-based anonymous identity, persists chat history ──

_DATA_DIR = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))
_SESSIONS_DIR = _DATA_DIR / 'sessions'
_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _get_or_create_sid(request: Request, response: Response = None) -> str:
    sid = request.cookies.get('insar_sid')
    if not sid:
        sid = secrets.token_hex(16)
        if response:
            response.set_cookie('insar_sid', sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return sid


def _save_ui_event(sid: str, ui: dict):
    if not isinstance(ui, dict) or ui.get('type') == 'pipeline_progress':
        return
    f = _SESSIONS_DIR / f'{sid}_ui.json'
    events = []
    if f.is_file():
        try:
            events = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            pass
    events.append(ui)
    if len(events) > 100:
        events = events[-80:]
    f.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_ui_events(sid: str) -> list[dict]:
    f = _SESSIONS_DIR / f'{sid}_ui.json'
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


app = FastAPI(title='InSAR Agent')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

static = Path(__file__).parent / 'static'
if static.is_dir():
    app.mount('/static', StaticFiles(directory=str(static)), name='static')

_PROJECTS_DIR = _DATA_DIR / 'projects'
_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Pipeline state management
_pipeline_queues: dict[str, queue.Queue] = {}
_pipeline_stop_events: dict[str, threading.Event] = {}
_pipeline_states: dict[str, dict] = {}
_pipeline_states_lock = threading.Lock()
_PIPELINE_STATES_FILE = _DATA_DIR / 'pipeline_states.json'


def _load_pipeline_states():
    if _PIPELINE_STATES_FILE.is_file():
        try:
            data = json.loads(_PIPELINE_STATES_FILE.read_text(encoding='utf-8'))
            for steps in data.values():
                for val in steps.values():
                    if val.get('status') == 'running':
                        val['status'] = 'failed'
                        val['text'] = (val.get('text', '') + ' (服务重启)').strip()
            return data
        except Exception:
            pass
    return {}


def _save_pipeline_states():
    with _pipeline_states_lock:
        _PIPELINE_STATES_FILE.write_text(
            json.dumps(_pipeline_states, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )


_pipeline_states = _load_pipeline_states()

_DEBUG_MAX_LINES = 500


def _save_debug_line(sid: str, text: str):
    f = _SESSIONS_DIR / f'{sid}_debug.json'
    lines = []
    if f.is_file():
        try:
            lines = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            lines = []
    lines.append(text)
    if len(lines) > _DEBUG_MAX_LINES:
        lines = lines[-_DEBUG_MAX_LINES:]
    f.write_text(json.dumps(lines, ensure_ascii=False), encoding='utf-8')


def _load_debug_lines(sid: str) -> list[str]:
    f = _SESSIONS_DIR / f'{sid}_debug.json'
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


# ── Pages ──

@app.get('/', response_class=HTMLResponse)
async def index():
    html = static / 'index.html'
    if html.is_file():
        resp = Response(content=html.read_text(encoding='utf-8'), media_type='text/html')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return '<h1>index.html not found</h1>'


@app.get('/upload', response_class=HTMLResponse)
async def upload_page():
    f = static / 'upload.html'
    if f.is_file():
        return f.read_text(encoding='utf-8')
    return '<h1>upload.html not found</h1>'


@app.get('/viewer', response_class=HTMLResponse)
async def viewer_page():
    f = static / 'viewer.html'
    if f.is_file():
        return f.read_text(encoding='utf-8')
    return '<h1>viewer.html not found</h1>'


# ── Chat endpoint (core) ──

@app.post('/chat')
async def chat(request: Request):
    body = await request.json()
    user_message: str = body.get('message', '')
    history: list[dict] = body.get('history') or []

    if not user_message.strip():
        return StreamingResponse(
            iter([_sse_event('error', {'message': 'Empty message'})]),
            media_type='text/event-stream',
        )

    sid = _get_or_create_sid(request)
    disk_history = _load_history(sid)
    if disk_history:
        history = disk_history
    elif not history:
        history = []

    if sid not in _pipeline_queues:
        _pipeline_queues[sid] = queue.Queue()
    pipeline_queue = _pipeline_queues[sid]

    event_queue: queue.Queue = queue.Queue()

    def callback(msg):
        if isinstance(msg, dict):
            if msg.get('type') == 'debug_line':
                event_queue.put(('debug_line', {'text': msg.get('text', str(msg))}))
            else:
                event_queue.put(('tool_progress', {'text': msg.get('text', str(msg))}))
            return
        extra = None
        if msg.startswith('[思考]') or msg.startswith('[推理]'):
            event_queue.put(('thinking', {'text': msg[4:]}))
            return
        try:
            obj = json.loads(msg)
            if '_tool' in obj:
                tool_name = obj.pop('_tool')
                event_queue.put(('tool_progress', {'text': f'调用工具: {tool_name}'}))
                extra = _extract_ui(obj, tool_name)
            else:
                event_queue.put(('tool_progress', {'text': msg}))
        except (json.JSONDecodeError, AttributeError):
            event_queue.put(('tool_progress', {'text': msg}))
            extra = _extract_text(msg)

        if extra:
            event_queue.put(('ui_update', extra))
            _save_ui_event(sid, extra)

    def pipeline_callback(event: dict):
        etype = event.get('type', '')
        if etype == 'pipeline_progress':
            pipeline_queue.put(('pipeline_progress', event))
            st = _pipeline_states.get(sid, {})
            st[event['step']] = {'status': event['status'], 'text': event.get('text', '')}
            _pipeline_states[sid] = st
            _save_pipeline_states()
            event_queue.put(('ui_update', event))
        elif etype == 'debug':
            pipeline_queue.put(('debug', event))
            _save_debug_line(sid, event.get('text', ''))
        elif etype == 'tool_progress':
            pipeline_queue.put(('tool_progress', {'text': event['text']}))
        elif etype == 'tool_start':
            pipeline_queue.put(('tool_progress', {'text': f'Pipeline: {event["tool_name"]}'}))
        elif etype == 'tool_result':
            ui = event.get('ui_update')
            if ui:
                pipeline_queue.put(('ui_update', ui))
                event_queue.put(('ui_update', ui))
                _save_ui_event(sid, ui)
        elif etype == 'error':
            pipeline_queue.put(('error', {'message': event['message']}))
            event_queue.put(('error', {'message': f'[Pipeline] {event["message"]}'}))
        elif etype == 'done':
            pipeline_queue.put(('done', {}))

    stop_event = threading.Event()
    _pipeline_stop_events[sid] = stop_event
    agent = InsarAgent(callback=callback, pipeline_callback=pipeline_callback, stop_event=stop_event)

    def run_chat():
        try:
            response, new_history = agent.chat(user_message, history=history)
            _save_history(sid, new_history)
            event_queue.put(('response', {'text': response}))
        except Exception as e:
            event_queue.put(('error', {'message': str(e)}))
        finally:
            event_queue.put(('done', {}))

    thread = threading.Thread(target=run_chat, daemon=True)
    thread.start()

    def generate():
        while True:
            try:
                event_type, data = event_queue.get(timeout=120)
                yield _sse_event(event_type, data)
                if event_type in ('done', 'error'):
                    break
            except queue.Empty:
                yield _sse_event('error', {'message': 'Request timeout'})
                break

    resp = StreamingResponse(generate(), media_type='text/event-stream')
    resp.set_cookie('insar_sid', sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return resp


@app.get('/api/pipeline/stream')
async def pipeline_stream(request: Request):
    sid = _get_or_create_sid(request)
    pq = _pipeline_queues.get(sid)
    if not pq:
        return StreamingResponse(
            iter([_sse_event('done', {})]),
            media_type='text/event-stream',
        )

    def generate():
        while True:
            try:
                event_type, data = pq.get(timeout=30)
                yield _sse_event(event_type, data)
                if event_type in ('done', 'error'):
                    break
            except queue.Empty:
                yield ': heartbeat\n\n'

    return StreamingResponse(generate(), media_type='text/event-stream')


@app.post('/api/pipeline/cancel')
async def pipeline_cancel(request: Request):
    sid = _get_or_create_sid(request)
    stop_evt = _pipeline_stop_events.get(sid)
    if stop_evt:
        stop_evt.set()
        return {'status': 'ok', 'message': 'Cancel signal sent'}
    return {'status': 'ok', 'message': 'No active pipeline'}


@app.get('/api/pipeline/status')
async def pipeline_status(request: Request):
    sid = _get_or_create_sid(request)
    state = _pipeline_states.get(sid, {})
    return {'status': 'ok', 'steps': state}


@app.get('/api/pipeline/debug')
async def pipeline_debug(request: Request):
    sid = _get_or_create_sid(request)
    lines = _load_debug_lines(sid)
    return {'status': 'ok', 'lines': lines}


def _extract_ui(data: dict, tool_name: str) -> dict | None:
    if tool_name in ('resolve_location', 'fetch_boundary') and 'lon' in data:
        result = {'type': 'aoi', 'lon': data['lon'], 'lat': data['lat'],
                'label': data.get('display_name', data.get('name', 'AOI'))}
        if data.get('geojson_path'):
            result['geojson_url'] = '/api/file?path=' + data['geojson_path']
        return result
    if tool_name == 'fetch_boundary' and data.get('geojson_path'):
        bbox = data.get('bbox')
        lon = (bbox[0] + bbox[2]) / 2 if bbox else 0
        lat = (bbox[1] + bbox[3]) / 2 if bbox else 0
        return {'type': 'aoi', 'lon': lon, 'lat': lat, 'label': data.get('name', ''),
                'geojson_url': '/api/file?path=' + data['geojson_path']}
    if tool_name in ('query_slc', 'discover_stacks'):
        result = {'type': 'slc_result', 'total': data.get('total_scenes', data.get('total_stacks', 0)),
                  'stacks': data.get('stacks', [])}
        if data.get('coverage_image'):
            result['coverage_url'] = '/api/file?path=' + data['coverage_image']
        if data.get('footprints'):
            result['footprints'] = data['footprints']
        return result
    if tool_name == 'submit_insar_jobs':
        return {'type': 'job_submitted', 'estimated_cost': data.get('estimated_cost', 0),
                'job_file': data.get('job_file', ''), 'total_pairs': data.get('total_pairs', 0)}
    if tool_name == 'auto_pipeline' and data.get('status') == 'completed':
        return {'type': 'pipeline_done', 'clipped_dir': data.get('clipped_data_dir', '')}
    if tool_name == 'preview_mintpy_config':
        return {'type': 'mintpy_config', 'config_path': data.get('config_path', ''),
                'data_dir': data.get('data_dir', '')}
    if tool_name == 'search_catalog' and data.get('total', 0) > 0:
        overlays = []
        for fp in data.get('footprints', []):
            if fp.get('geojson'):
                overlays.append({
                    'geojson': fp['geojson'],
                    'name': fp.get('name', ''),
                    'path': fp.get('path'), 'frame': fp.get('frame'),
                    'date_start': fp.get('date_start', ''), 'date_end': fp.get('date_end', ''),
                })
        return {'type': 'catalog_results', 'total': data['total'],
                'matches': data.get('matches', []), 'overlays': overlays,
                'footprints': data.get('footprints', [])}
    if tool_name == 'view_result':
        ui = {'type': 'view_result', **data}
        return ui
    return None


def _extract_text(msg: str) -> dict | None:
    import re
    lon_m = re.search(r'lon[=:]\s*([\d.-]+)', msg)
    lat_m = re.search(r'lat[=:]\s*([\d.-]+)', msg)
    if lon_m and lat_m:
        return {'type': 'aoi', 'lon': float(lon_m.group(1)), 'lat': float(lat_m.group(1))}
    return None


# ── Sessions API ──

@app.get('/api/sessions')
async def api_sessions(request: Request):
    """List all historical sessions with title and metadata."""
    sid_current = _get_or_create_sid(request)
    sessions = []
    for f in sorted(_SESSIONS_DIR.glob('*.json'), key=lambda x: x.stat().st_mtime, reverse=True):
        stem = f.stem
        if stem.endswith('_creds') or stem.endswith('_debug'):
            continue
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list) and not (isinstance(data, dict) and 'messages' in data):
            continue
        messages = data if isinstance(data, list) else data.get('messages', [])
        if not messages:
            continue
        custom_title = data.get('_title') if isinstance(data, dict) else None
        first_user = custom_title or next((m['content'][:40] for m in messages if m.get('role') == 'user'), '(空)')
        msg_count = len(messages)
        mtime = int(f.stat().st_mtime)
        sessions.append({
            'sid': stem,
            'title': first_user,
            'has_custom_title': bool(custom_title),
            'msg_count': msg_count,
            'mtime': mtime,
            'is_current': stem == sid_current,
        })
    return {'status': 'ok', 'sessions': sessions}


@app.get('/api/sessions/current')
async def api_session_current(request: Request):
    """Return the current session's message history."""
    sid = _get_or_create_sid(request)
    history = _load_history(sid)
    ui_events = _load_ui_events(sid)
    return {'status': 'ok', 'sid': sid, 'history': history, 'ui_events': ui_events}


@app.post('/api/sessions/new')
async def api_session_new(response: Response):
    """Create a new session (generate a new sid)."""
    import secrets
    new_sid = secrets.token_hex(16)
    resp = Response(content=json.dumps({'status': 'ok', 'sid': new_sid}), media_type='application/json')
    resp.set_cookie('insar_sid', new_sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return resp


@app.post('/api/sessions/{sid}/load')
async def api_session_load(sid: str, request: Request, response: Response):
    """Switch to a different session by updating the cookie."""
    f = _SESSIONS_DIR / f'{sid}.json'
    if not f.is_file():
        return Response(content=json.dumps({'status': 'error', 'message': 'Session not found'}), media_type='application/json', status_code=404)
    resp = Response(content=json.dumps({'status': 'ok', 'sid': sid}), media_type='application/json')
    resp.set_cookie('insar_sid', sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return resp


@app.post('/api/sessions/{sid}/rename')
async def api_session_rename(sid: str, request: Request):
    """Rename a session by setting a custom title."""
    body = await request.json()
    new_title = body.get('title', '').strip()
    if not new_title:
        return {'status': 'error', 'message': 'Title cannot be empty'}
    f = _SESSIONS_DIR / f'{sid}.json'
    if not f.is_file():
        return {'status': 'error', 'message': 'Session not found'}
    data = json.loads(f.read_text(encoding='utf-8'))
    if isinstance(data, list):
        data = {'messages': data}
    data['_title'] = new_title
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'status': 'ok', 'sid': sid, 'title': new_title}


@app.delete('/api/sessions/{sid}')
async def api_session_delete(sid: str):
    """Delete a session and its credentials."""
    f = _SESSIONS_DIR / f'{sid}.json'
    creds_f = _SESSIONS_DIR / f'{sid}_creds.json'
    deleted = False
    if f.is_file():
        f.unlink()
        deleted = True
    if creds_f.is_file():
        creds_f.unlink()
    return {'status': 'ok', 'sid': sid, 'deleted': deleted}


# ── Credentials API (called by agent tools) ──

@app.post('/api/credentials')
async def api_save_credentials(request: Request):
    """Store credentials in session (called by agent when user provides them)."""
    body = await request.json()
    sid = _get_or_create_sid(request)
    creds = _load_credentials(sid)
    creds.update(body)
    _save_credentials(sid, creds)
    return {'status': 'ok'}


@app.get('/api/credentials')
async def api_get_credentials(request: Request):
    sid = _get_or_create_sid(request)
    return {'status': 'ok', 'credentials': _load_credentials(sid)}


# ── Catalog ──

@app.get('/api/catalog')
async def api_catalog(name: str = '', lon: float = None, lat: float = None):
    result = _catalog_search(lon=lon, lat=lat, name_query=name)
    return {'total': result.total, 'matches': result.matches, 'warnings': result.warnings}


@app.post('/api/catalog/register')
async def api_catalog_register(request: Request):
    body = await request.json()
    try:
        entry = _catalog_register(
            name=body.get('name', ''),
            center_lon=float(body.get('lon', 0)),
            center_lat=float(body.get('lat', 0)),
            bbox=(
                float(body.get('bbox_west', 0)),
                float(body.get('bbox_south', 0)),
                float(body.get('bbox_east', 0)),
                float(body.get('bbox_north', 0)),
            ),
            date_start=body.get('date_start', ''),
            date_end=body.get('date_end', ''),
            result_files=_translate_files(body.get('files', {})),
            path=int(body.get('path', 0)),
            frame=int(body.get('frame', 0)),
        )
        return {'status': 'ok', 'entry': entry}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


@app.post('/api/catalog/auto-import')
async def api_auto_import(request: Request):
    body = await request.json()
    base_dir = _translate_path(body.get('dir', ''))
    dry_run = body.get('preview', False)
    if not base_dir:
        return {'status': 'error', 'message': 'dir parameter required'}
    try:
        imported = _catalog_auto_import(base_dir, dry_run=dry_run, fast=False)
        action = 'preview' if dry_run else 'import'
        return {'status': 'ok', 'action': action, 'count': len(imported), 'entries': imported}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


@app.delete('/api/catalog/{entry_id}')
async def api_catalog_delete(entry_id: str):
    from insar_agent.tools.catalog import _load_index, _save_index
    index = _load_index()
    new_entries = [e for e in index if e.get('id') != entry_id]
    if len(new_entries) == len(index):
        return {'status': 'error', 'message': f'Entry not found: {entry_id}'}
    _save_index(new_entries)
    return {'status': 'ok', 'deleted': entry_id}


@app.get('/api/catalog/list')
async def api_catalog_list():
    from insar_agent.tools.catalog import _load_index
    entries = _load_index()
    return {'total': len(entries), 'entries': entries}


# ── Directory browser ──

_BROWSE_ROOTS = [
    _translate_path(d) if ':' in d or '\\' in d else d
    for d in os.environ.get('BROWSE_ROOTS', '/app/isce2_gpu,/app/output,/app/projects,/app/data').split(',')
    if d.strip()
]


@app.get('/api/browse/mappings')
async def api_browse_mappings():
    raw = os.environ.get('WIN_PATH_MAP', '')
    mappings = []
    if raw:
        for pair in raw.split(';'):
            pair = pair.strip()
            if '=' in pair:
                k, v = pair.split('=', 1)
                mappings.append({'windows': k.strip(), 'docker': v.strip()})
    return {'status': 'ok', 'mappings': mappings}


@app.get('/api/browse')
async def api_browse(path: str = Query(default='')):
    path = _translate_path(path) if path else ''
    p = Path(path) if path else Path('/')
    if not p.exists():
        return {'status': 'error', 'message': f'Path not found: {path}'}
    if not p.is_dir():
        p = p.parent

    parent = str(p.parent) if str(p) != '/' and p.parent != p else None
    if parent:
        for root in _BROWSE_ROOTS:
            if str(p.resolve()).startswith(str(Path(root).resolve()) + os.sep) or str(p.resolve()) == str(Path(root).resolve()):
                break
        else:
            parent = None

    items = []
    try:
        for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.name.startswith('.') or entry.is_symlink():
                continue
            items.append({
                'name': entry.name,
                'is_dir': entry.is_dir(),
                'path': str(entry).replace('\\', '/'),
            })
    except PermissionError:
        return {'status': 'error', 'message': 'Permission denied'}

    return {
        'status': 'ok',
        'current': str(p).replace('\\', '/'),
        'parent': parent.replace('\\', '/') if parent else None,
        'roots': [r.replace('\\', '/') for r in _BROWSE_ROOTS],
        'items': items,
    }


@app.get('/api/browse/resolve')
async def api_browse_resolve(name: str = Query(default=''), children: str = Query(default='')):
    if not name:
        return {'status': 'error', 'message': 'name required'}

    roots = os.environ.get('BROWSE_ROOTS', '/app/isce2_gpu,/app/output,/app/projects,/app/data').split(',')
    roots = [r.strip() for r in roots if r.strip()]
    child_set = set(c.strip() for c in children.split(',') if c.strip()) if children else None

    candidates = []
    for root in roots:
        p = Path(root)
        if not p.is_dir():
            continue
        for found in p.rglob(name):
            if not found.is_dir():
                continue
            path = str(found).replace('\\', '/')
            score = 0
            if child_set:
                try:
                    sub_names = {x.name for x in found.iterdir() if x.is_dir()}
                    score = len(child_set & sub_names)
                except PermissionError:
                    score = 0
            candidates.append((path, score))

    if not candidates:
        return {'status': 'error', 'message': f'Not found: {name}'}
    candidates.sort(key=lambda x: -x[1])
    return {'status': 'ok', 'path': candidates[0][0]}


# ── Results & download ──

_RESULT_EXTENSIONS = {'.h5', '.tif', '.png', '.txt', '.json', '.geojson', '.cfg'}


def _scan_all_files(base_dir: Path) -> list[dict]:
    files = []
    if not base_dir.is_dir():
        return files
    for root, _, filenames in os.walk(base_dir):
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _RESULT_EXTENSIONS:
                fpath = Path(root) / fn
                rel = fpath.relative_to(base_dir)
                size_kb = round(fpath.stat().st_size / 1024, 1)
                files.append({'name': fn, 'path': str(rel), 'size_kb': size_kb, 'project': rel.parts[0] if rel.parts else ''})
    files.sort(key=lambda f: (f['project'], f['name']))
    return files


@app.get('/api/results')
async def api_results():
    return {'projects_dir': str(_PROJECTS_DIR), 'files': _scan_all_files(_PROJECTS_DIR)}


@app.get('/download', response_class=HTMLResponse)
async def download_page():
    files = _scan_all_files(_PROJECTS_DIR)
    rows = []
    for f in files:
        rows.append(f'<tr><td>{f["project"]}</td><td>{f["name"]}</td><td>{f["size_kb"]} KB</td><td><a href="/download-file?path={f["path"]}">Download</a></td></tr>')
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>InSAR Agent - Download</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#1a1a2e;color:#e0e0e0;padding:30px}}h1{{color:#00ff88;margin-bottom:8px}}.sub{{color:#888;font-size:13px;margin-bottom:20px}}table{{width:100%;border-collapse:collapse;background:#16213e}}th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid #0f3460}}th{{background:#0f3460}}a{{color:#00ff88}}a:hover{{text-decoration:underline}}.back{{display:inline-block;margin-bottom:16px;color:#888;font-size:14px;text-decoration:none}}.empty{{color:#555;text-align:center;padding:40px}}</style></head><body>
<a class="back" href="/">Back to Chat</a><h1>Results</h1><p class="sub">({len(files)} files)</p>
{('<table><tr><th>Project</th><th>Filename</th><th>Size</th><th></th></tr>'+''.join(rows)+'</table>') if rows else '<p class="empty">No results yet.</p>'}
</body></html>'''


@app.get('/download-file')
async def download_file(path: str = Query(...)):
    full_path = (_PROJECTS_DIR / path).resolve()
    if not str(full_path).startswith(str(_PROJECTS_DIR.resolve())):
        return {'error': 'Invalid path'}
    if not full_path.is_file():
        return {'error': 'File not found'}
    return FileResponse(full_path, filename=full_path.name)


@app.get('/api/mintpy/zip/{task_id}')
async def api_mintpy_zip(task_id: str):
    from insar_agent.config import get_task_dir
    zip_path = get_task_dir(task_id) / 'mintpy' / '_mintpy_results.zip'
    if not zip_path.is_file():
        return Response(content='{"error":"Zip not ready, MintPy may not have completed yet"}', status_code=404, media_type='application/json')
    return FileResponse(zip_path, media_type='application/zip', filename=f'mintpy_{task_id}.zip')


@app.get('/api/analysis/zip/{task_id}')
async def api_analysis_zip(task_id: str):
    from insar_agent.config import get_task_dir
    zip_path = get_task_dir(task_id) / 'mintpy' / 'analysis' / '_analysis_results.zip'
    if not zip_path.is_file():
        return Response(content='{"error":"Analysis zip not ready"}', status_code=404, media_type='application/json')
    return FileResponse(zip_path, media_type='application/zip', filename=f'analysis_{task_id}.zip')


# ── Visualization ──

@app.get('/api/file')
async def api_file(path: str = Query(...)):
    """Serve any file from known project/data directories (coverage maps, output files)."""
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': 'File not found'}
    resolved = str(p.resolve())
    allowed = ['/app/src/projects', '/app/projects', '/app/data', '/app/output', '/app/isce2_gpu', '/app/src/insar_agent/data']
    if not any(resolved.startswith(a) for a in allowed):
        return {'error': 'Access denied'}
    return FileResponse(p)


@app.get('/api/render-tiff')
async def api_render_tiff(path: str = Query(...), cmap: str = Query(default='jet'), vmin: float = Query(default=None), vmax: float = Query(default=None)):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    try:
        import rasterio
    except ImportError:
        return {'error': 'rasterio not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with rasterio.open(str(p)) as src:
            data = src.read(1).astype(np.float32)
            nodata = src.nodata
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
        data *= 1000
        valid = data[~np.isnan(data)]
        if vmin is None: vmin = float(np.percentile(valid, 2))
        if vmax is None: vmax = float(np.percentile(valid, 98))
        threshold = (vmax - vmin) * 0.005 if vmax > vmin else 1e-6
        data = np.where(np.abs(data) < threshold, np.nan, data)
        rows, cols = data.shape
        # Raw 1:1 RGBA from colormap
        import matplotlib as mpl
        cmap_obj = plt.cm.jet
        cmap_obj.set_bad('white')
        norm_data = (data - vmin) / (vmax - vmin)
        norm_data = np.clip(norm_data, 0, 1)
        rgba = cmap_obj(norm_data)
        rgba[np.isnan(data)] = [1, 1, 1, 0]
        rgba_uint8 = (rgba * 255).astype(np.uint8)
        buf_data = BytesIO()
        plt.imsave(buf_data, rgba_uint8, format='png')
        buf_data.seek(0)
        # Try to add colorbar below; fall back to raw image if compositing fails
        try:
            fig, ax = plt.subplots(figsize=(max(cols / 100, 6), 0.35), dpi=200)
            norm_bar = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
            cb = mpl.colorbar.ColorbarBase(ax, cmap=cmap_obj, norm=norm_bar, orientation='horizontal')
            cb.set_label('Velocity (mm/yr)', color='#333', fontsize=40)
            cb.ax.tick_params(labelsize=32, colors='#333')
            buf_cbar = BytesIO()
            fig.savefig(buf_cbar, format='png', dpi=100, bbox_inches='tight', facecolor='white', edgecolor='white')
            plt.close(fig); buf_cbar.seek(0)
            from PIL import Image
            img_data = Image.open(buf_data)
            img_cbar = Image.open(buf_cbar).convert('RGBA')
            cbar_h = int(img_cbar.height * cols / img_cbar.width)
            img_cbar = img_cbar.resize((cols, cbar_h), Image.LANCZOS)
            composite = Image.new('RGBA', (cols, rows + cbar_h), (255, 255, 255, 255))
            composite.paste(img_data.convert('RGBA'), (0, 0))
            composite.paste(img_cbar, (0, rows))
            buf = BytesIO()
            composite.save(buf, format='PNG')
            buf.seek(0)
            resp = Response(content=buf.getvalue(), media_type='image/png')
        except Exception:
            buf_data.seek(0)
            resp = Response(content=buf_data.getvalue(), media_type='image/png')
        resp.headers['X-Data-Width'] = str(cols)
        resp.headers['X-Data-Height'] = str(rows)
        return resp
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/tiff/pixel')
async def api_tiff_pixel(path: str = Query(...), row: int = Query(...), col: int = Query(...)):
    """Return the value at a specific pixel in a GeoTIFF (for hover tooltip)."""
    import numpy as np
    try:
        import rasterio
    except ImportError:
        return {'error': 'rasterio not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with rasterio.open(str(p)) as src:
            if row < 0 or row >= src.height or col < 0 or col >= src.width:
                return {'error': 'out of bounds', 'row': row, 'col': col}
            val = src.read(1)[row, col].astype(np.float32)
            nodata = src.nodata
        if nodata is not None and val == nodata:
            return {'value': None, 'row': row, 'col': col, 'label': 'NoData'}
        return {'value': float(val) * 1000, 'row': row, 'col': col, 'label': f'{float(val)*1000:.2f} mm/yr'}
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/timeseries/info')
async def api_timeseries_info(path: str = Query(...)):
    try:
        import h5py
    except ImportError:
        return {'error': 'h5py not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with h5py.File(str(p), 'r') as f:
            ts = f.get('timeseries')
            if ts is None:
                return {'error': 'No timeseries dataset found'}
            shape = ts.shape
            dates = None
            if 'date' in f:
                dset = f['date']
                dates = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dset[:]]
            return {'status': 'ok', 'shape': list(shape), 'num_dates': shape[0], 'rows': shape[1], 'cols': shape[2], 'dates': dates[:20] if dates else None, 'total_dates': len(dates) if dates else 0}
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/timeseries/data')
async def api_timeseries_data(path: str = Query(...), row: int = Query(...), col: int = Query(...)):
    try:
        import h5py
    except ImportError:
        return {'error': 'h5py not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with h5py.File(str(p), 'r') as f:
            ts = f.get('timeseries')
            if ts is None:
                return {'error': 'No timeseries dataset found'}
            if row < 0 or row >= ts.shape[1] or col < 0 or col >= ts.shape[2]:
                return {'error': 'Pixel out of bounds'}
            values = [float(v) for v in ts[:, row, col]]
            dates = None
            if 'date' in f:
                dset = f['date']
                dates = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dset[:]]
            return {'status': 'ok', 'row': row, 'col': col, 'dates': dates, 'displacement_m': values}
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/timeseries/plot')
async def api_timeseries_plot(path: str = Query(...)):
    """Generate a time series displacement plot from a MintPy HDF5 file, returns PNG."""
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    try:
        import h5py
    except ImportError:
        return {'error': 'h5py not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with h5py.File(str(p), 'r') as f:
            ts = f.get('timeseries')
            if ts is None:
                return {'error': 'No timeseries dataset found'}
            values = np.nanmean(ts[:], axis=(1, 2))
            dates = None
            if 'date' in f:
                dset = f['date']
                dates = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dset[:]]
        fig, ax = plt.subplots(figsize=(10, 4))
        color = '#1a73e8'
        if dates and len(dates) == len(values):
            import matplotlib.dates as mdates
            from datetime import datetime
            dts = [datetime.strptime(d, '%Y%m%d') for d in dates]
            ax.plot(dts, values * 1000, 'o-', markersize=3, linewidth=1, color=color)
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        else:
            ax.plot(values * 1000, 'o-', markersize=3, linewidth=1, color=color)
        ax.axhline(0, color='#ef4444', linewidth=0.5, linestyle='--')
        ax.set_xlabel('Date' if dates else 'Index', color='#333')
        ax.set_ylabel('Displacement (mm)', color='#333')
        ax.set_title(f'Mean Time Series — {p.name}', color='#333')
        ax.tick_params(colors='#333')
        ax.grid(True, alpha=0.2, color='#ccc')
        fig.autofmt_xdate()
        fig.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=120, facecolor='white')
        plt.close(fig)
        buf.seek(0)
        return Response(content=buf.getvalue(), media_type='image/png')
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/timeseries/pixel-plot')
async def api_timeseries_pixel_plot(path: str = Query(...), row: int = Query(...), col: int = Query(...), vel_path: str = Query(default='')):
    """Generate a time series plot for a specific pixel. Returns PNG."""
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    try:
        import h5py
    except ImportError:
        return {'error': 'h5py not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with h5py.File(str(p), 'r') as f:
            ts = f.get('timeseries')
            if ts is None:
                return {'error': 'No timeseries dataset found'}
            if row < 0 or row >= ts.shape[1] or col < 0 or col >= ts.shape[2]:
                return {'error': f'Pixel ({row},{col}) out of bounds [{ts.shape[1]},{ts.shape[2]}]'}
            values = np.array(ts[:, row, col], dtype=float)
            dates = None
            if 'date' in f:
                dset = f['date']
                dates = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dset[:]]
        cum_disp = (values[-1] - values[0]) * 1000
        vel_val = None
        if vel_path:
            vp = Path(_translate_path(vel_path))
            if vp.is_file():
                try:
                    import rasterio
                    with rasterio.open(str(vp)) as src:
                        vel_data = src.read(1).astype(np.float32)
                        if 0 <= row < vel_data.shape[0] and 0 <= col < vel_data.shape[1]:
                            vel_val = float(vel_data[row, col]) * 1000
                except Exception:
                    pass
        title = f'Pixel ({row}, {col}) — Cum: {cum_disp:.1f} mm'
        if vel_val is not None:
            title += f' | Vel: {vel_val:.1f} mm/yr'
        fig, ax = plt.subplots(figsize=(10, 4))
        vals_mm = values * 1000
        color = '#1a73e8'
        if dates and len(dates) == len(values):
            import matplotlib.dates as mdates
            from datetime import datetime
            dts = [datetime.strptime(d, '%Y%m%d') for d in dates]
            ax.plot(dts, vals_mm, 'o-', markersize=4, linewidth=1.5, color=color)
            ax.fill_between(dts, 0, vals_mm, alpha=0.1, color=color)
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        else:
            ax.plot(vals_mm, 'o-', markersize=4, linewidth=1.5, color=color)
            ax.fill_between(range(len(vals_mm)), 0, vals_mm, alpha=0.1, color=color)
        ax.set_xlabel('Date' if dates else 'Index', color='#333')
        ax.set_ylabel('Displacement (mm)', color='#333')
        ax.set_title(title, color='#333')
        ax.tick_params(colors='#333')
        ax.grid(True, alpha=0.2, color='#ccc')
        fig.autofmt_xdate()
        fig.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=120, facecolor='white')
        plt.close(fig)
        buf.seek(0)
        return Response(content=buf.getvalue(), media_type='image/png')
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/tiff-meta')
async def api_tiff_meta(path: str = Query(...)):
    """Return width/height of a GeoTIFF."""
    try:
        import rasterio
    except ImportError:
        return {'error': 'rasterio not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with rasterio.open(str(p)) as src:
            return {'status': 'ok', 'width': src.width, 'height': src.height}
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/tiff-image')
async def api_tiff_image(path: str = Query(...), w: int = Query(default=0)):
    """Serve a GeoTIFF as a plain PNG image (no matplotlib wrapper), suitable for click interaction."""
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    try:
        import rasterio
    except ImportError:
        return {'error': 'rasterio not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with rasterio.open(str(p)) as src:
            data = src.read(1).astype(np.float32)
            nodata = src.nodata
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
        valid = data[~np.isnan(data)]
        vmin, vmax = float(np.percentile(valid, 2)), float(np.percentile(valid, 98))
        threshold = (vmax - vmin) * 0.005 if vmax > vmin else 1e-6
        data = np.where(np.abs(data) < threshold, np.nan, data)
        norm = plt.Normalize(vmin, vmax)
        cmap = plt.cm.jet.copy()
        cmap.set_bad('white')
        rgba = cmap(norm(data))
        rgba[..., 3] = np.where(np.isnan(data), 0, 1)
        rgba_uint8 = (rgba * 255).astype(np.uint8)
        import io
        from PIL import Image
        img = Image.fromarray(rgba_uint8)
        if img.width > 800:
            ratio = 800 / img.width
            img = img.resize((800, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        resp = Response(content=buf.getvalue(), media_type='image/png')
        resp.headers['X-Tiff-Width'] = str(src.width)
        resp.headers['X-Tiff-Height'] = str(src.height)
        return resp
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/timeseries/cumulative-map')
async def api_timeseries_cumulative(path: str = Query(...), step: int = Query(default=-1)):
    """Generate a 1:1 pixel cumulative displacement map from HDF5. step=-1 for last frame."""
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    try:
        import h5py
    except ImportError:
        return {'error': 'h5py not installed'}
    p = Path(_translate_path(path)) if path else None
    if not p or not p.is_file():
        return {'error': f'File not found: {path}'}
    try:
        with h5py.File(str(p), 'r') as f:
            ts = f.get('timeseries')
            if ts is None:
                return {'error': 'No timeseries dataset found'}
            if step < 0 or step >= ts.shape[0]:
                step = ts.shape[0] - 1
            cum = np.array(ts[step], dtype=float) - np.array(ts[0], dtype=float)
            num_steps = ts.shape[0]
            dates = None
            if 'date' in f:
                dset = f['date']
                dates = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dset[:]]
        cum *= 1000
        valid = cum[~np.isnan(cum)]
        vmin, vmax = float(np.percentile(valid, 2)), float(np.percentile(valid, 98))
        threshold = (vmax - vmin) * 0.005 if vmax > vmin else 1e-6
        cum = np.where(np.abs(cum) < threshold, np.nan, cum)
        rows, cols = cum.shape
        # Render raw RGBA array at 1:1 (no interpolation, no padding, no axes)
        cmap = plt.cm.jet.copy()
        cmap.set_bad('white')
        norm_cum = (cum - vmin) / (vmax - vmin)
        norm_cum = np.clip(norm_cum, 0, 1)
        rgba = cmap(norm_cum)
        rgba[np.isnan(cum)] = [1, 1, 1, 0]
        rgba_uint8 = (rgba * 255).astype(np.uint8)
        buf_data = BytesIO()
        plt.imsave(buf_data, rgba_uint8, format='png')
        buf_data.seek(0)
        try:
            import matplotlib as mpl
            fig, ax = plt.subplots(figsize=(max(cols / 100, 6), 0.35), dpi=200)
            norm_bar = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
            cb = mpl.colorbar.ColorbarBase(ax, cmap=cmap, norm=norm_bar, orientation='horizontal')
            cb.set_label('Displacement (mm)', color='#333', fontsize=40)
            cb.ax.tick_params(labelsize=32, colors='#333')
            buf_cbar = BytesIO()
            fig.savefig(buf_cbar, format='png', dpi=100, bbox_inches='tight', facecolor='white', edgecolor='white')
            plt.close(fig); buf_cbar.seek(0)
            from PIL import Image
            img_data = Image.open(buf_data)
            img_cbar = Image.open(buf_cbar).convert('RGBA')
            cbar_h = int(img_cbar.height * cols / img_cbar.width)
            img_cbar = img_cbar.resize((cols, cbar_h), Image.LANCZOS)
            composite = Image.new('RGBA', (cols, rows + cbar_h), (255, 255, 255, 255))
            composite.paste(img_data.convert('RGBA'), (0, 0))
            composite.paste(img_cbar, (0, rows))
            buf = BytesIO()
            composite.save(buf, format='PNG')
            buf.seek(0)
            resp = Response(content=buf.getvalue(), media_type='image/png')
        except Exception:
            buf_data.seek(0)
            resp = Response(content=buf_data.getvalue(), media_type='image/png')
        resp.headers['X-Data-Width'] = str(cols)
        resp.headers['X-Data-Height'] = str(rows)
        resp.headers['X-Num-Steps'] = str(num_steps)
        resp.headers['X-Current-Step'] = str(step)
        if dates:
            resp.headers['X-Date'] = dates[step]
        return resp
    except Exception as e:
        return {'error': str(e)}


if __name__ == '__main__':
    import uvicorn
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 8866))
    uvicorn.run(app, host=host, port=port, log_level='info')
