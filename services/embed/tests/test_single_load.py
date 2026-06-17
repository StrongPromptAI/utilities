"""Regression guard: the ONNX model must load exactly ONCE per process.

This bug has bitten twice. First in-process (lru_cache key mismatch between the
startup preload and per-request paths double-loaded the model and OOM'd — fixed
in 02203d7). Then again in this sidecar: `_get_session(model_id=None)` under
`@lru_cache` made `_get_session()` (key `()`) and `_get_session(None)` (key
`(None,)`) two distinct cache keys, so the warmup path loaded the model twice
every boot. On the 3 GB always-on chat box that ~2x boot peak left no headroom,
and a misrouted batch job one-shot OOM-killed it (2026-06-17 outage).

The fix removed the parameter so there is exactly one cache key. This test pins
that: the heavy ORT/tokenizer loads are stubbed and counted, and the app.py
warmup call sequence is replicated. Any future signature that reintroduces a
defaulted/variable cache key — or any second call site with a different key —
fails here instead of in production memory.
"""

import sys
import types
from functools import lru_cache


def _install_stubs(counter: dict):
    """Replace onnxruntime + transformers with light stubs that count session loads
    and return valid minimal shapes so _embed() runs end-to-end."""
    import numpy as np

    fake_ort = types.ModuleType("onnxruntime")

    class _SessOpts:
        intra_op_num_threads = 0
        inter_op_num_threads = 0

    class _InferenceSession:
        def __init__(self, *a, **k):
            counter["n"] += 1

        def get_inputs(self):
            return [types.SimpleNamespace(name="input_ids"),
                    types.SimpleNamespace(name="attention_mask")]

        def run(self, _outputs, _inputs):
            # (batch, seq_len, hidden_dim) — batch/seq inferred from input shape
            n = _inputs["input_ids"].shape[0]
            seq = _inputs["input_ids"].shape[1]
            return [np.ones((n, seq, 768), dtype=np.float32)]

    fake_ort.SessionOptions = _SessOpts
    fake_ort.InferenceSession = _InferenceSession
    sys.modules["onnxruntime"] = fake_ort

    fake_tf = types.ModuleType("transformers")

    class _Tok:
        def __call__(self, texts, **k):
            n = len(texts)
            return {
                "input_ids": np.ones((n, 3), dtype=np.int64),
                "attention_mask": np.ones((n, 3), dtype=np.int64),
            }

    class _AutoTok:
        @staticmethod
        def from_pretrained(*a, **k):
            return _Tok()

    fake_tf.AutoTokenizer = _AutoTok
    sys.modules["transformers"] = fake_tf


def test_model_loads_once_through_warmup():
    counter = {"n": 0}
    _install_stubs(counter)

    import nomic_embed as ne

    # Force the baked-path branch so no HF download is attempted.
    orig_exists = ne.os.path.exists
    ne.os.path.exists = lambda p: True
    try:
        ne._get_session.cache_clear()

        # Replicate app.py _warmup(): _get_session() then _embed(["warmup"]).
        ne._get_session()
        ne._embed(["warmup"])  # internally calls _get_session() again — must be a cache hit
    finally:
        ne.os.path.exists = orig_exists

    assert counter["n"] == 1, (
        f"model loaded {counter['n']}x through warmup — expected 1. "
        "A defaulted/variable arg on _get_session re-introduced the lru_cache "
        "key-mismatch double-load (see module docstring)."
    )


def test_defaulted_arg_signature_double_loads():
    """Pin WHY the parameter was removed: a defaulted arg double-loads. If this
    ever stops being true the fix's rationale changed and the guard above is moot."""
    counter = {"n": 0}

    @lru_cache(maxsize=1)
    def buggy_get_session(model_id=None):
        counter["n"] += 1
        return object()

    buggy_get_session()       # key ()
    buggy_get_session(None)   # key (None,) — what _embed used to pass
    assert counter["n"] == 2


if __name__ == "__main__":
    # Dependency-free runner (embed venv has no pytest); still pytest-collectable.
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
