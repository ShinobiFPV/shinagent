"""
IMQ2 X Games Analyst Tools
Spoken-text wrappers over integrations/xgames_data.py, used by both
watchalong agent modes whenever config.yaml's watchalong.active_sport is
"xgames". X Games results only post after events end (see that module's
docstring) -- there's no live scoring feed to poll, so this is a
lookup/status tool, not a proactive-alert source.
"""
import logging

log = logging.getLogger(__name__)


def get_xgames_results(discipline: str = "all", year: int = None) -> str:
    """Latest available X Games results, optionally filtered to a
    discipline (snowboard/ski/skateboard/bmx/moto_x). 'year' only
    matters for the hand-curated historical entries -- live scraping can
    only ever see the site's current default event (see xgames_data.py's
    docstring), so a specific past year isn't independently selectable
    here yet."""
    try:
        from integrations.xgames_data import get_xg_client

        xg = get_xg_client()
        results = xg.get_results(year)

        if discipline and discipline != "all":
            filtered = [r for r in results if discipline in (r.get("discipline") or "")]
            results = filtered or results  # don't blank the response if the filter matched nothing

        if not results:
            return f"No X Games results found{' for ' + discipline if discipline != 'all' else ''}."

        lines = ["X Games Results:"]
        for r in results[:10]:
            score_part = f" ({r['score']})" if r.get("score") else ""
            lines.append(f"  {r['event']}: Gold -- {r['gold']}{score_part}")
        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_xgames_results error: {e}", exc_info=True)
        return f"[xgames] Error: {e}"
