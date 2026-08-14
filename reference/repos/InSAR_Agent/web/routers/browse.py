import os
from pathlib import Path

from fastapi import APIRouter, Query

from web.core import translate_path

router = APIRouter(prefix='/api/browse', tags=['browse'])

_BROWSE_ROOTS = [
    translate_path(d) if ':' in d or '\\' in d else d
    for d in os.environ.get('BROWSE_ROOTS', '/app/isce2_gpu,/app/output,/app/projects,/app/data').split(',')
    if d.strip()
]


@router.get('/mappings')
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


@router.get('')
async def api_browse(path: str = Query(default='')):
    path = translate_path(path) if path else ''
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


@router.get('/resolve')
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
