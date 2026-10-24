import requests, csv, pathlib, time

# ====================== CONFIG (edit here) ======================
OUT = pathlib.Path("first_baskets.csv")

# Game ID prefixes to include: 002 = regular season (incl. NBA Cup group games),
# 004 = playoffs, 005 = play-in, 006 = NBA Cup knockout rounds.
INCLUDE_PREFIXES = ("002",)

# Method rules are checked in order against the play's text (subType + description).
# First match wins. Anything left over that is a 2-pointer becomes "Other".
METHOD_RULES = [
    ("dunk",  "Dunk"),     # also catches "alley oop dunk", "tip dunk", "driving dunk"
    ("layup", "Layup"),     # also catches "driving layup", "tip layup", "putback layup"
]
ALLEY_OOP_KEYWORDS = ("alley", "alley-oop", "alley oop")   # flags the alley_oop column
# ================================================================

BASE = "https://cdn.nba.com/static/json"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"}
FIELDS = ["date", "game_id", "away", "home",
          "tip_winner_player", "tip_winner_team",
          "fb_team", "fb_player", "fb_method", "alley_oop",
          "fb_points", "fb_assist", "fb_period_clock", "raw_subtype", "raw_description"]

def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))

def final_games():
    sched = get(f"{BASE}/staticData/scheduleLeagueV2.json")
    for d in sched["leagueSchedule"]["gameDates"]:
        for g in d["games"]:
            if g["gameStatus"] == 3 and g["gameId"].startswith(INCLUDE_PREFIXES):
                yield g

def is_scoring_play(a):
    if a.get("shotResult") != "Made":
        return False
    return a["actionType"] in ("2pt", "3pt", "freethrow")

def classify(a):
    text = f"{a.get('subType', '')} {a.get('description', '')}".lower()
    alley = any(k in text for k in ALLEY_OOP_KEYWORDS)
    if a["actionType"] == "freethrow":
        return "Free Throw", False
    if a["actionType"] == "3pt":
        return "3 Pointer", alley
    for keyword, label in METHOD_RULES:
        if keyword in text:
            return label, alley
    return "Other", alley

def parse(g):
    acts = get(f"{BASE}/liveData/playbyplay/playByPlay_{g['gameId']}.json")["game"]["actions"]
    acts = sorted(acts, key=lambda a: a.get("orderNumber", a.get("actionNumber", 0)))

    fb = next((a for a in acts if is_scoring_play(a)), None)
    if not fb:
        return None

    tip = next((a for a in acts if a["actionType"] == "jumpball" and a["period"] == 1), None)
    tip_player = tip_team = ""
    if tip:
        tip_player = tip.get("jumpBallWonPlayerName", "")
        won_id = tip.get("jumpBallWonPersonId")
        # Team of the tip winner = team of that player's first other action
        # (more reliable than the teamTricode on the jump ball row itself)
        other = next((a for a in acts if a.get("personId") == won_id
                      and a["actionType"] != "jumpball" and a.get("teamTricode")), None)
        tip_team = other["teamTricode"] if other else tip.get("teamTricode", "")

    method, alley = classify(fb)
    return {
        "date": g["gameDateEst"][:10],
        "game_id": g["gameId"],
        "away": g["awayTeam"]["teamTricode"],
        "home": g["homeTeam"]["teamTricode"],
        "tip_winner_player": tip_player,
        "tip_winner_team": tip_team,
        "fb_team": fb["teamTricode"],
        "fb_player": fb["playerName"],
        "fb_method": method,
        "alley_oop": alley,
        "fb_points": 3 if fb["actionType"] == "3pt" else 1 if fb["actionType"] == "freethrow" else 2,
        "fb_assist": fb.get("assistPlayerNameInitial", "") or "Unassisted",
        "fb_period_clock": f"Q{fb['period']} {fb['clock']}",
        "raw_subtype": fb.get("subType", ""),
        "raw_description": fb.get("description", ""),
    }

# ---- main: load existing, add only new games, rewrite sorted ----
existing = []
if OUT.exists():
    with OUT.open(newline="") as f:
        existing = list(csv.DictReader(f))
done = {r["game_id"] for r in existing}

new_rows = []
for g in final_games():
    if g["gameId"] in done:
        continue
    try:
        row = parse(g)
        if row:
            new_rows.append(row)
    except Exception as e:
        print("skip (will retry next run):", g["gameId"], e)

all_rows = sorted(existing + new_rows, key=lambda r: (r["date"], r["game_id"]))
with OUT.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(all_rows)
print(f"added {len(new_rows)} games, total {len(all_rows)}")
