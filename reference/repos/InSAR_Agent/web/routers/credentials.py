from fastapi import APIRouter, Request

from web.core import get_or_create_sid, load_credentials, save_credentials

router = APIRouter(prefix='/api/credentials', tags=['credentials'])


@router.post('')
async def api_save_credentials(request: Request):
    body = await request.json()
    sid = get_or_create_sid(request)
    creds = load_credentials(sid)
    creds.update(body)
    save_credentials(sid, creds)
    return {'status': 'ok'}


@router.get('')
async def api_get_credentials(request: Request):
    sid = get_or_create_sid(request)
    return {'status': 'ok', 'credentials': load_credentials(sid)}
