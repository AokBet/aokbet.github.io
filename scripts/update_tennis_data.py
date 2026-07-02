#!/usr/bin/env python3
"""Construit les scores structurés et les profils d'analyse tennis AokBet.

Les scores viennent de Livescores. Les statistiques historiques proviennent des
jeux de données publics de Jeff Sackmann / Tennis Abstract (CC BY-NC-SA 4.0).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
LIVE_PATH = ROOT / "tennis-live.json"
HISTORY_PATH = ROOT / "tennis-history.json"
NOW = datetime.now(timezone.utc)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SURFACES = {
    "wimbledon": "Grass", "eastbourne": "Grass", "queens-club": "Grass",
    "halle": "Grass", "stuttgart": "Grass", "s-hertogenbosch": "Grass",
    "nottingham": "Grass", "ilkley": "Grass", "newport": "Grass",
    "roland-garros": "Clay", "french-open": "Clay", "madrid": "Clay",
    "rome": "Clay", "monte-carlo": "Clay", "barcelona": "Clay",
    "hamburg": "Clay", "bastad": "Clay", "gstaad": "Clay",
    "kitzbuhel": "Clay", "brasov-romania": "Clay", "milan-italy": "Clay",
    "quito-ecuador": "Clay", "troyes-france": "Clay",
    "australian-open": "Hard", "us-open": "Hard", "indian-wells": "Hard",
    "miami": "Hard", "cincinnati": "Hard", "canada": "Hard",
    "washington": "Hard", "beijing": "Hard", "shanghai": "Hard",
}


def slug_label(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split("-") if part)


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def safe_int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def surface_for(slug: str) -> str:
    lowered = slug.lower()
    for key, surface in SURFACES.items():
        if key in lowered:
            return surface
    return "Unknown"


def competition_parts(category: str, section: str):
    section_lower = section.lower()
    if section_lower.startswith(("women", "ladies")):
        tour = "WTA"
    elif section_lower.startswith("men"):
        tour = "ATP"
    else:
        tour = "WTA" if category.lower().startswith("wta") else "ATP"

    event_type = "doubles" if "double" in section_lower else "singles"
    generic_section = any(word in section_lower for word in ("single", "double"))
    tournament_slug = category if generic_section else section
    level = "Grand Slam" if tournament_slug in {"wimbledon", "roland-garros", "australian-open", "us-open"} else (
        "Challenger" if "challenger" in category else tour
    )
    return tour, event_type, tournament_slug, level


def score_values(container):
    if container is None:
        return [], []
    values, tiebreaks = [], []
    for cell in container.select(":scope > .yf"):
        direct = "".join(str(node).strip() for node in cell.contents if getattr(node, "name", None) != "sup").strip()
        if direct == "":
            continue
        score = safe_int(direct)
        if score is None:
            continue
        values.append(score)
        sup = cell.find("sup")
        tiebreaks.append(safe_int(sup.get_text(strip=True)) if sup else None)
    return values[:5], tiebreaks[:5]


def completed_set(a, b):
    if a is None or b is None:
        return False
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low in {5, 6}) or (high >= 10 and high - low >= 2)


def sets_won(own, other, status):
    total = 0
    for a, b in zip(own, other):
        if status == "finished" or completed_set(a, b):
            total += int(a > b)
    return total


def fetch_score_pages():
    pages = []
    for day_offset in (0, 1):
        date = (NOW + timedelta(days=day_offset)).date().isoformat()
        url = "https://www.livescores.com/tennis/" if day_offset == 0 else f"https://www.livescores.com/tennis/{date}/"
        try:
            response = requests.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            if "-vs-" in response.text:
                pages.append((date, response.text))
                print(f"Fetch OK {url}: {len(response.text)} octets", file=sys.stderr)
        except Exception as exc:
            print(f"Fetch error {url}: {exc}", file=sys.stderr)
    return pages


def parse_score_pages(pages):
    from bs4 import BeautifulSoup

    events, seen = [], set()
    pattern = re.compile(r"^/tennis/([^/]+)/([^/]+)/(.+)-vs-(.+)/(\d{5,8})/?$")
    for page_date, html in pages:
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("a", href=True):
            match = pattern.match(link["href"])
            if not match:
                continue
            category, section, p1_slug, p2_slug, event_id = match.groups()
            if event_id in seen:
                continue
            seen.add(event_id)

            status_node = link.select_one(".Ih")
            status_text = status_node.get_text(" ", strip=True) if status_node else ""
            raw = link.get_text(" ", strip=True)
            if re.search(r"Canc\.|Retired|W\.O\.", raw, re.I):
                continue
            if re.fullmatch(r"FT|Fin\.?", status_text, re.I):
                status = "finished"
            elif re.fullmatch(r"\d{1,2}:\d{2}", status_text):
                status = "scheduled"
            else:
                status = "live"

            team_nodes = link.select(".nf.of > .xf")
            teams = []
            for team_node in team_nodes[:2]:
                members = [node.get_text(" ", strip=True) for node in team_node.select(".vf") if node.get_text(" ", strip=True)]
                teams.append(members)
            p1_members = teams[0] if len(teams) > 0 and teams[0] else [slug_label(p1_slug)]
            p2_members = teams[1] if len(teams) > 1 and teams[1] else [slug_label(p2_slug)]
            p1_name = " / ".join(p1_members)
            p2_name = " / ".join(p2_members)
            score_groups = link.select(".pf.of > span")
            p1_sets, p1_tb = score_values(score_groups[0] if len(score_groups) > 0 else None)
            p2_sets, p2_tb = score_values(score_groups[1] if len(score_groups) > 1 else None)
            games = link.select(".uf > .yf")
            p1_game = games[0].get_text(strip=True) if len(games) > 0 else ""
            p2_game = games[1].get_text(strip=True) if len(games) > 1 else ""
            if status == "live" and not p1_sets and not p2_sets:
                status = "scheduled"

            tour, event_type, tournament_slug, level = competition_parts(category, section)
            timestamp_node = link.find_next("button", attrs={"data-favouritesDetails": True})
            scheduled_at = None
            if timestamp_node:
                epoch_match = re.search(r"-(\d{13})$", timestamp_node.get("data-favouritesDetails", ""))
                if epoch_match:
                    scheduled_at = datetime.fromtimestamp(int(epoch_match.group(1)) / 1000, timezone.utc).isoformat()

            p1_won = sets_won(p1_sets, p2_sets, status)
            p2_won = sets_won(p2_sets, p1_sets, status)
            time_info = "Terminé" if status == "finished" else ("En cours" if status == "live" else (status_text or "À venir"))
            events.append({
                "id": event_id,
                "date": page_date,
                "scheduledAt": scheduled_at,
                "tour": tour,
                "eventType": event_type,
                "tournament": slug_label(tournament_slug),
                "tournamentSlug": tournament_slug,
                "level": level,
                "surface": surface_for(tournament_slug),
                "status": status,
                "round": "",
                "timeInfo": time_info,
                "sourceUrl": f"https://www.livescores.com{link['href']}",
                "p1": {"name": p1_name, "key": norm_name(p1_name), "members": p1_members, "cc": "", "serving": False, "sets": p1_sets,
                       "tiebreaks": p1_tb, "setsWon": p1_won, "game": p1_game,
                       "win": status == "finished" and p1_won > p2_won},
                "p2": {"name": p2_name, "key": norm_name(p2_name), "members": p2_members, "cc": "", "serving": False, "sets": p2_sets,
                       "tiebreaks": p2_tb, "setsWon": p2_won, "game": p2_game,
                       "win": status == "finished" and p2_won > p1_won},
            })
    events.sort(key=lambda event: (event["date"], event.get("scheduledAt") or "", event["tournament"], event["id"]))
    return events


def expected(rating_a, rating_b):
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def classify_style(profile):
    serve = profile.get("serve") or {}
    aces = serve.get("acesPerMatch")
    hold_proxy = serve.get("servicePointsWon")
    if aces is None or hold_proxy is None:
        return {"label": "Profil à confirmer", "confidence": "limited"}
    if aces >= 8 and hold_proxy >= 0.64:
        label = "Serveur offensif"
    elif hold_proxy >= 0.64:
        label = "Attaquant de fond"
    elif aces <= 4 and hold_proxy < 0.60:
        label = "Contreur / relanceur"
    else:
        label = "All-court équilibré"
    return {"label": label, "confidence": "estimated"}


def build_history():
    current_year = NOW.year
    ratings = defaultdict(lambda: 1500.0)
    surface_ratings = defaultdict(lambda: defaultdict(lambda: 1500.0))
    stats = defaultdict(lambda: {
        "name": "", "tour": "", "matches": 0, "wins": 0, "surface": defaultdict(lambda: [0, 0]),
        "recent": [], "rank": None, "lastDate": "", "aces": 0.0, "svpt": 0.0,
        "firstWon": 0.0, "secondWon": 0.0, "secondIn": 0.0, "statMatches": 0,
    })
    rows = []
    sources = []
    # Les dépôts originaux ont été retirés de GitHub en 2026. Ce miroir conserve
    # les fichiers, leur provenance et la licence originale de Jeff Sackmann.
    for tour, folder, prefix in (("ATP", "atp", "atp_matches"), ("WTA", "wta", "wta_matches")):
        for year in range(current_year - 4, current_year + 1):
            url = f"https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{folder}/{prefix}_{year}.csv"
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                sources.append(url)
                for row in csv.DictReader(io.StringIO(response.text)):
                    row["_tour"] = tour
                    rows.append(row)
            except Exception as exc:
                print(f"Historique indisponible {url}: {exc}", file=sys.stderr)

    rows.sort(key=lambda row: row.get("tourney_date", ""))
    for row in rows:
        winner, loser = row.get("winner_name", "").strip(), row.get("loser_name", "").strip()
        if not winner or not loser:
            continue
        wk, lk = norm_name(winner), norm_name(loser)
        surface = (row.get("surface") or "Unknown").title()
        date = row.get("tourney_date", "")
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"

        exp = expected(ratings[wk], ratings[lk])
        ratings[wk] += 24 * (1 - exp)
        ratings[lk] += 24 * (0 - (1 - exp))
        surf_exp = expected(surface_ratings[surface][wk], surface_ratings[surface][lk])
        surface_ratings[surface][wk] += 28 * (1 - surf_exp)
        surface_ratings[surface][lk] += 28 * (0 - (1 - surf_exp))

        for key, name, won, opponent, rank_prefix, stat_prefix in (
            (wk, winner, True, loser, "winner", "w"),
            (lk, loser, False, winner, "loser", "l"),
        ):
            item = stats[key]
            item["name"], item["tour"] = name, row["_tour"]
            item["matches"] += 1
            item["wins"] += int(won)
            item["surface"][surface][1] += 1
            item["surface"][surface][0] += int(won)
            item["rank"] = safe_int(row.get(f"{rank_prefix}_rank"), item["rank"])
            item["lastDate"] = max(item["lastDate"], date)
            item["recent"].append({"result": "W" if won else "L", "opponent": opponent, "opponentKey": norm_name(opponent),
                                   "date": date, "surface": surface, "tournament": row.get("tourney_name", "")})
            item["recent"] = item["recent"][-15:]
            svpt = safe_float(row.get(f"{stat_prefix}_svpt"))
            if svpt:
                first_in = safe_float(row.get(f"{stat_prefix}_1stIn"))
                first_won = safe_float(row.get(f"{stat_prefix}_1stWon"))
                second_won = safe_float(row.get(f"{stat_prefix}_2ndWon"))
                item["aces"] += safe_float(row.get(f"{stat_prefix}_ace"))
                item["svpt"] += svpt
                item["firstWon"] += first_won
                item["secondWon"] += second_won
                item["secondIn"] += max(svpt - first_in, 0)
                item["statMatches"] += 1

    profiles = {}
    for key, item in stats.items():
        surface_summary = {}
        for surface, (wins, matches) in item["surface"].items():
            surface_summary[surface] = {"wins": wins, "matches": matches, "winRate": round(wins / matches, 3) if matches else None,
                                        "elo": round(surface_ratings[surface][key])}
        recent = list(reversed(item["recent"]))
        recent10 = recent[:10]
        recent_wins = sum(match["result"] == "W" for match in recent10)
        serve = None
        if item["statMatches"]:
            serve = {
                "acesPerMatch": round(item["aces"] / item["statMatches"], 1),
                "servicePointsWon": round((item["firstWon"] + item["secondWon"]) / item["svpt"], 3) if item["svpt"] else None,
                "secondServeWon": round(item["secondWon"] / item["secondIn"], 3) if item["secondIn"] else None,
                "sample": item["statMatches"],
            }
        profile = {
            "name": item["name"], "tour": item["tour"], "rank": item["rank"], "lastDate": item["lastDate"],
            "elo": round(ratings[key]), "careerWindow": {"wins": item["wins"], "matches": item["matches"],
            "winRate": round(item["wins"] / item["matches"], 3) if item["matches"] else None},
            "surfaces": surface_summary, "recent": recent, "form": {"wins": recent_wins, "matches": len(recent10),
            "winRate": round(recent_wins / len(recent10), 3) if recent10 else None}, "serve": serve,
        }
        profile["style"] = classify_style(profile)
        profiles[key] = profile

    output = {
        "schemaVersion": 2,
        "generatedAt": NOW.isoformat(),
        "source": {"name": "Jeff Sackmann / Tennis Abstract (miroir d'archive)", "license": "CC BY-NC-SA 4.0",
                   "url": "https://github.com/Aneeshers/tennis-sackmann-archive", "years": [current_year - 4, current_year]},
        "method": {"eloK": 24, "surfaceEloK": 28, "recentWindow": 10},
        "profiles": profiles,
    }
    HISTORY_PATH.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Historique: {len(profiles)} profils depuis {len(rows)} matchs")


def limited_profile(name, tour):
    seed = int(hashlib.sha256(norm_name(name).encode()).hexdigest()[:8], 16)
    return {"name": name, "tour": tour, "rank": None, "elo": 1500 + seed % 41 - 20, "surfaces": {}, "recent": [],
            "form": {"wins": 0, "matches": 0, "winRate": None}, "serve": None,
            "style": {"label": "Profil à confirmer", "confidence": "limited"}}


def team_profile(player, profiles, tour):
    members = player.get("members") or [player["name"]]
    member_profiles = [profiles.get(norm_name(name)) for name in members]
    known = [profile for profile in member_profiles if profile]
    if not known:
        profile = limited_profile(player["name"], tour)
        profile.update({"teamSize": len(members), "profileCoverage": 0,
                        "style": {"label": "Équipe à confirmer", "confidence": "limited"}})
        return profile

    surfaces = {}
    surface_names = set().union(*(profile.get("surfaces", {}).keys() for profile in known))
    for surface in surface_names:
        entries = [profile.get("surfaces", {}).get(surface) for profile in known]
        entries = [entry for entry in entries if entry]
        matches = sum(entry.get("matches", 0) for entry in entries)
        wins = sum(entry.get("wins", 0) for entry in entries)
        elo_values = [entry.get("elo") for entry in entries if entry.get("elo") is not None]
        surfaces[surface] = {"wins": wins, "matches": matches,
                             "winRate": round(wins / matches, 3) if matches else None,
                             "elo": round(sum(elo_values) / len(elo_values)) if elo_values else None}

    recent = sorted((match for profile in known for match in profile.get("recent", [])[:10]),
                    key=lambda match: match.get("date", ""), reverse=True)[:10]
    recent_wins = sum(match.get("result") == "W" for match in recent)
    serve_values = [profile.get("serve") for profile in known if profile.get("serve")]
    serve = None
    if serve_values:
        def average(key):
            values = [item.get(key) for item in serve_values if item.get(key) is not None]
            return round(sum(values) / len(values), 3) if values else None
        serve = {"acesPerMatch": round(average("acesPerMatch") or 0, 1),
                 "servicePointsWon": average("servicePointsWon"),
                 "secondServeWon": average("secondServeWon"),
                 "sample": sum(item.get("sample", 0) for item in serve_values)}

    return {"name": player["name"], "tour": tour, "rank": None,
            "teamSize": len(members), "profileCoverage": len(known),
            "elo": round(sum(profile.get("elo", 1500) for profile in known) / len(known)),
            "surfaces": surfaces, "recent": recent,
            "form": {"wins": recent_wins, "matches": len(recent),
                     "winRate": round(recent_wins / len(recent), 3) if recent else None},
            "serve": serve, "style": {"label": "Équipe de double", "confidence": "estimated"}}


def attach_analysis(event, profiles):
    is_team = event["eventType"] == "doubles"
    p1 = team_profile(event["p1"], profiles, event["tour"]) if is_team else (profiles.get(event["p1"]["key"]) or limited_profile(event["p1"]["name"], event["tour"]))
    p2 = team_profile(event["p2"], profiles, event["tour"]) if is_team else (profiles.get(event["p2"]["key"]) or limited_profile(event["p2"]["name"], event["tour"]))
    surface = event["surface"]

    def rating(profile):
        surface_data = profile.get("surfaces", {}).get(surface, {})
        surface_elo = surface_data.get("elo")
        return round((surface_elo * 0.7 + profile.get("elo", 1500) * 0.3) if surface_elo else profile.get("elo", 1500)), surface_data

    r1, s1 = rating(p1)
    r2, s2 = rating(p2)
    base = expected(r1, r2)
    form1 = p1.get("form", {}).get("winRate")
    form2 = p2.get("form", {}).get("winRate")
    form_edge = ((form1 - form2) * 0.10) if form1 is not None and form2 is not None else 0
    p1_prob = min(0.92, max(0.08, base + form_edge))
    samples = min(s1.get("matches", 0), s2.get("matches", 0)) if surface != "Unknown" else 0
    if is_team:
        known_profiles = p1.get("profileCoverage", 0) + p2.get("profileCoverage", 0)
        expected_profiles = p1.get("teamSize", 2) + p2.get("teamSize", 2)
        has_both = known_profiles == expected_profiles
    else:
        has_both = event["p1"]["key"] in profiles and event["p2"]["key"] in profiles
    confidence = "high" if has_both and samples >= 15 else ("medium" if has_both else "limited")

    h2h1 = [match for match in p1.get("recent", []) if match.get("opponentKey") == event["p2"]["key"]]
    h2h_wins = sum(match["result"] == "W" for match in h2h1)
    h2h_total = len(h2h1)
    event["analysis"] = {
        "model": "AokBet Elo équipes v1" if is_team else "AokBet Elo v1",
        "teamMode": is_team,
        "generatedAt": NOW.isoformat(),
        "dataQuality": confidence,
        "surface": surface,
        "probability": {"p1": round(p1_prob * 100), "p2": 100 - round(p1_prob * 100)},
        "players": {
            "p1": {**p1, "blendedElo": r1, "surfaceStats": s1},
            "p2": {**p2, "blendedElo": r2, "surfaceStats": s2},
        },
        "h2h": {"p1Wins": h2h_wins, "p2Wins": h2h_total - h2h_wins, "matches": h2h_total, "scope": "15 derniers matchs connus par joueur"},
        "factors": [
            {"key": "elo", "label": "Elo adapté à la surface", "p1": r1, "p2": r2,
             "leader": "p1" if r1 > r2 else ("p2" if r2 > r1 else "tie")},
            {"key": "surface", "label": "Réussite sur la surface", "p1": s1.get("winRate"), "p2": s2.get("winRate"),
             "leader": "p1" if (s1.get("winRate") or 0) > (s2.get("winRate") or 0) else ("p2" if (s2.get("winRate") or 0) > (s1.get("winRate") or 0) else "tie")},
            {"key": "form", "label": "Forme sur 10 matchs", "p1": form1, "p2": form2,
             "leader": "p1" if (form1 or 0) > (form2 or 0) else ("p2" if (form2 or 0) > (form1 or 0) else "tie")},
        ],
        "notice": "Estimation statistique, pas une garantie de résultat.",
    }


def build_scores():
    pages = fetch_score_pages()
    if not pages:
        print("Aucune source score disponible; fichier existant conservé", file=sys.stderr)
        return
    events = parse_score_pages(pages)
    profiles = {}
    history_generated = None
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        profiles = history.get("profiles", {})
        history_generated = history.get("generatedAt")
    for event in events:
        attach_analysis(event, profiles)
    output = {
        "schemaVersion": 2,
        "generatedAt": NOW.isoformat(),
        "source": {"name": "Livescores", "url": "https://www.livescores.com/tennis/", "fetchedPages": len(pages)},
        "historyGeneratedAt": history_generated,
        "stats": {"events": len(events), "live": sum(e["status"] == "live" for e in events),
                  "scheduled": sum(e["status"] == "scheduled" for e in events),
                  "finished": sum(e["status"] == "finished" for e in events),
                  "analysed": sum("analysis" in e for e in events)},
        "events": events,
    }
    LIVE_PATH.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Scores: {len(events)} matchs, {output['stats']['analysed']} analysés")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scores"
    if mode == "history":
        build_history()
    elif mode == "scores":
        build_scores()
    else:
        raise SystemExit("Usage: update_tennis_data.py [scores|history]")
