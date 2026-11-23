import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("halumem_official_shim", ROOT / "scripts" / "halumem_official_shim.py")
shim = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("halumem_official_shim", shim)  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(shim)


def test_budget_stop_escapes_the_official_except_exception_pattern():
    meter = shim.Meter(1.0, 1.0, cap_usd=0.0)
    create = shim.meter_create(lambda **k: None, meter)

    def official_style():  # mirrors evaluation.process_user: future.result() inside try/except Exception
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(create) for _ in range(3)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    return "swallowed"
        return "finished"

    with pytest.raises(shim.BudgetExceeded):
        official_style()


def test_meter_records_usage_injects_extra_body_and_maps_402():
    meter = shim.Meter(0.5, 1.0, cap_usd=100.0)
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4))

    shim.meter_create(create, meter, {"thinking": {"type": "disabled"}})(model="m", messages=[])
    assert seen["extra_body"] == {"thinking": {"type": "disabled"}}
    assert (meter.calls, meter.input_tokens, meter.output_tokens, meter.spent) == (1, 10, 4, 9.0)
    meter.extra_usd = 1.0
    assert meter.spent == 10.0

    class E402(Exception):
        status_code = 402

    def broke(**kwargs):
        raise E402("Insufficient Balance")

    with pytest.raises(shim.BudgetExceeded):
        shim.meter_create(broke, meter)()
    assert meter.errors == 1


def test_read_constants_does_not_execute(tmp_path):
    f = tmp_path / "m.py"
    f.write_text('raise SystemExit("executed")\nA = "x"\nB = """multi\nline"""\nC = 3\n')
    assert shim.read_constants(f, ("A", "B")) == {"A": "x", "B": "multi\nline"}
    with pytest.raises(KeyError):
        shim.read_constants(f, ("C",))
