from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter(tags=['pages'])

_static = Path(__file__).resolve().parent.parent / 'static'


@router.get('/', response_class=HTMLResponse)
async def index():
    html = _static / 'index.html'
    if html.is_file():
        resp = Response(content=html.read_text(encoding='utf-8'), media_type='text/html')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return '<h1>index.html not found</h1>'


@router.get('/upload', response_class=HTMLResponse)
async def upload_page():
    f = _static / 'upload.html'
    if f.is_file():
        return f.read_text(encoding='utf-8')
    return '<h1>upload.html not found</h1>'


@router.get('/viewer', response_class=HTMLResponse)
async def viewer_page():
    f = _static / 'viewer.html'
    if f.is_file():
        return f.read_text(encoding='utf-8')
    return '<h1>viewer.html not found</h1>'
