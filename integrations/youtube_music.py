"""
IMQ2 YouTube Music Integration
Uses the YouTube Data API v3 to search for tracks and create playlists.
Playlists created here appear in YouTube Music automatically.

Quota cost (10,000 units/day free):
  search:           100 units per call
  playlist.insert:   50 units
  playlistItems:     50 units per video added
"""

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


def search_tracks(query: str, max_results: int = 10) -> list[dict]:
    """
    Search YouTube for tracks matching query.
    Returns list of dicts with id, title, channel, duration_s, url.
    """
    from integrations.google_services import get_google_service
    yt = get_google_service("youtube")

    resp = yt.search().list(
        part="snippet",
        q=query,
        type="video",
        videoCategoryId="10",   # Music category
        maxResults=min(max_results, 25),
    ).execute()

    results = []
    for item in resp.get("items", []):
        vid_id = item["id"].get("videoId", "")
        snip   = item.get("snippet", {})
        if not vid_id:
            continue
        results.append({
            "id":      vid_id,
            "title":   snip.get("title", ""),
            "channel": snip.get("channelTitle", ""),
            "url":     f"https://music.youtube.com/watch?v={vid_id}",
        })

    log.info(f"YouTube search '{query}': {len(results)} results")
    return results


def create_playlist(title: str, description: str = "",
                    privacy: str = "unlisted") -> dict:
    """
    Create a YouTube playlist. Returns dict with id and url.
    Privacy: 'public', 'unlisted', or 'private'.
    """
    from integrations.google_services import get_google_service
    yt = get_google_service("youtube")

    resp = yt.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title":       title,
                "description": description,
            },
            "status": {"privacyStatus": privacy},
        },
    ).execute()

    pl_id = resp["id"]
    url   = f"https://music.youtube.com/playlist?list={pl_id}"
    log.info(f"Created playlist '{title}' ({pl_id})")
    return {"id": pl_id, "url": url, "title": title}


def add_tracks_to_playlist(playlist_id: str, video_ids: list[str]) -> int:
    """Add videos to a playlist. Returns count of successfully added tracks."""
    from integrations.google_services import get_google_service
    yt = get_google_service("youtube")

    added = 0
    for vid_id in video_ids:
        try:
            yt.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind":    "youtube#video",
                            "videoId": vid_id,
                        },
                    }
                },
            ).execute()
            added += 1
        except Exception as e:
            log.warning(f"Could not add {vid_id} to playlist: {e}")

    log.info(f"Added {added}/{len(video_ids)} tracks to playlist {playlist_id}")
    return added


def create_music_playlist(title: str, search_queries: list[str],
                          tracks_per_query: int = 3,
                          description: str = "",
                          privacy: str = "unlisted") -> dict:
    """
    High-level: create a playlist and fill it from a list of search queries.
    Returns dict with playlist url, title, and track list.
    """
    # Search for tracks
    all_tracks = []
    seen_ids   = set()
    for q in search_queries:
        results = search_tracks(q, max_results=tracks_per_query + 2)
        for t in results:
            if t["id"] not in seen_ids and len(all_tracks) < 50:
                seen_ids.add(t["id"])
                all_tracks.append(t)
            if len(all_tracks) >= tracks_per_query * len(search_queries):
                break

    if not all_tracks:
        return {"ok": False, "error": "No tracks found for the given queries."}

    # Create playlist
    pl = create_playlist(title=title, description=description, privacy=privacy)

    # Add tracks
    added = add_tracks_to_playlist(pl["id"], [t["id"] for t in all_tracks])

    return {
        "ok":     True,
        "url":    pl["url"],
        "title":  title,
        "tracks": all_tracks[:added],
        "count":  added,
    }
