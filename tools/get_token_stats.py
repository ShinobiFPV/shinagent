"""
IMQ2 Tool — get_token_stats
Returns token usage statistics from the SQLite memory database.
Q2 can use this to report usage, log to a sheet, or answer questions about cost.
"""
import logging
log = logging.getLogger(__name__)

def get_token_stats(since_days: int = None) -> str:
    """
    Return token usage stats from the conversation database.
    Optionally filter to the last N days with since_days.
    """
    try:
        from memory.manager import MemoryManager
        m = MemoryManager()
        s = m.token_stats(since_days=since_days)
        m.close()
        period = f"last {since_days} days" if since_days else "all time"
        return (
            f"Token usage ({period}): "
            f"{s['turns']} turns, "
            f"{s['prompt_tokens']:,} prompt tokens, "
            f"{s['completion_tokens']:,} completion tokens, "
            f"{s['total_tokens']:,} total tokens."
        )
    except Exception as e:
        log.error(f"get_token_stats error: {e}")
        return f"Could not retrieve token stats: {e}"
