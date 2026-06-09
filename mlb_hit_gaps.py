"""
MLB batter "hit gap" analysis -> daily static site.

For every batter in the chosen date range, builds a table with:
  - AVG ................. batting average (H / AB)
  - PA .................. total plate appearances
  - H ................... total hits
  - avg_PA_between_hits . average HITLESS plate appearances between
                          consecutive hits (the typical dry spell)
  - PA_since_last_hit ... hitless plate appearances since the most recent
                          hit (the current ongoing drought)

Outputs:
  - index.html ........ self-contained, sortable "scoreboard" page
  - mlb_hit_gaps.csv .. raw data

Data source: pitch-by-pitch Statcast via pybaseball, rolled up to one row
per plate appearance. Columns 2 & 3 depend on PA ORDER, so we need
sequential data rather than season totals.

Run locally (needs internet access to baseballsavant.mlb.com):
    pip install -r requirements.txt
    python mlb_hit_gaps.py
"""

import datetime as dt
import html
import pandas as pd

# ---------------------------------------------------------------- config
SEASON_START = "2026-03-26"                    # adjust to opening day
SEASON_END   = dt.date.today().isoformat()     # through today
MIN_PA       = 50                              # drop tiny samples
SORT_BY      = "PA_since_last_hit"             # default sort column
HTML_OUT     = "index.html"
CSV_OUT      = "mlb_hit_gaps.csv"

HIT_EVENTS = {"single", "double", "triple", "home_run"}
NON_AB_EVENTS = {
    "walk", "intent_walk", "hit_by_pitch",
    "sac_fly", "sac_bunt", "sac_fly_double_play", "sac_bunt_double_play",
    "catcher_interf",
}


