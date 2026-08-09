from src.backend.utils.memory_monitor import get_memory_usage, enforce_memory_ceiling


def test_memory_ceiling_under_limit():
    mem_info = get_memory_usage()
    assert mem_info["process_ram_mb"] <= 4096.0
    assert mem_info["is_under_limit"] is True


def test_enforce_memory_ceiling():
    # Should complete without error when memory usage is normal
    enforce_memory_ceiling(limit_mb=4096.0)
