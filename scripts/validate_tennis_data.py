#!/usr/bin/env python3
"""Validation légère des fichiers JSON publiés par les workflows."""

import json
import sys
from pathlib import Path


def fail(message):
    raise SystemExit(f"JSON tennis invalide: {message}")


path = Path(sys.argv[1] if len(sys.argv) > 1 else "tennis-live.json")
data = json.loads(path.read_text(encoding="utf-8"))

if data.get("schemaVersion") != 2:
    fail("schemaVersion doit valoir 2")

if path.name == "tennis-live.json":
    events = data.get("events")
    if not isinstance(events, list):
        fail("events doit être une liste")
    ids = set()
    for event in events:
        event_id = str(event.get("id", ""))
        if not event_id or event_id in ids:
            fail(f"identifiant absent ou dupliqué: {event_id}")
        ids.add(event_id)
        if event.get("tour") not in {"ATP", "WTA"}:
            fail(f"tour inconnu pour {event_id}")
        if event.get("eventType") not in {"singles", "doubles"}:
            fail(f"eventType inconnu pour {event_id}")
        if event.get("status") not in {"scheduled", "live", "finished"}:
            fail(f"statut inconnu pour {event_id}")
        if event.get("tournament", "").lower() in {"mens singles", "womens singles", "mens doubles", "womens doubles"}:
            fail(f"tournoi générique mal classé pour {event_id}")
        for side in ("p1", "p2"):
            player = event.get(side, {})
            if not player.get("name") or not isinstance(player.get("sets", []), list):
                fail(f"joueur invalide dans {event_id}")
        analysis = event.get("analysis")
        if analysis:
            probability = analysis.get("probability", {})
            if probability.get("p1", 0) + probability.get("p2", 0) != 100:
                fail(f"probabilités invalides dans {event_id}")
else:
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        fail("profiles doit être un objet non vide")

print(f"OK: {path} validé")
