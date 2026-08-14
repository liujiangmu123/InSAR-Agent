"""InSAR job submission tool - SBAS interferogram jobs to ASF HyP3"""
import os, json, statistics
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime
from math import floor, hypot
from typing import Optional

import asf_search as asf
import geopandas as gpd
from hyp3_sdk import HyP3

from ._aoi import make_aoi_wkt
from ..config import load_accounts, _PROJECTS_DIR

@dataclass
class SubmitResult:
    project_name: str
    path: int
    frame: int
    total_pairs: int
    success_count: int
    failed_count: int
    job_ids: list[str] = field(default_factory=list)
    failed_pairs: list[dict] = field(default_factory=list)
    job_file: str = ''
    task_id: str = ''
    account_used: str = ''
    credits_used: int = 0
    estimated_cost: int = 0
    warnings: list[str] = field(default_factory=list)

def discover_stacks(vector=None, lon=None, lat=None, start='2021-01-01', end='2021-12-31',
                    flight_dir=None, polarization=None, area_ratio_threshold=0.95,
                    location_deviation_km=5.0, half_span=0.25, output_dir=None) -> dict:
    if vector and os.path.isfile(vector):
        wkt = gpd.read_file(vector).geometry.union_all().wkt
    elif lon is not None and lat is not None:
        wkt = make_aoi_wkt(lon, lat, half_span)
    else:
        wkt = 'POLYGON((-180 -90, 180 -90, 180 90, -180 90, -180 -90))'

    results = asf.geo_search(platform=asf.PLATFORM.SENTINEL1, intersectsWith=wkt,
                              start=start, end=end, processingLevel=asf.PRODUCT_TYPE.SLC,
                              beamMode=asf.BEAMMODE.IW)
    if flight_dir:
        results = [r for r in results if r.properties.get('flightDirection') == flight_dir]
    if polarization:
        results = [r for r in results if polarization in r.properties.get('polarization', '')]
    if not results:
        return {'stacks': [], 'total_stacks': 0}

    scenes = []
    for r in results:
        pn = r.properties.get('pathNumber'); fn = r.properties.get('frameNumber')
        if pn is None or fn is None: continue
        scenes.append({'sceneName': r.properties['sceneName'],
                        'date': r.properties['startTime'][:10],
                        'pathNumber': pn, 'frameNumber': fn})
    scenes.sort(key=lambda x: x['date'])

    from shapely.geometry import shape
    gdf = gpd.GeoDataFrame({'geometry': [shape(r.geometry) for r in results]}, crs='EPSG:4326')
    cen_lon = gdf.geometry.centroid.x.median()
    utm_zone = int(floor((cen_lon + 180) / 6)) + 1
    utm_epsg = 32601 + utm_zone if cen_lon >= 0 else 32701 + utm_zone
    gdf_area = gdf.to_crs(epsg=utm_epsg)
    area_map = {}
    cx_map = {}
    cy_map = {}
    for i, r in enumerate(results):
        geom = gdf_area.iloc[i]['geometry']
        area_map[r.properties['sceneName']] = geom.area
        cx_map[r.properties['sceneName']] = geom.centroid.x
        cy_map[r.properties['sceneName']] = geom.centroid.y

    raw_groups = defaultdict(list)
    for s in scenes:
        raw_groups[(s['pathNumber'], s['frameNumber'])].append(s)

    valid_stacks = defaultdict(list)
    discard_details = []
    total_discarded = 0
    for (p, f), slist in sorted(raw_groups.items()):
        area_rounded = Counter(round(area_map[s['sceneName']] / 1e6, 0) for s in slist)
        ref_area = area_rounded.most_common(1)[0][0] * 1e6
        area_ok = []
        area_discard = 0; area_discard_dates = []
        for s in slist:
            ratio = area_map[s['sceneName']] / ref_area
            if ratio < area_ratio_threshold or ratio > 1 / area_ratio_threshold:
                area_discard += 1; area_discard_dates.append(s['date'])
            else:
                area_ok.append(s)
        loc_discard = 0; loc_discard_dates = []
        if area_ok:
            ref_cx = statistics.median(cx_map[s['sceneName']] for s in area_ok)
            ref_cy = statistics.median(cy_map[s['sceneName']] for s in area_ok)
            normal_list = []
            for s in area_ok:
                dx = cx_map[s['sceneName']] - ref_cx; dy = cy_map[s['sceneName']] - ref_cy
                if hypot(dx, dy) / 1000 > location_deviation_km:
                    loc_discard += 1; loc_discard_dates.append(s['date'])
                else:
                    normal_list.append(s)
        else:
            normal_list = []
        if normal_list:
            valid_stacks[(p, f)] = normal_list
        if area_discard or loc_discard:
            total_discarded += area_discard + loc_discard
            discard_details.append((p, f, area_discard, loc_discard, area_discard_dates, loc_discard_dates))

    stack_list = []
    for i, (key, slist) in enumerate(valid_stacks.items()):
        p, f = key
        dates = [s['date'] for s in slist]
        discarded = next((d for d in discard_details if d[0] == p and d[1] == f), None)
        stack_list.append({'index': i, 'path': p, 'frame': f, 'count': len(slist),
                            'start': dates[0], 'end': dates[-1],
                            'discarded_area': discarded[2] if discarded else 0,
                            'discarded_loc': discarded[3] if discarded else 0,
                            'discarded_area_dates': discarded[4] if discarded else [],
                            'discarded_loc_dates': discarded[5] if discarded else []})
    export_data = {'stacks': stack_list, 'total_stacks': len(stack_list), 'total_discarded': total_discarded}
    # Add footprint geometries
    try:
        import json as _json
        fp_list = []
        fp_seen = set()
        for r in results:
            pn = r.properties.get('pathNumber')
            fn = r.properties.get('frameNumber')
            if pn is None or fn is None: continue
            pn, fn = int(pn), int(fn)
            key = (pn, fn)
            if key in fp_seen: continue
            fp_seen.add(key)
            gj = _json.loads(gpd.GeoSeries([shape(r.geometry)], crs='EPSG:4326').to_json())
            flight = r.properties.get('flightDirection', '')
            fp_list.append({'path': pn, 'frame': fn, 'geojson': gj, 'flight_direction': flight})
        export_data['footprints'] = fp_list
    except Exception:
        pass
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, 'stacks.json'), 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
    return export_data


