/* ============================================================
   注册表夹具 —— GET /api/registry（无 session 探测）载荷的快照。
   由服务端能力声明（src/insar_agent/registry/capabilities.py）生成,
   与 api/app.py registry() 端点逐字段一致（probe=None 分支）。

   用途:前端测试/校验脚本喂 state.setRegistry(REGISTRY),
   在 Node 里复现「注册表水合后的 STEP_DEFS」——这是真实服务端
   目录的快照,不是手写演示数据。registry 声明变更后按下方命令重生成。

   重生成（项目根目录,内容替换 REGISTRY 常量）:
     python - <<'PY'
     import json, sys; from pathlib import Path
     sys.path.insert(0, str(Path('src').resolve()))
     from insar_agent.registry.capabilities import PIPELINE
     out = [{'id': c.id, 'name': c.name, 'deps': list(c.deps), 'method': c.default_method,
             'methods': [{'id': m.id, 'label': m.label, 'engine': m.engine, 'why': m.why,
                          'recommend': m.recommend, 'extra': m.extra,
                          'ok': True, 'simulated': False, 'blocked': ''} for m in c.methods],
             'params': {k: {'default': p.default, 'kind': p.kind, 'type': p.type,
                            'min': p.min, 'max': p.max, 'hint': p.hint} for k, p in c.params.items()},
             'outputs': [{'path': a.candidates[0], 'kind': a.kind, 'layout': a.layout} for a in c.artifacts],
             'replay': c.replay, 'timeouts': {'idle': c.timeouts.idle, 'total': c.timeouts.total}}
            for c in PIPELINE]
     print(json.dumps(out, ensure_ascii=False, indent=2))
     PY
   ============================================================ */
