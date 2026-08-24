"""JSON 预览:小 dict 圆整 keys;超 64KB 截断;坏 JSON 诚实 unsupported。"""

from __future__ import annotations

import json

from insar_agent.preview.json_view import _MAX_READ, preview_json


def test_small_dict_json_round_trips_qa_and_gnss_keys(tmp_path):
    qa = {
        "simulated": False,
        "method": "coherence_mask",
        "velocity_coverage": 0.91,
        "nan_fraction": 0.09,
        "vel_p2": -12.3,
        "vel_p98": 8.1,
    }
    qa_path = tmp_path / "qa.json"
    qa_path.write_text(json.dumps(qa, indent=1, ensure_ascii=False), encoding="utf-8")
    result = preview_json(qa_path)
    assert result.kind == "json"
    assert result.truncated is False
    assert result.payload["data"] == qa
    assert result.payload["keys"] == list(qa)
    assert json.loads(result.payload["text"]) == qa

    gnss = {
        "method": "gnss_compare",
        "simulated": False,
        "gnss_rmse_mm": 4.2,
        "n_stations": 5,
        "n_skipped": 0,
        "status": "ok",
        "gnss_csv": "stations.csv",
    }
    gnss_path = tmp_path / "gnss.json"
    gnss_path.write_text(json.dumps(gnss, indent=1, ensure_ascii=False), encoding="utf-8")
    g = preview_json(gnss_path)
    assert g.kind == "json"
    assert g.payload["keys"] == list(gnss)
    assert g.payload["data"] == gnss
    assert json.loads(g.payload["text"]) == gnss


def test_oversized_file_truncated(tmp_path):
    ok = tmp_path / "padded.json"
    ok.write_text('{"ok": true}' + (" " * _MAX_READ), encoding="utf-8")
    padded = preview_json(ok)
    assert padded.truncated is True
    assert padded.kind == "json"
    assert padded.payload["data"] == {"ok": True}
    assert padded.payload["keys"] == ["ok"]

    broken = tmp_path / "huge.json"
    broken.write_bytes(b'{"pad":"' + (b"n" * (_MAX_READ + 2048)) + b'"}')
    result = preview_json(broken)
    assert result.truncated is True
    assert result.kind == "unsupported"
    assert result.note == "文件过大且截断后不是合法 JSON"
    assert result.payload == {"reason": "invalid_json"}
    assert "keys" not in result.payload


def test_invalid_json_unsupported(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    result = preview_json(path)
    assert result.kind == "unsupported"
    assert result.truncated is False
    assert result.note == "JSONDecodeError"
    assert result.payload == {"reason": "invalid_json"}
    assert "keys" not in result.payload
    assert "data" not in result.payload
