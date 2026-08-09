import gc
import logging
import psutil
from typing import Dict, Any

logger = logging.getLogger(__name__)

MAX_RAM_LIMIT_MB = 4096.0  # Strict 4GB RAM Ceiling from Constitution Principle I


def get_memory_usage() -> Dict[str, Any]:
    """Returns current process RAM usage and system RAM usage in MB."""
    process = psutil.Process()
    process_ram_mb = process.memory_info().rss / (1024 * 1024)
    system_mem = psutil.virtual_memory()
    system_used_ram_mb = system_mem.used / (1024 * 1024)
    system_total_ram_mb = system_mem.total / (1024 * 1024)

    return {
        "process_ram_mb": round(process_ram_mb, 2),
        "system_used_ram_mb": round(system_used_ram_mb, 2),
        "system_total_ram_mb": round(system_total_ram_mb, 2),
        "max_ceiling_mb": MAX_RAM_LIMIT_MB,
        "is_under_limit": process_ram_mb <= MAX_RAM_LIMIT_MB,
    }


def enforce_memory_ceiling(limit_mb: float = MAX_RAM_LIMIT_MB) -> None:
    """Raises MemoryError if process memory exceeds limit ceiling."""
    usage = get_memory_usage()
    if usage["process_ram_mb"] > limit_mb:
        trigger_garbage_collection()
        # Re-check after GC
        usage_after = get_memory_usage()
        if usage_after["process_ram_mb"] > limit_mb:
            err_msg = (
                f"RAM Limit Exceeded! Current usage {usage_after['process_ram_mb']}MB "
                f"exceeds ceiling of {limit_mb}MB."
            )
            logger.error(err_msg)
            raise MemoryError(err_msg)


def trigger_garbage_collection() -> None:
    """Executes explicit Python garbage collection and model cache clearing."""
    logger.info("Executing garbage collection...")
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