# ---------------------------------------------------------------- pure logic
def compute_player_metrics(pa: pd.DataFrame) -> pd.DataFrame:
    """One row per PA in -> one row per batter out. Network-free / testable."""
    pa = pa.copy()
    pa["game_date"] = pd.to_datetime(pa["game_date"])
    pa["is_hit"] = pa["events"].isin(HIT_EVENTS)
    pa["is_ab"]  = ~pa["events"].isin(NON_AB_EVENTS)

    rows = []
    for batter_id, g in pa.groupby("batter", sort=False):
        g = g.sort_values(["game_date", "game_pk", "at_bat_number"]).reset_index(drop=True)
        n_pa = len(g)
        n_ab = int(g["is_ab"].sum())
        n_h  = int(g["is_hit"].sum())
        avg  = n_h / n_ab if n_ab > 0 else float("nan")

        hit_pos = g.index[g["is_hit"]].tolist()
        if len(hit_pos) >= 2:
            gaps = [hit_pos[i + 1] - hit_pos[i] - 1 for i in range(len(hit_pos) - 1)]
            avg_between = sum(gaps) / len(gaps)
        else:
            avg_between = float("nan")

        pa_since = (n_pa - 1) - hit_pos[-1] if hit_pos else n_pa

        rows.append({
            "batter": batter_id,
            "AVG": round(avg, 3) if avg == avg else avg,
            "PA": n_pa,
            "H": n_h,
            "avg_PA_between_hits": round(avg_between, 2) if avg_between == avg_between else avg_between,
            "PA_since_last_hit": pa_since,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- network
def fetch_pa_data(start: str, end: str) -> pd.DataFrame:
    from pybaseball import statcast, cache
    cache.enable()  # caches per-day so reruns only fetch new days
    print(f"Downloading Statcast {start} -> {end} (first run is slow)...")
    data = statcast(start_dt=start, end_dt=end)
    return data[data["events"].notna()][
        ["batter", "game_date", "game_pk", "at_bat_number", "events"]
    ].copy()


def add_player_names(df: pd.DataFrame) -> pd.DataFrame:
    from pybaseball import playerid_reverse_lookup
    lk = playerid_reverse_lookup(df["batter"].tolist(), key_type="mlbam")
    lk["player"] = lk["name_first"].str.title() + " " + lk["name_last"].str.title()
    df.insert(0, "player", df["batter"].map(dict(zip(lk["key_mlbam"], lk["player"]))))
    return df.drop(columns="batter")


# ---------------------------------------------------------------- html
COLS = [
    ("player", "Player", "txt"),
    ("AVG", "AVG", "num"),
    ("PA", "PA", "num"),
    ("H", "H", "num"),
    ("avg_PA_between_hits", "Avg PA Between Hits", "num"),
    ("PA_since_last_hit", "PA Since Last Hit", "num"),
]

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MLB Hit Gaps</title>
<style>
:root{
  --paper:#f6f4ee; --ink:#16110d; --line:#d8d2c4; --muted:#6b6256;
  --board:#11201a; --board-ink:#eef1ec; --chalk:#b78a2e; --cold:#2f6fb0;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.4 ui-sans-serif,system-ui,"Helvetica Neue",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:28px 18px 60px}
header{border-bottom:3px solid var(--ink);padding-bottom:14px;margin-bottom:8px}
h1{margin:0;font:700 30px/1 "Arial Narrow","Helvetica Neue",sans-serif;
  letter-spacing:.14em;text-transform:uppercase}
.sub{color:var(--muted);font-size:13px;margin-top:6px}
.legend{font-size:12.5px;color:var(--muted);margin:14px 0 18px;max-width:760px}
.legend b{color:var(--ink)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
thead th{position:sticky;top:0;background:var(--board);color:var(--board-ink);
  text-align:right;padding:10px 12px;font:600 12px/1 ui-sans-serif,sans-serif;
  letter-spacing:.05em;cursor:pointer;user-select:none;white-space:nowrap}
thead th:first-child{text-align:left}
thead th .arr{color:var(--chalk);font-size:10px;margin-left:4px}
tbody td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line);
  font-family:ui-monospace,"SFMono-Regular","Cascadia Code",monospace;font-size:13.5px}
tbody td:first-child{text-align:left;font-family:inherit;font-weight:600}
tbody tr:hover{background:#efeadd}
.rank{color:var(--muted);font-variant-numeric:tabular-nums;
  font-family:ui-monospace,monospace;font-size:12px;padding-right:6px}
footer{margin-top:22px;font-size:12px;color:var(--muted)}
a{color:var(--cold)}
@media(max-width:560px){h1{font-size:23px}.legend{font-size:12px}}
</style></head>
<body><div class="wrap">
<header>
  <h1>MLB Hit Gaps</h1>
  <div class="sub">Updated __UPDATED__ &middot; __NPLAYERS__ batters &middot; min __MINPA__ PA &middot; season since __START__</div>
</header>
<p class="legend">
  <b>AVG</b> batting average. &nbsp;
  <b>Avg PA Between Hits</b> the typical hitless plate-appearance streak between hits. &nbsp;
  <b>PA Since Last Hit</b> the current ongoing drought &mdash; deeper blue means colder. &nbsp;
  Click any header to sort.
</p>
__TABLE__
<footer>Source: Statcast via pybaseball. AVG computed from Statcast events and may differ from official by the odd hit.</footer>
</div>
<script>
const tbody=document.querySelector('tbody');
document.querySelectorAll('th').forEach((th,ci)=>{
  let asc=false;
  th.addEventListener('click',()=>{
    asc=!asc;
    document.querySelectorAll('th .arr').forEach(a=>a.textContent='');
    th.querySelector('.arr').textContent=asc?'\\u25B2':'\\u25BC';
    const rows=[...tbody.querySelectorAll('tr')];
    rows.sort((a,b)=>{
      const x=a.children[ci].dataset.v, y=b.children[ci].dataset.v;
      const nx=parseFloat(x), ny=parseFloat(y);
      const xn=!isNaN(nx), yn=!isNaN(ny);
      if(xn&&yn) return asc?nx-ny:ny-nx;
      if(xn) return asc?1:-1; if(yn) return asc?-1:1;
      return asc?x.localeCompare(y):y.localeCompare(x);
    });
    rows.forEach((r,i)=>{r.querySelector('.rank').textContent=i+1; tbody.appendChild(r);});
  });
});
</script>
</body></html>
"""


def write_html(df: pd.DataFrame, path: str):
    max_cold = max(int(df["PA_since_last_hit"].max()), 1)

    head = "<tr><th class='rank'></th>" + "".join(
        f"<th>{html.escape(label)}<span class='arr'></span></th>" for _, label, _ in COLS
    ) + "</tr>"

    body = []
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        cells = [f"<td class='rank'>{i}</td>"]
        for key, _, kind in COLS:
            val = row[key]
            if pd.isna(val):
                disp, sortv = "\u2014", ""
            elif key == "AVG":
                disp = f"{val:.3f}".lstrip("0") or ".000"; sortv = str(val)
            elif kind == "num":
                disp = f"{val:g}"; sortv = str(val)
            else:
                disp = html.escape(str(val)); sortv = str(val)
            style = ""
            if key == "PA_since_last_hit" and not pd.isna(val):
                a = min(val / max_cold, 1) * 0.78
                style = f" style='background:rgba(47,111,176,{a:.2f});'"
                if a > 0.45:
                    style = f" style='background:rgba(47,111,176,{a:.2f});color:#fff;'"
            cells.append(f"<td data-v=\"{html.escape(sortv)}\"{style}>{disp}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    table = f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"
    page = (PAGE
            .replace("__TABLE__", table)
            .replace("__UPDATED__", dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
            .replace("__NPLAYERS__", str(len(df)))
            .replace("__MINPA__", str(MIN_PA))
            .replace("__START__", SEASON_START))
    with open(path, "w") as f:
        f.write(page)


# ---------------------------------------------------------------- main
def main():
    pa = fetch_pa_data(SEASON_START, SEASON_END)
    table = compute_player_metrics(pa)
    table = table[table["PA"] >= MIN_PA]
    table = add_player_names(table)
    table = table.sort_values(SORT_BY, ascending=False).reset_index(drop=True)
    table.to_csv(CSV_OUT, index=False)
    write_html(table, HTML_OUT)
    print(f"Wrote {len(table)} players -> {HTML_OUT}, {CSV_OUT}")


if __name__ == "__main__":
    main()
