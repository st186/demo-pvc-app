"""GPU selection and CPU fallback, simulated (CI and dev machines have no GPU)."""

import pytest
from PIL import Image

from pvccard import passport

pytestmark = pytest.mark.skipif(not passport.available(), reason="install .[photo]")


@pytest.fixture
def fresh(monkeypatch):
    """Isolated session cache and GPU state for one test."""
    monkeypatch.setattr(passport, "_sessions", {})
    monkeypatch.setattr(passport, "_state", {q: "idle" for q in passport.MODELS})
    monkeypatch.setattr(passport, "_gpu_active", None)
    monkeypatch.setattr(passport, "_gpu_failed", False)
    return monkeypatch


def test_working_gpu_makes_best_the_default(fresh):
    # Pretend the CPU provider is a GPU: sessions really run on it.
    fresh.setattr(passport, "GPU_PROVIDERS", ("CPUExecutionProvider",))
    assert passport.default_quality() == "fast"      # unverified until a session runs
    passport._rembg_session("fast")
    assert passport._gpu_active is True
    assert passport.default_quality() == "best"


def test_gpu_that_fails_to_load_falls_back_to_cpu(fresh):
    fresh.setattr(passport, "GPU_PROVIDERS", ("CPUExecutionProvider",))
    real = passport._new_session

    def broken_gpu(quality, gpu):
        if gpu:
            raise RuntimeError("DirectML device lost")
        return real(quality, gpu)

    fresh.setattr(passport, "_new_session", broken_gpu)
    passport._rembg_session("fast")
    assert passport._gpu_failed and passport.gpu_provider() is None
    assert passport.default_quality() == "fast"
    assert passport.status("fast")["state"] == "ready"


def test_gpu_that_fails_mid_run_retries_on_cpu(fresh):
    fresh.setattr(passport, "GPU_PROVIDERS", ("CPUExecutionProvider",))
    import rembg
    real_remove = rembg.remove
    calls = []

    def flaky_remove(img, session=None, **kw):
        calls.append(passport._gpu_active)
        if passport._gpu_active:
            raise RuntimeError("GPU out of memory")
        return real_remove(img, session=session, **kw)

    fresh.setattr(rembg, "remove", flaky_remove)
    out = passport._remove(Image.new("RGB", (64, 64), "white"), "fast")
    assert out.mode == "RGBA"
    assert calls == [True, False]                    # GPU try, then CPU retry
    assert passport._gpu_failed
