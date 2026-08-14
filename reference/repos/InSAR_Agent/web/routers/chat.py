import json
import queue
import threading

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from insar_agent.agent import InsarAgent
from web.core import get_or_create_sid, load_history, save_history, sse_event

router = APIRouter(tags=['chat'])


@router.post('/chat')
async def chat(request: Request):
    body = await request.json()
    user_message: str = body.get('message', '')
    history: list[dict] = body.get('history') or []

    if not user_message.strip():
        return StreamingResponse(
            iter([sse_event('error', {'message': 'Empty message'})]),
            media_type='text/event-stream',
        )

    sid = get_or_create_sid(request)
    if not history:
        history = load_history(sid)

    event_queue: queue.Queue = queue.Queue()

    def callback(event: dict):
        etype = event.get('type', '')
        if etype == 'thinking':
            event_queue.put(('thinking', {'text': event['text']}))
        elif etype == 'tool_start':
            event_queue.put(('tool_progress', {'text': f'\u8c03\u7528\u5de5\u5177: {event["tool_name"]}'}))
        elif etype == 'tool_result':
            event_queue.put(('tool_progress', {'text': f'\u5de5\u5177\u5b8c\u6210: {event["tool_name"]}'}))
            ui = event.get('ui_update')
            if ui:
                event_queue.put(('ui_update', ui))
        elif etype == 'tool_progress':
            event_queue.put(('tool_progress', {'text': event['text']}))
        elif etype == 'pipeline_progress':
            event_queue.put(('ui_update', event))
        elif etype == 'agent_response':
            event_queue.put(('response', {'text': event['text']}))
        elif etype == 'error':
            event_queue.put(('error', {'message': event['message']}))
        elif etype == 'done':
            event_queue.put(('done', {}))

    agent = InsarAgent(callback=callback)

    def run_chat():
        try:
            response, new_history = agent.chat(user_message, history=history)
            save_history(sid, new_history)
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
                yield sse_event(event_type, data)
                if event_type in ('done', 'error'):
                    break
            except queue.Empty:
                yield sse_event('error', {'message': 'Request timeout'})
                break

    resp = StreamingResponse(generate(), media_type='text/event-stream')
    resp.set_cookie('insar_sid', sid, httponly=True, samesite='lax', max_age=86400 * 30)
    return resp
