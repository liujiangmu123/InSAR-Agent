"""6-step InSAR workflow engine - NL-driven pipeline with pause/resume"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

from .config import get_project_dir, get_output_dir, load_accounts, load_settings
from .tools.query_slc import query_slc, SlcQueryResult
from .tools.submit_insar import submit_insar_jobs, SubmitResult
from .tools.check_status import check_job_status, JobStatusResult
from .tools.download import download_products, DownloadResult
from .tools.unzip import unzip_products, UnzipResult
from .tools.clip import clip_to_common_overlap, ClipResult

STEPS = ['1_check_slc', '2_submit', '3_status', '4_download', '5_unzip', '6_clip']

STEP_LABELS = {
    '1_check_slc': 'Query SLC',
    '2_submit': 'Submit InSAR',
    '3_status': 'Check Status',
    '4_download': 'Download',
    '5_unzip': 'Unzip',
    '6_clip': 'Clip to Overlap',
}


@dataclass
class StepState:
    status: str = 'pending'
    started: str = ''
    finished: str = ''
    outputs: dict = field(default_factory=dict)
    error: str = ''


@dataclass
class WorkflowState:
    project_name: str
    created: str = ''
    updated: str = ''
    params: dict = field(default_factory=dict)
    steps: dict[str, StepState] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created:
            self.created = datetime.now().isoformat()
        if not self.steps:
            self.steps = {s: StepState() for s in STEPS}


class WorkflowRunner:
    """Orchestrate the 6-step InSAR pipeline with state persistence."""

    def __init__(self, project_name: str, callback: Optional[Callable[[str], None]] = None):
        self.project_name = project_name
        self.callback = callback or (lambda msg: None)
        self.state: Optional[WorkflowState] = None
        self._load_or_create()

    def _log(self, msg: str):
        self.callback(msg)

    def _load_or_create(self):
        state_file = get_project_dir(self.project_name) / 'state.json'
        if state_file.exists():
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            steps_data = data.get('steps', {})
            steps = {k: StepState(**v) for k, v in steps_data.items()}
            self.state = WorkflowState(
                project_name=data.get('project_name', self.project_name),
                created=data.get('created', ''),
                updated=data.get('updated', ''),
                params=data.get('params', {}),
                steps=steps,
            )
            self._log(f'Loaded project "{self.project_name}"')
        else:
            self.state = WorkflowState(project_name=self.project_name)
            self._save()
            self._log(f'Created project "{self.project_name}"')

    def _save(self):
        get_project_dir(self.project_name).mkdir(parents=True, exist_ok=True)
        data = {
            'project_name': self.state.project_name,
            'created': self.state.created,
            'updated': datetime.now().isoformat(),
            'params': self.state.params,
            'steps': {k: asdict(v) for k, v in self.state.steps.items()},
        }
        with open(get_project_dir(self.project_name) / 'state.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def set_params(self, **kwargs):
        self.state.params.update(kwargs)
        self._save()

    def run_step(self, step_key: str, force: bool = False) -> dict:
        if step_key not in STEPS:
            return {'status': 'error', 'error': f'Unknown step: {step_key}'}

        step = self.state.steps[step_key]
        if step.status == 'done' and not force:
            return {'status': 'skipped', 'reason': 'Already completed'}

        step.status = 'running'
        step.started = datetime.now().isoformat()
        step.error = ''
        self._save()

        handlers = {
            '1_check_slc': self._run_check_slc,
            '2_submit': self._run_submit,
            '3_status': self._run_status,
            '4_download': self._run_download,
            '5_unzip': self._run_unzip,
            '6_clip': self._run_clip,
        }

        try:
            result = handlers[step_key]()
            step.status = 'done'
            step.outputs = result
            step.finished = datetime.now().isoformat()
            self._save()
            self._log(f'Step {step_key} completed: {result}')
            return {'status': 'done', 'result': result}
        except Exception as e:
            step.status = 'failed'
            step.error = str(e)
            step.finished = datetime.now().isoformat()
            self._save()
            self._log(f'Step {step_key} failed: {e}')
            return {'status': 'failed', 'error': str(e)}

    def run_all(self, start_from: int = 0, force: bool = False) -> dict[str, dict]:
        results = {}
        for step_key in STEPS[start_from:]:
            results[step_key] = self.run_step(step_key, force=force)
            if results[step_key]['status'] == 'failed':
                break
        return results

    def _run_check_slc(self) -> dict:
        p = self.state.params
        result = query_slc(
            vector=p.get('vector'),
            lon=p.get('lon', 117.2),
            lat=p.get('lat', 35.6),
            start=p.get('start', '2017-01-01'),
            end=p.get('end', '2023-12-31'),
            flight_dir=p.get('flight_dir'),
            area_ratio_threshold=p.get('area_ratio', 0.95),
            location_deviation_km=p.get('loc_dev_km', 5.0),
            output_dir=str(get_output_dir(self.project_name)),
        )
        return {
            'total_scenes': result.total_scenes,
            'stacks': [asdict(s) for s in result.stacks],
            'coverage_image': result.coverage_image,
            'warnings': result.warnings,
        }

    def _run_submit(self) -> dict:
        p = self.state.params
        output_dir = str(get_output_dir(self.project_name))
        accounts = load_accounts()

        path = p.get('path')
        frame = p.get('frame')
        if path is None or frame is None:
            raise ValueError('Path and frame must be set in params before submitting')

        result = submit_insar_jobs(
            path=path, frame=frame,
            project_name=self.project_name,
            max_neighbors=p.get('max_neighbors', 2),
            insar_opts={
                'looks': p.get('looks', '20x4'),
                'include_inc_map': p.get('include_inc_map', True),
                'include_look_vectors': p.get('include_look_vectors', True),
                'include_dem': p.get('include_dem', True),
                'include_displacement_maps': p.get('include_disp', True),
                'include_wrapped_phase': p.get('include_wrapped', True),
                'apply_water_mask': p.get('apply_water_mask', True),
            },
            accounts=accounts if accounts else None,
            vector=p.get('vector'),
            start=p.get('start', '2021-01-01'),
            end=p.get('end', '2021-12-31'),
            flight_dir=p.get('flight_dir'),
            polarization=p.get('polarization'),
            area_ratio_threshold=p.get('area_ratio', 0.95),
            location_deviation_km=p.get('loc_dev_km', 5.0),
            output_dir=output_dir,
            confirm=True,
        )
        return {
            'project_name': result.project_name,
            'path': result.path, 'frame': result.frame,
            'total_pairs': result.total_pairs,
            'success_count': result.success_count,
            'failed_count': result.failed_count,
            'job_ids': result.job_ids,
            'job_file': result.job_file,
            'account_used': result.account_used,
            'estimated_cost': result.estimated_cost,
            'warnings': result.warnings,
        }

    def _run_status(self) -> dict:
        p = self.state.params
        job_file = self.state.steps['2_submit'].outputs.get('job_file', '')
        if not job_file or not os.path.isfile(job_file):
            raise ValueError('Job file not found. Run Step 2 first.')

        result = check_job_status(
            job_file=job_file,
            output_dir=str(get_output_dir(self.project_name)),
        )
        return {
            'total': result.total,
            'succeeded': result.succeeded,
            'running': result.running,
            'pending': result.pending,
            'failed': result.failed,
            'all_done': result.all_done,
            'url_file': result.url_file,
            'download_urls': result.download_urls,
            'failed_jobs': result.failed_jobs,
            'warnings': result.warnings,
        }

    def _run_download(self) -> dict:
        status_outputs = self.state.steps['3_status'].outputs
        url_file = status_outputs.get('url_file', '')
        if not url_file or not os.path.isfile(url_file):
            raise ValueError('URL file not found. Run Step 3 first.')

        settings = load_settings()
        accounts = load_accounts()
        if not accounts:
            raise ValueError('No accounts configured')

        save_dir = str(get_output_dir(self.project_name) / 'downloads')
        result = download_products(
            url_file=url_file,
            save_dir=save_dir,
            username=accounts[0]['username'],
            password=accounts[0]['password'],
            max_workers=settings.get('max_download_jobs', 4),
        )
        return {
            'save_dir': result.save_dir,
            'total_files': result.total_files,
            'success_count': result.success_count,
            'failed_count': result.failed_count,
            'failed_files': result.failed_files,
            'total_bytes': result.total_bytes,
            'elapsed_seconds': result.elapsed_seconds,
            'warnings': result.warnings,
        }

    def _run_unzip(self) -> dict:
        dl_outputs = self.state.steps['4_download'].outputs
        save_dir = dl_outputs.get('save_dir', '')
        if not save_dir or not os.path.isdir(save_dir):
            raise ValueError('Download directory not found. Run Step 4 first.')

        settings = load_settings()
        result = unzip_products(
            source_dir=save_dir,
            max_workers=settings.get('max_unzip_jobs', 4),
            delete_source=settings.get('delete_source_zip', True),
        )
        return {
            'source_dir': result.source_dir,
            'output_dir': result.output_dir,
            'total_files': result.total_files,
            'success_count': result.success_count,
            'failed_count': result.failed_count,
            'skipped_count': result.skipped_count,
            'failed_files': result.failed_files,
            'elapsed_seconds': result.elapsed_seconds,
            'warnings': result.warnings,
        }

    def _run_clip(self) -> dict:
        unzip_outputs = self.state.steps['5_unzip'].outputs
        data_dir = unzip_outputs.get('output_dir', unzip_outputs.get('source_dir', ''))
        if not data_dir or not os.path.isdir(data_dir):
            raise ValueError('Unzipped data directory not found. Run Step 5 first.')

        result = clip_to_common_overlap(
            data_dir=data_dir,
            exclude_dates=self.state.params.get('exclude_dates', []),
        )
        return {
            'data_dir': result.data_dir,
            'output_dir': result.output_dir,
            'total_files': result.total_files,
            'clipped_count': result.clipped_count,
            'skipped_count': result.skipped_count,
            'txt_copied': result.txt_copied,
            'overlap': result.overlap,
            'elapsed_seconds': result.elapsed_seconds,
            'mintpy_config': result.mintpy_config,
            'warnings': result.warnings,
        }

    def status(self) -> dict:
        return {
            'project_name': self.state.project_name,
            'created': self.state.created,
            'updated': self.state.updated,
            'params': self.state.params,
            'steps': {
                k: {'status': v.status, 'error': v.error}
                for k, v in self.state.steps.items()
            },
        }
