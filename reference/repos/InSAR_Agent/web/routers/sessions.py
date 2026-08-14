import json

from fastapi import APIRouter, Request, Response

from web.core import SESSIONS_DIR, get_or_create_sid, load_history

router = APIRouter(prefix='/api/sessions', tags=['sessions'])


@router.get('')
async def api_sessions(request: Request):
    sid_current = get_or_create_sid(request)
    sessions = []
    for f in sorted(SESSIONS_DIR.glob('*.json'), key=lambda x: x.stat().st_mtime, reverse=True):
        stem = f.stem
        if stem.endswith('_creds'):
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


@router.get('/current')
async def api_session_current(request: Request):
    sid = get_or_create_sid(request)
    history = load_history(sid)
    return {'status': 'ok', 'sid': sid, 'history': history}


@router.post('/new')
async def api_session_new():
    import secrets
    new_sid = secrets.token_hex(16)
    resp = Response(content=json.dumps({'status': 'ok', 'sid': new_sid}), media_type='application/json')
    resp.set_cookie('insar_sid', new_sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return resp


@router.post('/{sid}/load')
async def api_session_load(sid: str):
    f = SESSIONS_DIR / f'{sid}.json'
    if not f.is_file():
        return Response(content=json.dumps({'status': 'error', 'message': 'Session not found'}),
                        media_type='application/json', status_code=404)
    resp = Response(content=json.dumps({'status': 'ok', 'sid': sid}), media_type='application/json')
    resp.set_cookie('insar_sid', sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return resp


@router.post('/{sid}/rename')
async def api_session_rename(sid: str, request: Request):
    body = await request.json()
    new_title = body.get('title', '').strip()
    if not new_title:
        return {'status': 'error', 'message': 'Title cannot be empty'}
    f = SESSIONS_DIR / f'{sid}.json'
    if not f.is_file():
        return {'status': 'error', 'message': 'Session not found'}
    data = json.loads(f.read_text(encoding='utf-8'))
    if isinstance(data, list):
        data = {'messages': data}
    data['_title'] = new_title
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'status': 'ok', 'sid': sid, 'title': new_title}


@router.delete('/{sid}')
async def api_session_delete(sid: str):
    f = SESSIONS_DIR / f'{sid}.json'
    creds_f = SESSIONS_DIR / f'{sid}_creds.json'
    deleted = False
    if f.is_file():
        f.unlink()
        deleted = True
    if creds_f.is_file():
        creds_f.unlink()
    return {'status': 'ok', 'sid': sid, 'deleted': deleted}