def submit_insar_jobs(path, frame, project_name='', max_neighbors=2, insar_opts=None,
                       accounts=None, account_username='', vector=None, lon=None, lat=None, start='2021-01-01',
                        end='2021-12-31', flight_dir=None, polarization=None,
                        area_ratio_threshold=0.95, location_deviation_km=5.0,
                        half_span=0.25, output_dir=None, confirm=True,
                        scene_names=None, stop_event=None) -> SubmitResult:
    """Submit SBAS InSAR jobs for a specific path/frame stack to ASF HyP3.
    
    If scene_names is provided, skips ASF search and burst check entirely,
    using the given scene names directly for pairing.
    """
    warnings = []
    _prefix = f'[DEBUG submit p{path}f{frame}]'
    if insar_opts is None:
        insar_opts = {'looks': '20x4', 'include_inc_map': True, 'include_look_vectors': True,
                        'include_dem': True, 'include_displacement_maps': True,
                        'include_wrapped_phase': True, 'apply_water_mask': True}

    # Fast path: user provided specific scene names → skip ASF search + burst check
    if scene_names and len(scene_names) >= 2:
        final = [{'sceneName': name, 'date': name[17:25] if len(name) > 25 else name[:10]} for name in scene_names]
        final.sort(key=lambda s: s['date'])
        print(f'{_prefix} using provided scene_names ({len(scene_names)}): {[s["date"] for s in final]}', flush=True)
        # Generate pairs
        pairs = []
        for i in range(len(final)):
            for j in range(i + 1, min(i + 1 + max_neighbors, len(final))):
                pairs.append((final[i]['sceneName'], final[i]['date'],
                               final[j]['sceneName'], final[j]['date']))
        if not pairs:
            return SubmitResult(project_name=project_name, path=path, frame=frame,
                                 total_pairs=0, success_count=0, failed_count=0,
                                 warnings=[f'Only {len(final)} scenes provided, need at least 2'])
        # Submit
        est_cost = len(pairs) * (15 if insar_opts.get('looks') == '10x2' else 10)
        if accounts is None:
            accounts = []
            u0, p0 = os.environ.get('HYP3_USERNAME', ''), os.environ.get('HYP3_PASSWORD', '')
            if u0 and p0: accounts.append({'username': u0, 'password': p0})
            for i in range(1, 10):
                u, p = os.environ.get(f'HYP3_USERNAME_{i}'), os.environ.get(f'HYP3_PASSWORD_{i}')
                if u and p: accounts.append({'username': u, 'password': p})
        if not accounts:
            return SubmitResult(project_name=project_name, path=path, frame=frame,
                                 total_pairs=len(pairs), success_count=0, failed_count=0,
                                 estimated_cost=est_cost,
                                 warnings=['No accounts configured'])
        hyp3, selected_user = None, ''
        if account_username and accounts:
            for acc in accounts:
                if acc['username'] == account_username:
                    try:
                        h = HyP3(username=acc['username'], password=acc['password'])
                        if h.check_credits() >= est_cost:
                            hyp3, selected_user = h, acc['username']
                        else:
                            return SubmitResult(project_name=project_name, path=path, frame=frame,
                                                total_pairs=len(pairs), success_count=0, failed_count=0,
                                                estimated_cost=est_cost,
                                                warnings=[f'Account {account_username} has only {h.check_credits()} credits, need {est_cost}.'])
                    except Exception as e:
                        return SubmitResult(project_name=project_name, path=path, frame=frame,
                                            total_pairs=len(pairs), success_count=0, failed_count=0,
                                            estimated_cost=est_cost,
                                            warnings=[f'Account {account_username} login failed: {e}'])
                    break
            if not selected_user:
                return SubmitResult(project_name=project_name, path=path, frame=frame,
                                    total_pairs=len(pairs), success_count=0, failed_count=0,
                                    estimated_cost=est_cost,
                                    warnings=[f'Account {account_username} not found in saved credentials.'])
        else:
            for acc in accounts:
                try:
                    h = HyP3(username=acc['username'], password=acc['password'])
                    if h.check_credits() >= est_cost: hyp3, selected_user = h, acc['username']; break
                except Exception: continue
        if hyp3 is None:
            return SubmitResult(project_name=project_name, path=path, frame=frame,
                                 total_pairs=len(pairs), success_count=0, failed_count=0,
                                 estimated_cost=est_cost,
                                 warnings=['No account with sufficient credits'])
        task_name = f'{project_name}_{selected_user}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        task_dir = os.path.join(str(_PROJECTS_DIR), task_name)
        os.makedirs(task_dir, exist_ok=True)
        job_file = os.path.join(task_dir, f'{task_name}.txt')
        succeeded, failed_pairs = [], []
        cancelled = False
        for idx2, (ref_s, ref_d, sec_s, sec_d) in enumerate(pairs):
            if stop_event and stop_event.is_set():
                warnings.append('Submission cancelled by user')
                cancelled = True
                break
            try:
                batch = hyp3.submit_insar_job(granule1=ref_s, granule2=sec_s, name=project_name, **insar_opts)
                jids = [j.job_id for j in batch.jobs] if batch.jobs else []
                succeeded.extend(jids)
            except Exception as e:
                failed_pairs.append({'ref_date': ref_d, 'sec_date': sec_d, 'error': str(e)})
        with open(job_file, 'w') as f:
            for jid in succeeded: f.write(jid + '\n')
        with open(job_file + '.meta', 'w') as f:
            f.write(selected_user + '\n' + acc['password'] + '\n')
        if cancelled:
            remaining = [[rs, ss] for rs, _, ss, _ in pairs[idx2:]]
            remaining_file = job_file.replace('.txt', '_remaining.json')
            with open(remaining_file, 'w') as f2:
                json.dump(remaining, f2)
            print(f'{_prefix} saved {len(remaining)} remaining pairs to {remaining_file}', flush=True)
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=len(pairs), success_count=len(succeeded),
                             failed_count=len(failed_pairs), job_ids=succeeded,
                             failed_pairs=failed_pairs, job_file=job_file, task_id=task_name,
                             account_used=selected_user, estimated_cost=est_cost,
                             credits_used=len(succeeded) * (15 if insar_opts.get('looks') == '10x2' else 10),
                             warnings=warnings)

    # ── Normal path: ASF search + burst integrity check ──
    # Use relativeOrbit+frame for direct lookup when no AOI specified
    search_kwargs = {
        'platform': asf.PLATFORM.SENTINEL1,
        'start': start, 'end': end,
        'processingLevel': asf.PRODUCT_TYPE.SLC,
        'beamMode': asf.BEAMMODE.IW,
        'relativeOrbit': path,
        'frame': frame,
    }
    if vector and os.path.isfile(vector):
        search_kwargs['intersectsWith'] = gpd.read_file(vector).geometry.union_all().wkt
        print(f'{_prefix} AOI from vector: {vector}', flush=True)
    elif lon is not None and lat is not None:
        search_kwargs['intersectsWith'] = make_aoi_wkt(lon, lat, half_span)
        print(f'{_prefix} AOI from lon/lat: {lon},{lat} span={half_span}', flush=True)
    else:
        print(f'{_prefix} using relativeOrbit={path} frame={frame}, skipping AOI', flush=True)

    results = asf.geo_search(**search_kwargs)
    print(f'{_prefix} ASF raw={len(results)}', flush=True)
    if flight_dir:
        results = [r for r in results if r.properties.get('flightDirection') == flight_dir]
    if polarization:
        results = [r for r in results if polarization in r.properties.get('polarization', '')]
    print(f'{_prefix} after fd/pol filter={len(results)}', flush=True)
    if not results:
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=0, success_count=0, failed_count=0,
                             warnings=['No SLC data found'])

    scenes = []
    for r in results:
        sc = {'sceneName': r.properties['sceneName'], 'date': r.properties['startTime'][:10],
               'pathNumber': r.properties.get('pathNumber'), 'frameNumber': r.properties.get('frameNumber')}
        if sc['pathNumber'] is None or sc['frameNumber'] is None: continue
        scenes.append(sc)
    scenes.sort(key=lambda x: x['date'])

    from shapely.geometry import shape
    gdf = gpd.GeoDataFrame({'geometry': [shape(r.geometry) for r in results if r.properties.get('pathNumber') is not None]},
                           crs='EPSG:4326')
    cen_lon = gdf.geometry.centroid.x.median()
    utm_zone = int(floor((cen_lon + 180) / 6)) + 1
    utm_epsg = 32601 + utm_zone if cen_lon >= 0 else 32701 + utm_zone
    gdf_area = gdf.to_crs(epsg=utm_epsg)
    area_map = {}
    cx_map = {}
    cy_map = {}
    for i, r in enumerate([rr for rr in results if rr.properties.get('pathNumber') is not None]):
        geom = gdf_area.iloc[i]['geometry']
        area_map[r.properties['sceneName']] = geom.area
        cx_map[r.properties['sceneName']] = geom.centroid.x
        cy_map[r.properties['sceneName']] = geom.centroid.y

    target = [s for s in scenes if s['pathNumber'] == path and s['frameNumber'] == frame]
    # count all path/frame combos found
    _pf_counts = Counter((s['pathNumber'], s['frameNumber']) for s in scenes)
    print(f'{_prefix} path/frame matches: {dict(_pf_counts)}', flush=True)
    print(f'{_prefix} target p{path}f{frame} has {len(target)} scenes: {[s["date"] for s in target]}', flush=True)
    if not target:
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=0, success_count=0, failed_count=0,
                             warnings=[f'No scenes found for Path {path} Frame {frame}'])

    area_rounded = Counter(round(area_map[s['sceneName']] / 1e6, 0) for s in target)
    ref_area = area_rounded.most_common(1)[0][0] * 1e6
    filtered = []
    for s in target:
        ratio = area_map[s['sceneName']] / ref_area
        if ratio < area_ratio_threshold or ratio > 1 / area_ratio_threshold:
            continue
        filtered.append(s)
    if filtered:
        ref_cx = statistics.median(cx_map[s['sceneName']] for s in filtered)
        ref_cy = statistics.median(cy_map[s['sceneName']] for s in filtered)
        final = []
        for s in filtered:
            dx = cx_map[s['sceneName']] - ref_cx
            dy = cy_map[s['sceneName']] - ref_cy
            if hypot(dx, dy) / 1000 > location_deviation_km:
                continue
            final.append(s)
    else:
        final = []

    print(f'{_prefix} after burst check: area_filter={len(filtered)}/{len(target)} loc_filter={len(final) if filtered else 0}', flush=True)
    if not final:
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=0, success_count=0, failed_count=0,
                             warnings=[f'No valid scenes after burst integrity check for Path {path} Frame {frame}'])

    if not project_name:
        project_name = f'Path{path}_Frame{frame}'

    pairs = []
    for i in range(len(final)):
        for j in range(i + 1, min(i + 1 + max_neighbors, len(final))):
            pairs.append((final[i]['sceneName'], final[i]['date'],
                           final[j]['sceneName'], final[j]['date']))

    if not pairs:
        _dates = [s['date'] for s in final]
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=0, success_count=0, failed_count=0,
                             warnings=[f'Only {len(final)} valid scene(s) after burst check for Path {path} Frame {frame}. Need at least 2. Dates: {_dates}'])

    if not confirm:
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=len(pairs), success_count=0, failed_count=0,
                             warnings=['Confirmation required but confirm=False'])

    if accounts is None:
        accounts = []
        u0 = os.environ.get('HYP3_USERNAME', '')
        p0 = os.environ.get('HYP3_PASSWORD', '')
        if u0 and p0:
            accounts.append({'username': u0, 'password': p0})
        for i in range(1, 10):
            u = os.environ.get(f'HYP3_USERNAME_{i}')
            p = os.environ.get(f'HYP3_PASSWORD_{i}')
            if u and p:
                accounts.append({'username': u, 'password': p})
    if not accounts:
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=len(pairs), success_count=0, failed_count=0,
                             warnings=['No accounts configured'])

    cost_per_pair = 15 if insar_opts.get('looks') == '10x2' else 10
    est_cost = len(pairs) * cost_per_pair
    hyp3 = None
    selected_user = ''
    for acc in accounts:
        try:
            h = HyP3(username=acc['username'], password=acc['password'])
            creds = h.check_credits()
            if creds >= est_cost:
                hyp3 = h
                selected_user = acc['username']
                break
        except Exception:
            continue
    if hyp3 is None:
        return SubmitResult(project_name=project_name, path=path, frame=frame,
                             total_pairs=len(pairs), success_count=0, failed_count=0,
                             estimated_cost=est_cost,
                             warnings=['No account with sufficient credits'])

    task_name = f'{project_name}_{selected_user}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    task_dir = os.path.join(str(_PROJECTS_DIR), task_name)
    os.makedirs(task_dir, exist_ok=True)
    job_file = os.path.join(task_dir, f'{task_name}.txt')

    succeeded = []
    failed_pairs = []
    cancelled = False
    for idx, (ref_s, ref_d, sec_s, sec_d) in enumerate(pairs):
        if stop_event and stop_event.is_set():
            warnings.append('Submission cancelled by user')
            cancelled = True
            break
        try:
            batch = hyp3.submit_insar_job(granule1=ref_s, granule2=sec_s, name=project_name, **insar_opts)
            jids = [j.job_id for j in batch.jobs] if batch.jobs else []
            succeeded.extend(jids)
        except Exception as e:
            failed_pairs.append({'ref_date': ref_d, 'sec_date': sec_d, 'error': str(e)})

    with open(job_file, 'w') as f:
        for jid in succeeded:
            f.write(jid + '\n')
    with open(job_file + '.meta', 'w') as f:
        f.write(selected_user + '\n' + acc['password'] + '\n')

    if cancelled:
        remaining = [[rs, ss] for rs, _, ss, _ in pairs[idx:]]
        remaining_file = job_file.replace('.txt', '_remaining.json')
        with open(remaining_file, 'w') as f:
            json.dump(remaining, f)
        print(f'{_prefix} saved {len(remaining)} remaining pairs to {remaining_file}', flush=True)

    return SubmitResult(project_name=project_name, path=path, frame=frame,
                         total_pairs=len(pairs), success_count=len(succeeded),
                         failed_count=len(failed_pairs), job_ids=succeeded,
                         failed_pairs=failed_pairs, job_file=job_file, task_id=task_name,
                         account_used=selected_user, estimated_cost=est_cost,
                         credits_used=len(succeeded) * cost_per_pair, warnings=warnings)
