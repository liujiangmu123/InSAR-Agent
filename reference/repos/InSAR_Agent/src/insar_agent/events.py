"""Typed event protocol for agent → web communication.

Every event is a plain dict with a 'type' key. Tools emit
ToolResultEvent dicts that include both LLM data and optional
frontend UiUpdate payloads — no more _extract_ui() guessing.
"""


# ── Agent lifecycle events ──

def thinking(text: str) -> dict:
    return {'type': 'thinking', 'text': text}


def tool_start(tool_name: str) -> dict:
    return {'type': 'tool_start', 'tool_name': tool_name}


def tool_result(tool_name: str, data: dict, ui_update: dict | None = None) -> dict:
    result = {'type': 'tool_result', 'tool_name': tool_name, 'data': data}
    if ui_update:
        result['ui_update'] = ui_update
    return result


def agent_response(text: str) -> dict:
    return {'type': 'agent_response', 'text': text}


def agent_error(message: str) -> dict:
    return {'type': 'error', 'message': message}


def agent_done() -> dict:
    return {'type': 'done'}


# ── UI update payloads ──

def ui_aoi(lon: float, lat: float, label: str = '',
           geojson_url: str = '', bbox: list | None = None) -> dict:
    result = {'type': 'aoi', 'lon': lon, 'lat': lat, 'label': label,
              'geojson_url': geojson_url}
    if bbox:
        result['bbox'] = bbox
    return result


def ui_slc_result(total: int, stacks: list[dict], footprints: list[dict] | None = None,
                  coverage_url: str = '') -> dict:
    result = {'type': 'slc_result', 'total': total, 'stacks': stacks,
              'coverage_url': coverage_url}
    if footprints:
        result['footprints'] = footprints
    return result


def ui_job_submitted(estimated_cost: int, total_pairs: int, job_file: str = '') -> dict:
    return {'type': 'job_submitted',
            'estimated_cost': estimated_cost, 'total_pairs': total_pairs,
            'job_file': job_file}


def ui_pipeline_done(clipped_dir: str = '', task_id: str = '') -> dict:
    result = {'type': 'pipeline_done', 'clipped_data_dir': clipped_dir}
    if task_id:
        result['task_id'] = task_id
    return result


def ui_mintpy_config(config_path: str = '', data_dir: str = '',
                     config_content: str = '') -> dict:
    return {'type': 'mintpy_config',
            'config_path': config_path, 'data_dir': data_dir,
            'config_content': config_content}


def ui_catalog_results(total: int, matches: list[dict],
                       overlays: list[dict] | None = None) -> dict:
    result = {'type': 'catalog_results', 'total': total, 'matches': matches}
    if overlays:
        result['overlays'] = overlays
    return result


def ui_pipeline_progress(step: str, status: str, text: str = '') -> dict:
    return {'type': 'pipeline_progress', 'step': step, 'status': status, 'text': text}


def ui_view_result(entry_id: str, entry_name: str, path: int, frame: int,
                   date_start: str = '', date_end: str = '',
                   cum_url: str = '', velocity_url: str = '',
                   timeseries_url: str = '', ts_path: str = '',
                   vel_path: str = '') -> dict:
    result = {'type': 'view_result',
              'entry_id': entry_id, 'entry_name': entry_name,
              'path': path, 'frame': frame,
              'date_start': date_start, 'date_end': date_end}
    if cum_url:
        result['cum_url'] = cum_url
    if velocity_url:
        result['velocity_url'] = velocity_url
    if timeseries_url:
        result['timeseries_url'] = timeseries_url
    if ts_path:
        result['ts_path'] = ts_path
    if vel_path:
        result['vel_path'] = vel_path
    return result
