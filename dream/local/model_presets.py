"""Private exact-model launch presets, with atomic updates and conflict checks."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import glob
import json
import os
from pathlib import Path
import re
import tempfile
from .. import config


def model_key(path: Path) -> str:
    path = path.resolve()
    match = re.match(r'^(.*)-\d{5}-of-(\d{5})\.gguf$', path.name)
    parts = sorted(path.parent.glob(glob.escape(match[1])+'-?????-of-'+match[2]+'.gguf')) if match else [path]
    if path.is_dir():
        from .models import directory_model_files
        parts = directory_model_files(path)
    # Stat all shards, never hash/read hundreds of GB of tensor data.
    identity = [(str(p.resolve()),p.stat().st_size,p.stat().st_mtime_ns) for p in parts]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class Presets:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('DREAM_MODEL_PRESETS', str(config.DATA_DIR/'config/model-presets.json')))

    def _read(self):
        if not self.path.exists(): return {'version':1,'models':{}}
        try:
            if self.path.stat().st_size > 4_000_000: raise ValueError('file too large')
            value=json.loads(self.path.read_text())
            if value.get('version')!=1 or not isinstance(value.get('models'),dict):raise ValueError('invalid schema')
            return value
        except (ValueError,AttributeError) as exc:
            raise ValueError(f'Invalid model presets at {self.path}; original preserved: {exc}') from exc

    def load(self, path):
        value=self._read()['models'].get(model_key(path))
        if value is not None and (not isinstance(value,dict) or not isinstance(value.get('selection'),dict) or not isinstance(value.get('revision'),str)):
            raise ValueError('Invalid saved model selection; original preserved')
        if value is not None:
            selection = value['selection']
            if set(selection) != {'gpus', 'ctx', 'options'} or not isinstance(selection['options'], dict):
                raise ValueError('Invalid saved selection fields; original preserved')
            gpus, ctx = selection['gpus'], selection['ctx']
            if type(ctx) is not int or not 9 <= ctx <= 2**31-1:
                raise ValueError('Invalid saved context; original preserved')
            if gpus is not None and (type(gpus) is not int or not 1 <= gpus <= 64):
                raise ValueError('Invalid saved GPU count; original preserved')
            from .settings import validate_options
            value = {**value, 'selection': {**selection, 'options':
                     validate_options(selection['options'], ctx=ctx, gpus=gpus)}}
        return value

    @contextmanager
    def _locked(self):
        import fcntl
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            os.chmod(lock.name,0o600)
            fcntl.flock(lock,fcntl.LOCK_EX)
            yield

    def _update(self,path,selection,expected,expected_key=None):
        key=model_key(path)
        if expected_key is not None and key != expected_key:
            raise ValueError("Model identity changed since selection; settings not saved")
        with self._locked():
            data=self._read();old=data['models'].get(key)
            if old is not None and not isinstance(old, dict):
                raise ValueError('Invalid saved model entry; original preserved')
            if (old.get('revision') if old else None)!=expected:
                raise ValueError('Model preset changed in another session; newer choices preserved')
            revision=None
            if selection is None: data['models'].pop(key,None)
            else:
                from .settings import validate_options
                gpus,ctx=selection['gpus'],selection['ctx']
                if gpus is not None and (type(gpus) is not int or not 1<=gpus<=64):raise ValueError('Invalid GPU count')
                if type(ctx) is not int or not 9<=ctx<=2**31-1:raise ValueError('Invalid context')
                selection = {**selection, 'options': validate_options(selection['options'],ctx=ctx,gpus=gpus)}
                stamp=datetime.now(timezone.utc).isoformat()
                revision=hashlib.sha256(json.dumps([selection,stamp],sort_keys=True).encode()).hexdigest()
                data['models'][key]={'selection':selection,'revision':revision,'saved_at':stamp,'model_path':str(path.resolve())}
            raw=json.dumps(data,indent=2,allow_nan=False)
            if len(raw.encode())>4_000_000:raise ValueError('Preset store full; archive old entries explicitly')
            if expected_key is not None and model_key(path) != expected_key:
                raise ValueError("Model identity changed before preset write")
            fd,tmp=tempfile.mkstemp(dir=self.path.parent,prefix='.model-presets-')
            try:
                with os.fdopen(fd,'w') as out:
                    out.write(raw);out.flush();os.fsync(out.fileno())
                os.replace(tmp,self.path)
            finally:Path(tmp).unlink(missing_ok=True)
            return revision

    def save(self,path,selection,*,expected,expected_key=None):return self._update(path,selection,expected,expected_key)
    def clear(self,path,*,expected):return self._update(path,None,expected)