export const REGISTRY =
[
  {
    "id": 1,
    "name": "鏁版嵁鑾峰彇",
    "deps": [],
    "method": "local_import",
    "methods": [
      {
        "id": "asf_search_slc",
        "label": "asf_search_slc",
        "engine": "asf_api",
        "why": "涓嬭浇鍘熷 SLC,鍙帶鎬ф渶寮?,
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "hyp3_submit",
        "label": "hyp3_submit",
        "engine": "hyp3",
        "why": "浜戠澶勭悊,璺宠繃 2-6 姝?澶卞幓涓棿浜х墿鎺у埗鏉?,
        "recommend": false,
        "extra": "闇€ ASF 閰嶉",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "local_import",
        "label": "local_import",
        "engine": "-",
        "why": "宸叉湁鏈湴鏁版嵁",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "scenes": {
        "default": 7,
        "kind": "science",
        "type": "int",
        "min": 2,
        "max": 200,
        "hint": "鏅暟 2-200"
      },
      "platform": {
        "default": "sentinel-1",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "dates": {
        "default": "2019-06-10..2019-08-15",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "source": {
        "default": "",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "local_import 鐨勬暟鎹簮鐩綍(HyP3 浜у搧鐩綍鎴?SLC 鐩綍)"
      }
    },
    "outputs": [
      {
        "path": "data/slc",
        "kind": "SLC",
        "layout": "isce2"
      },
      {
        "path": "hyp3",
        "kind": "IFG_UNWRAPPED",
        "layout": "hyp3"
      },
      {
        "path": "mintpy/inputs/ERA5.h5",
        "kind": "CONFIG",
        "layout": ""
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 1800,
      "total": 21600
    }
  },
  {
    "id": 2,
    "name": "杈呭姪鏁版嵁",
    "deps": [
      1
    ],
    "method": "dem_copernicus",
    "methods": [
      {
        "id": "dem_copernicus",
        "label": "dem_copernicus",
        "engine": "dem_service",
        "why": "Copernicus 30 m,瑕嗙洊鍏ㄧ悆涓旇川閲忕ǔ瀹?,
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "dem_srtm",
        "label": "dem_srtm",
        "engine": "dem_service",
        "why": "SRTM 30 m,楂樼含搴﹁鐩栫己鍙?,
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "dem_local",
        "label": "dem_local",
        "engine": "-",
        "why": "浣跨敤鏈湴 DEM 鐡︾墖",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "dem": {
        "default": "copernicus-30m",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "orbit": {
        "default": "poeorb",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/dem",
        "kind": "DEM",
        "layout": "isce2"
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 600,
      "total": 3600
    }
  },
  {
    "id": 3,
    "name": "閰嶅噯",
    "deps": [
      1,
      2
    ],
    "method": "isce2_tops_geom_esd",
    "methods": [
      {
        "id": "isce2_tops_geom_esd",
        "label": "isce2_tops_geom_esd",
        "engine": "isce2",
        "why": "S1 IW 鏍囧噯璺緞:鍑犱綍閰嶅噯 + ESD 绮惧寲",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_xcorr",
        "label": "isce2_stripmap_xcorr",
        "engine": "isce2",
        "why": "鏉″甫妯″紡(ALOS raw,2026-08 WSL 瀹炴祴鍏ㄩ摼閫氳繃)",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "snap_backgeocoding",
        "label": "snap_backgeocoding",
        "engine": "snap",
        "why": "璧?SNAP 閾?闇€鎹?layout",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "esd_coherence_threshold": {
        "default": 0.85,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "ESD 鐩稿共闃堝€?0-1"
      },
      "reference_image": {
        "default": "data/raw/reference/IMG-HH",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:鍙傝€?raw 褰卞儚 IMG 鐩稿璺緞"
      },
      "reference_leader": {
        "default": "data/raw/reference/LED",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:鍙傝€冨奖鍍?LED 澶存枃浠剁浉瀵硅矾寰?
      },
      "secondary_image": {
        "default": "data/raw/secondary/IMG-HH",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:浠?raw 褰卞儚 IMG 鐩稿璺緞"
      },
      "secondary_leader": {
        "default": "data/raw/secondary/LED",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:浠庡奖鍍?LED 澶存枃浠剁浉瀵硅矾寰?
      },
      "resample_flag": {
        "default": "",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:FBD 浠庡奖鍍忛厤 FBS 涓诲奖鍍忔椂鐢?dual2single,绌?涓嶉噸閲囨牱"
      },
      "dem_path": {
        "default": "data/dem/dem.wgs84",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:ISCE 鏍煎紡 DEM 鐩稿璺緞"
      },
      "threads": {
        "default": 8,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 32,
        "hint": "绾跨▼鏁?1-32(鏈満 24 鏍?鐣?4 鏍哥粰绯荤粺)"
      }
    },
    "outputs": [
      {
        "path": "data/coreg",
        "kind": "RSLC",
        "layout": "isce2"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 14400
    }
  },
  {
    "id": 4,
    "name": "骞叉秹",
    "deps": [
      3
    ],
    "method": "isce2_ifg_multilook",
    "methods": [
      {
        "id": "isce2_ifg_multilook",
        "label": "isce2_ifg_multilook",
        "engine": "isce2",
        "why": "鍙皟澶氳姣斻€傚皬鍩虹嚎缃戠粶鎸夋椂绌哄熀绾垮壀鏋?闈炲叏缁勫悎",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "snap_interferogram",
        "label": "snap_interferogram",
        "engine": "snap",
        "why": "SNAP 閾惧搴旀楠?,
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_ifg",
        "label": "isce2_stripmap_ifg",
        "engine": "isce2",
        "why": "鏉″甫閾惧共娑?stripmapApp 鍒嗛璋扁啋骞叉秹鈫掓护娉㈡(ALOS raw)",
        "recommend": false,
        "extra": "瀹炴祴(ALOS Baja):闃舵1 startup鈫抐ilter 绾?20 min;鍏ㄩ摼 33 min/32 GB",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "range_looks": {
        "default": 10,
        "kind": "science",
        "type": "int",
        "min": 1,
        "max": 40,
        "hint": "璺濈鍚戝瑙?1-40"
      },
      "azimuth_looks": {
        "default": 2,
        "kind": "science",
        "type": "int",
        "min": 1,
        "max": 40,
        "hint": "鏂逛綅鍚戝瑙?1-40"
      },
      "pairs": {
        "default": 11,
        "kind": "science",
        "type": "int",
        "min": 1,
        "max": 5000,
        "hint": ""
      },
      "threads": {
        "default": 8,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 32,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/ifg",
        "kind": "IFG_WRAPPED",
        "layout": "isce2"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 3600,
      "total": 28800
    }
  },
  {
    "id": 5,
    "name": "婊ゆ尝",
    "deps": [
      4
    ],
    "method": "goldstein",
    "methods": [
      {
        "id": "goldstein",
        "label": "goldstein",
        "engine": "isce2",
        "why": "浣庣浉骞插尯鎺ㄨ崘",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "boxcar",
        "label": "boxcar",
        "engine": "isce2",
        "why": "绠€鍗曞揩閫?浣嗚竟缂樻ā绯?,
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "none",
        "label": "none",
        "engine": "-",
        "why": "涓嶆护娉?淇濈暀鍏ㄩ儴缁嗚妭",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_filter",
        "label": "isce2_stripmap_filter",
        "engine": "isce2",
        "why": "鏉″甫閾炬护娉?stripmapApp filter 鍗曟(ALOS raw)",
        "recommend": false,
        "extra": "鍗曟閲嶈窇,鍒嗛挓绾?瀹炴祴鍏ㄩ摼 33 min 鍐呭崰姣斿緢灏?",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "alpha": {
        "default": 0.4,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "Goldstein alpha 0-1"
      },
      "filter_strength": {
        "default": 0.5,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/ifg_filt",
        "kind": "IFG_WRAPPED",
        "layout": "isce2"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 14400
    }
  },
  {
    "id": 6,
    "name": "瑙ｇ紶",
    "deps": [
      5
    ],
    "method": "snaphu_mcf",
    "methods": [
      {
        "id": "snaphu_mcf",
        "label": "snaphu_mcf",
        "engine": "snaphu",
        "why": "Minimum Cost Flow銆備綆鐩稿共鍖虹ǔ鍋?MintPy 鍘熺敓鍏煎",
        "recommend": true,
        "extra": "鑰楁椂/鍐呭瓨寰呭疄娴?,
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "snaphu_smooth",
        "label": "snaphu_smooth",
        "engine": "snaphu",
        "why": "绮惧害鏇撮珮浣嗛渶浜哄伐璋?cost function",
        "recommend": false,
        "extra": "闇€浜や簰閰嶇疆",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "icu",
        "label": "icu",
        "engine": "isce2",
        "why": "鍖哄煙澧為暱娉?澶ц寖鍥翠綆鐩稿共鍖烘槗浜х敓瑙ｇ紶瀛ゅ矝",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "3D_FULL",
        "label": "3D_FULL",
        "engine": "unw3d",
        "why": "闇€ 3D 鐩镐綅瑙ｇ紶宸ュ叿閾?杈撳嚭鏍煎紡涓庝笅娓镐笉鍏煎",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_unwrap_snaphu",
        "label": "isce2_stripmap_unwrap_snaphu",
        "engine": "isce2",
        "why": "鏉″甫閾捐В缂?stripmapApp 鍐呯疆 snaphu + 鍦扮悊缂栫爜(ALOS raw)",
        "recommend": false,
        "extra": "瀹炴祴 filter_low_band鈫抔eocode 绾?13 min(snaphu 绾?10 min)",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "min_coherence": {
        "default": 0.25,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "鐩稿共鎬ч槇鍊?0-1"
      },
      "cost_mode": {
        "default": "SMOOTH",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "threads": {
        "default": 8,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 32,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/unw",
        "kind": "IFG_UNWRAPPED",
        "layout": "isce2"
      },
      {
        "path": "params/unwrap.yaml",
        "kind": "CONFIG",
        "layout": ""
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 7200
    }
  },
  {
    "id": 7,
    "name": "鏃跺簭鍙嶆紨",
    "deps": [
      6
    ],
    "method": "mintpy_sbas",
    "methods": [
      {
        "id": "mintpy_sbas",
        "label": "mintpy_sbas",
        "engine": "mintpy",
        "why": "灏忓熀绾块泦,閫傚悎浣庣浉骞查潰鐘跺舰鍙樺尯",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "pystamps_ps",
        "label": "pystamps_ps",
        "engine": "pystamps",
        "why": "姘镐箙鏁ｅ皠浣?閫傚悎楂樼浉骞茬偣鐘剁洰鏍?闇€ ISCE2鈫扨yStamps 妗?,
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "network": {
        "default": "small_baseline",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "max_temporal_baseline": {
        "default": 120,
        "kind": "science",
        "type": "int",
        "min": 6,
        "max": 730,
        "hint": "鏃堕棿鍩虹嚎 6-730 澶?
      },
      "parallel_workers": {
        "default": 4,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 16,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "mintpy/timeseries.h5",
        "kind": "TIMESERIES",
        "layout": "mintpy_h5"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 86400
    }
  },
  {
    "id": 8,
    "name": "璇樊鏍℃",
    "deps": [
      7
    ],
    "method": "tropo_era5_pyaps",
    "methods": [
      {
        "id": "tropo_era5_pyaps",
        "label": "tropo_era5_pyaps",
        "engine": "pyaps",
        "why": "ERA5 澶ф皵鏍℃(闇€ CDS 鍑嵁鎴栧凡缂撳瓨鐨?ERA5.h5)",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "tropo_gacos",
        "label": "tropo_gacos",
        "engine": "gacos",
        "why": "GACOS 浜у搧,闇€鍦ㄧ嚎鐢宠",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "tropo_height_corr",
        "label": "tropo_height_corr",
        "engine": "mintpy",
        "why": "鏃犳皵璞℃暟鎹椂鐨勯檷绾ф柟妗?璇佹嵁绾у埆涓嬮檷)",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "ramp": {
        "default": "no",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "dem_error": {
        "default": true,
        "kind": "science",
        "type": "bool",
        "min": null,
        "max": null,
        "hint": ""
      },
      "solid_earth_tides": {
        "default": false,
        "kind": "science",
        "type": "bool",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "mintpy/timeseries_ERA5_ramp_demErr.h5",
        "kind": "TIMESERIES",
        "layout": "mintpy_h5"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 21600
    }
  },
  {
    "id": 9,
    "name": "褰㈠彉妯″瀷",
    "deps": [
      8
    ],
    "method": "linear",
    "methods": [
      {
        "id": "poly_periodic",
        "label": "poly_periodic(1,[1,0.5])",
        "engine": "mintpy",
        "why": "绾挎€?+ 骞村懆鏈?+ 鍗婂勾鍛ㄦ湡,鍖归厤瀛ｈ妭鍐昏瀺鏈虹悊",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "linear",
        "label": "linear",
        "engine": "mintpy",
        "why": "浠呯嚎鎬ц秼鍔?,
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "step",
        "label": "step(date)",
        "engine": "mintpy",
        "why": "鍚岄渿闃惰穬",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "exponential",
        "label": "exponential",
        "engine": "mintpy",
        "why": "闇囧悗/鐭垮尯琛板噺褰㈠彉",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "periods": {
        "default": [
          1,
          0.5
        ],
        "kind": "science",
        "type": "list",
        "min": null,
        "max": null,
        "hint": ""
      },
      "poly_order": {
        "default": 1,
        "kind": "science",
        "type": "int",
        "min": 0,
        "max": 3,
        "hint": ""
      },
      "step_date": {
        "default": "",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "mintpy/velocity.h5",
        "kind": "VELOCITY",
        "layout": "mintpy_h5"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 600,
      "total": 3600
    }
  },
  {
    "id": 10,
    "name": "鍑哄浘瀵煎嚭",
    "deps": [
      9
    ],
    "method": "figure_journal",
    "methods": [
      {
        "id": "figure_journal",
        "label": "figure_journal",
        "engine": "-",
        "why": "鏈熷垔绾ф帓鐗?600 dpi銆佽壊鐩插畨鍏ㄨ壊甯︺€佹瘮渚嬪昂",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "mintpy_geocode",
        "label": "mintpy_geocode",
        "engine": "mintpy",
        "why": "浠呭湴鐞嗙紪鐮?涓嶅仛鎺掔増",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "gdal_warp",
        "label": "gdal_warp",
        "engine": "gdal",
        "why": "瀵煎嚭 GeoTIFF 渚?GIS 浣跨敤",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "dpi": {
        "default": 600,
        "kind": "presentation",
        "type": "int",
        "min": 72,
        "max": 1200,
        "hint": "鍑哄浘 DPI 72-1200"
      },
      "cmap": {
        "default": "roma",
        "kind": "presentation",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "format": {
        "default": "png+pdf",
        "kind": "presentation",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "products/figures",
        "kind": "FIGURE",
        "layout": ""
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 600,
      "total": 1800
    }
  },
  {
    "id": 11,
    "name": "璐ㄦ",
    "deps": [
      10,
      7
    ],
    "method": "crossval_ps_sbas",
    "methods": [
      {
        "id": "crossval_ps_sbas",
        "label": "crossval_ps_sbas",
        "engine": "-",
        "why": "PS/SBAS 鍙岄摼浜ゅ弶楠岃瘉 鈥斺€?鏈」鐩嫭鏈夎川閲忛棬",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "loop_closure",
        "label": "loop_closure",
        "engine": "mintpy",
        "why": "闂悎鍥炶矾娈嬪樊妫€鏌?鍙獙瑙ｇ紶涓嶉獙鍙嶆紨",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "coherence_mask",
        "label": "coherence_mask",
        "engine": "mintpy",
        "why": "鐩稿共鎬ф帺鑶?鏈€寮辩殑璐ㄦ",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "corr_threshold": {
        "default": 0.85,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "浜ゅ弶楠岃瘉鐩稿叧闃堝€?0-1"
      }
    },
    "outputs": [
      {
        "path": "products/report/qa.json",
        "kind": "REPORT",
        "layout": ""
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 900,
      "total": 7200
    }
  }
];

/**
 * 步骤镜像种子:模拟服务端 /api/state 的步骤载荷（含权威 params）。
 * doneThrough 前的步骤记 done,其余 pending —— 测试用来复现
 * 「部分完成的计划」;真实运行里同样形状由服务端下发。
 * @param {object} St  已 import 的 state.js 模块（先 setRegistry 再调用）
 * @param {number} doneThrough  1..N 标 done 的最大步骤 id（0=全 pending）
 */
export function seedSteps(St, doneThrough = 0) {
  St.initSteps();
  St.syncServerSteps(St.STEP_DEFS.map((d) => ({
    id: d.id,
    state: d.id <= doneThrough ? 'done' : 'pending',
    method: d.method,
    params: { ...d.params },
    stale: false,
  })));
}
