"""
Build baseline.json: season-to-date batter state from the MLB Stats API.

Output per player (keyed by MLBAM id):
    name, team, pa, ab, h, sum_gaps, since
The live page loads this and continues the SAME running tally as today's
plate appearances come in, so the math stays identical (proven by parity test).

We use the Stats API play-by-play, which is explicitly ordered by atBatIndex,
so there is no chronological-ordering ambiguity (the source of the earlier bug).

Runs in CI daily. Caches each finished game so later runs only fetch new games.
"""

import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "https://statsapi.mlb.com"
SEASON_START = "2026-03-26"          # opening day
CACHE_DIR = "pbp_cache"              # finished games cached here (persisted by CI)
OUT = "baseline.json"
WORKERS = 8
TIMEOUT = 20

HIT_EVENTS = {"single", "double", "triple", "home_run"}
NON_AB_EVENTS = {
    "walk", "intent_walk", "hit_by_pitch",
    "sac_fly", "sac_bunt", "sac_fly_double_play", "sac_bunt_double_play",
    "catcher_interf",
}


def et_today():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return dt.date.today()


def get_json(session, url):
    for attempt in range(3):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
    return None


def team_abbrev_map(session):
    data = get_json(session, f"{BASE}/api/v1/teams?sportId=1")
    return {t["id"]: t.get("abbreviation", "") for t in data.get("teams", [])}


def finished_games(session, start, end):
    """Return list of (officialDate, gamePk, awayId, homeId) for Final games."""
    url = f"{BASE}/api/v1/schedule?sportId=1&startDate={start}&endDate={end}"
    data = get_json(session, url)
    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            teams = g.get("teams", {})
            games.append((
                g.get("officialDate", day.get("date")),
                g["gamePk"],
                teams.get("away", {}).get("team", {}).get("id"),
                teams.get("home", {}).get("team", {}).get("id"),
            ))
    return games


def fetch_pbp(session, game_pk):
    """Return allPlays for a game, using the on-disk cache for finished games."""
    path = os.path.join(CACHE_DIR, f"{game_pk}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    data = get_json(session, f"{BASE}/api/v1/game/{game_pk}/playByPlay")
    plays = data.get("allPlays", [])
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(plays, f)
    return plays


def plate_appearances(plays, away_abbr, home_abbr):
    """Yield (atBatIndex, batter_id, batter_name, is_hit, is_ab, team)."""
    for p in plays:
        res = p.get("result", {})
        ab_about = p.get("about", {})
        if res.get("type") != "atBat" or not ab_about.get("isComplete"):
            continue
        et = res.get("eventType")
        if not et:
            continue
        b = p.get("matchup", {}).get("batter", {})
        team = away_abbr if ab_about.get("halfInning") == "top" else home_abbr
        yield (
            ab_about.get("atBatIndex", 0),
            b.get("id"), b.get("fullName"),
            et in HIT_EVENTS, et not in NON_AB_EVENTS, team,
        )


def reduce_player(seq):
    """seq: ordered list of (is_hit, is_ab). Returns (pa, ab, h, sum_gaps, since)."""
    pa = ab = h = sum_gaps = since = 0
    for is_hit, is_ab in seq:
        pa += 1
        if is_ab:
            ab += 1
        if is_hit:
            if h >= 1:
                sum_gaps += since
            h += 1
            since = 0
        else:
            since += 1
    return pa, ab, h, sum_gaps, since


def main():
    cutoff = et_today()                       # live layer fetches this date onward
    end = (cutoff - dt.timedelta(days=1)).isoformat()
    session = requests.Session()

    abbr = team_abbrev_map(session)
    games = finished_games(session, SEASON_START, end)
    print(f"{len(games)} finished games {SEASON_START}..{end}", file=sys.stderr)

    # fetch all games (cached ones are instant)
    plays_by_game = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_pbp, session, pk): pk for (_, pk, _, _) in games}
        for fut in as_completed(futs):
            pk = futs[fut]
            try:
                plays_by_game[pk] = fut.result()
            except Exception as e:
                print(f"skip game {pk}: {e}", file=sys.stderr)

    # collect ordered PAs per batter across the season
    per_batter = {}   # id -> list of (sortkey, is_hit, is_ab, team, name)
    for (date, pk, away_id, home_id) in sorted(games):
        plays = plays_by_game.get(pk)
        if not plays:
            continue
        away, home = abbr.get(away_id, ""), abbr.get(home_id, "")
        for (idx, bid, name, is_hit, is_ab, team) in plate_appearances(plays, away, home):
            if bid is None:
                continue
            per_batter.setdefault(bid, []).append(((date, pk, idx), is_hit, is_ab, team, name))

    players = {}
    for bid, rows in per_batter.items():
        rows.sort(key=lambda r: r[0])
        pa, ab, h, sum_gaps, since = reduce_player([(r[1], r[2]) for r in rows])
        players[str(bid)] = {
            "name": rows[-1][4],
            "team": rows[-1][3],
            "pa": pa, "ab": ab, "h": h, "sum_gaps": sum_gaps, "since": since,
        }

    out = {
        "cutoffDate": cutoff.isoformat(),
        "generatedAt": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": SEASON_START[:4],
        "players": players,
    }
    with open(OUT, "w") as f:
        json.dump(out, f)
    print(f"wrote {len(players)} players -> {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
