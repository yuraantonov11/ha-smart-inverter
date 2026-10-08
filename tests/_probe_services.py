import sys
import os
import types
import importlib.util

os.chdir("/tmp")


class _Coerce:
    def __init__(self, t):
        self.t = t

    def __call__(self, v):
        return self.t(v)


class _All:
    def __init__(self, *a, **kw):
        self.args = a

    def __call__(self, v):
        for a in self.args:
            v = a(v) if callable(a) else v
        return v


class _Range:
    def __init__(self, min=None, max=None):
        self.min = min
        self.max = max

    def __call__(self, v):
        if self.min is not None and v < self.min:
            raise ValueError()
        if self.max is not None and v > self.max:
            raise ValueError()
        return v


class _Length:
    def __init__(self, min=None, max=None):
        self.min = min
        self.max = max

    def __call__(self, v):
        if self.min is not None and len(v) < self.min:
            raise ValueError()
        if self.max is not None and len(v) > self.max:
            raise ValueError()
        return v


class _In:
    def __init__(self, c):
        self.choices = c

    def __call__(self, v):
        if v not in self.choices:
            raise ValueError()
        return v


class _Optional:
    def __init__(self, k, *args, **kw):
        self.key = k
        self.default = kw.get("default") or (
            args[0] if args else None
        )


class _Required:
    def __init__(self, k):
        self.key = k


class _Schema:
    def __init__(self, schema):
        self._schema = schema
        self._required = set()
        self._defaults = {}
        for k, v in schema.items():
            if isinstance(v, _Required):
                self._required.add(v.key)
            elif isinstance(v, _Optional):
                self._defaults[v.key] = v.default

    def __call__(self, data):
        if not isinstance(data, dict):
            raise ValueError()
        missing = self._required - set(data.keys())
        if missing:
            raise ValueError(f"{missing}")
        result = dict(self._defaults)
        for k, v in data.items():
            if k not in self._schema:
                continue
            spec = self._schema[k]
            validator = (
                spec.key
                if isinstance(spec, (_Optional, _Required))
                else spec
            )
            if callable(validator):
                result[k] = validator(v)
            else:
                result[k] = v
        return result


class _Vol:
    Schema = _Schema
    Optional = _Optional
    Required = _Required
    All = _All
    Length = _Length
    Range = _Range
    In = _In
    Coerce = _Coerce


sys.modules["voluptuous"] = _Vol()
ha = types.ModuleType("homeassistant")
sys.modules["homeassistant"] = ha
core = types.ModuleType("homeassistant.core")
sys.modules["homeassistant.core"] = core
core.HomeAssistant = type("HA", (), {})
core.ServiceCall = type("SC", (), {})
exc = types.ModuleType("homeassistant.exceptions")
sys.modules["homeassistant.exceptions"] = exc
exc.ServiceValidationError = type("SVE", (Exception,), {})
const = types.ModuleType("homeassistant.const")
sys.modules["homeassistant.const"] = const
const.Platform = types.SimpleNamespace()
pkg = types.ModuleType("powmr_inverter")
pkg.__path__ = ["/opt/data/powmr-ai-work/powmr_inverter"]
sys.modules["powmr_inverter"] = pkg
spec = importlib.util.spec_from_file_location(
    "powmr_inverter.const",
    "/opt/data/powmr-ai-work/powmr_inverter/const.py",
)
m = importlib.util.module_from_spec(spec)
sys.modules["powmr_inverter.const"] = m
spec.loader.exec_module(m)
pkg.DOMAIN = m.DOMAIN
spec = importlib.util.spec_from_file_location(
    "powmr_inverter.services",
    "/opt/data/powmr-ai-work/powmr_inverter/services/__init__.py",
)
svc = importlib.util.module_from_spec(spec)
sys.modules["powmr_inverter.services"] = svc
spec.loader.exec_module(svc)

print("dir:", sorted([x for x in dir(svc) if not x.startswith("_")]))