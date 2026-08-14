from fastapi import APIRouter, Request

from insar_agent.tools.catalog import search_catalog as _catalog_search
from insar_agent.tools.catalog import register_result as _catalog_register
from insar_agent.tools.catalog import auto_import as _catalog_auto_import
from web.core import translate_files, translate_path

router = APIRouter(prefix='/api/catalog', tags=['catalog'])


@router.get('')
async def api_catalog(name: str = '', lon: float = None, lat: float = None):
    result = _catalog_search(lon=lon, lat=lat, name_query=name)
    return {'total': result.total, 'matches': result.matches, 'warnings': result.warnings}


@router.post('/register')
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
            result_files=translate_files(body.get('files', {})),
            path=int(body.get('path', 0)),
            frame=int(body.get('frame', 0)),
        )
        return {'status': 'ok', 'entry': entry}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


@router.post('/auto-import')
async def api_auto_import(request: Request):
    body = await request.json()
    base_dir = translate_path(body.get('dir', ''))
    dry_run = body.get('preview', False)
    if not base_dir:
        return {'status': 'error', 'message': 'dir parameter required'}
    try:
        imported = _catalog_auto_import(base_dir, dry_run=dry_run, fast=False)
        action = 'preview' if dry_run else 'import'
        return {'status': 'ok', 'action': action, 'count': len(imported), 'entries': imported}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


@router.delete('/{entry_id}')
async def api_catalog_delete(entry_id: str):
    from insar_agent.tools.catalog import _load_index, _save_index
    index = _load_index()
    new_entries = [e for e in index if e.get('id') != entry_id]
    if len(new_entries) == len(index):
        return {'status': 'error', 'message': f'Entry not found: {entry_id}'}
    _save_index(new_entries)
    return {'status': 'ok', 'deleted': entry_id}
