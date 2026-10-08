"""Explicit CPU determinism for the standalone real-model benchmark process."""

from importlib import import_module


def configure_cpu_benchmark(seed: int) -> None:
    torch = import_module("torch")
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
