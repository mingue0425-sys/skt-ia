"""Content-addressed JSON only; no pickle, imports, or executable deserialization."""
import json
import platform
import sys
from pathlib import Path
from importlib.metadata import version
from ..storage import canonical, sha256, code_version, write_json
from ..http import utc_now
from . import VERSION


def environment():
    return {'python': sys.version, 'platform': platform.platform(), 'machine': platform.machine(),
            'dependencies': {n: version(n) for n in ('exchange_calendars', 'numpy', 'pandas', 'scikit-learn', 'scipy', 'threadpoolctl')}}


def save_model(root, pipeline, provenance, project_root, started_at):
    content = {'producer': 'market_research.training', 'version': VERSION, 'pipeline': pipeline,
               'provenance': provenance, 'code_sha256': code_version(project_root), 'environment': environment(),
               'pipeline_sha256': sha256(canonical(pipeline))}
    model_id = sha256(canonical(content))
    meta = {'created_at': utc_now(), 'training_started_at': started_at, 'training_completed_at': utc_now(),
            'clock_kind': 'actual_offline_execution; training_asof is a separate logical cutoff'}
    envelope = {'model_id': model_id, 'content': content, 'execution_metadata': meta,
                'execution_metadata_sha256': sha256(canonical(meta))}
    path = Path(root)/f'{model_id}.json'
    if path.exists(): load_model(path, root, model_id, provenance['mode'], project_root)
    else: write_json(path, envelope)
    return {'model_id': model_id, 'path': str(path), 'pipeline_sha256': content['pipeline_sha256']}


def load_model(path, artifact_root, expected_id, mode, project_root):
    path, root = Path(path).resolve(), Path(artifact_root).resolve()
    if not path.is_relative_to(root) or path.name != expected_id + '.json': raise ValueError('artifact_origin_root_or_id_mismatch')
    if path.stat().st_size > 5_000_000: raise ValueError('artifact_size_limit')
    a = json.loads(path.read_text())
    c = a['content']
    if a['model_id'] != expected_id or sha256(canonical(c)) != expected_id or sha256(canonical(c['pipeline'])) != c['pipeline_sha256'] or sha256(canonical(a['execution_metadata'])) != a['execution_metadata_sha256']:
        raise ValueError('model_artifact_hash_mismatch')
    if c['producer'] != 'market_research.training' or c['version'] != VERSION: raise ValueError('artifact_producer_version_mismatch')
    if c['provenance']['mode'] != mode: raise ValueError('model_mode_promotion_forbidden')
    if c['environment'] != environment(): raise ValueError('model_environment_mismatch')
    if c['code_sha256'] != code_version(project_root): raise ValueError('model_code_mismatch_use_original_source')
    return a
