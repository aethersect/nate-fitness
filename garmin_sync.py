"""
Exécuté par GitHub Actions (voir .github/workflows/garmin-sync.yml).
Ne stocke jamais de mot de passe : reprend une session déjà ouverte à partir
du secret GARMIN_TOKENS_B64 (généré une fois en local avec generate_tokens.py).

Écrit data/garmin-data.json avec 4 sections :
  - activities  : toutes les activités Garmin brutes (course, vélo, muscu, plongée, ...)
  - health      : un point par jour (FC repos, HRV, sommeil, Body Battery, stress, pas)
  - bodyComposition : un point par pesée (poids, % masse grasse, muscle, os, eau, IMC)
  - dives       : détail des plongées (Descent), extrait défensivement (voir NOTE plus bas)

NOTE SUR LA PLONGÉE : Garmin ne documente pas publiquement les noms exacts des
champs de profondeur/température dans le JSON d'activité. Ce script essaie
plusieurs noms de clés connus et, pour la toute première plongée rencontrée,
imprime les clés brutes disponibles dans les logs GitHub Actions. Si les
métriques de plongée ressortent vides après une vraie synchro, va voir ces
logs (onglet Actions → dernier run → étape "Run sync") pour ajuster les noms.

Chaque section est enveloppée dans son propre try/except : si un type de
donnée échoue (compte sans balance connectée, pas de plongée, etc.), le reste
de la synchro continue normalement.
"""
import base64
import io
import json
import os
import sys
import tarfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import garminconnect

TOKEN_DIR = Path("/tmp/garmin_tokens")
OUTPUT_FILE = Path("data/garmin-data.json")

ACTIVITY_DAYS_BACK = 14   # activités (course, vélo, muscu, plongée...)
HEALTH_DAYS_BACK = 14     # santé quotidienne (FC, HRV, sommeil, stress, pas)
BODYCOMP_DAYS_BACK = 90   # composition corporelle (moins fréquent, donc fenêtre large)

ACTIVITY_TYPE_MAP = {
    "running": "course",
    "trail_running": "course",
    "treadmill_running": "course",
    "cycling": "velo",
    "mountain_biking": "velo",
    "road_biking": "velo",
    "indoor_cycling": "velo",
    "lap_swimming": "natation",
    "open_water_swimming": "natation",
    "strength_training": "muscu",
    "indoor_cardio": "muscu",
}

# Toute activité dont le typeKey contient un de ces fragments est traitée comme plongée
DIVE_TYPE_FRAGMENTS = ["diving", "dive", "apnea"]


# ═══════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════

def restore_tokens():
    b64 = os.environ["GARMIN_TOKENS_B64"]
    buf = io.BytesIO(base64.b64decode(b64))
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        tar.extractall("/tmp")
    return TOKEN_DIR


# ═══════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════

def map_activity_type(activity):
    type_key = (activity.get("activityType") or {}).get("typeKey", "")
    name = (activity.get("activityName") or "").lower()
    if "hyrox" in name:
        return "hyrox"
    if any(frag in type_key for frag in DIVE_TYPE_FRAGMENTS):
        return "plongee"
    return ACTIVITY_TYPE_MAP.get(type_key, "autre")


def is_dive(activity):
    type_key = (activity.get("activityType") or {}).get("typeKey", "")
    return any(frag in type_key for frag in DIVE_TYPE_FRAGMENTS)


def to_activity_entry(activity):
    dist_m = activity.get("distance") or 0
    dur_s = activity.get("duration") or 0
    start = activity.get("startTimeLocal", "")[:10]
    return {
        "id": f"garmin_{activity['activityId']}",
        "date": start,
        "type": map_activity_type(activity),
        "typeKeyRaw": (activity.get("activityType") or {}).get("typeKey"),
        "distance_km": round(dist_m / 1000, 2) if dist_m else None,
        "duration_min": round(dur_s / 60) if dur_s else None,
        "calories": activity.get("calories"),
        "avgHr": activity.get("averageHR"),
        "maxHr": activity.get("maxHR"),
        "notes": activity.get("activityName") or "",
        "source": "garmin",
    }


def fetch_activities(client):
    end = datetime.now().date()
    start = end - timedelta(days=ACTIVITY_DAYS_BACK)
    raw = client.get_activities_by_date(start.isoformat(), end.isoformat())
    entries = [to_activity_entry(a) for a in raw]
    dive_activities = [a for a in raw if is_dive(a)]
    return entries, dive_activities


# ═══════════════════════════════════════════════════════════
# DIVES — defensive extraction, real field names unconfirmed
# ═══════════════════════════════════════════════════════════

def pick(d, *keys):
    """Return the first present, non-None value among several candidate keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def to_dive_entry(client, activity, logged_raw_keys):
    activity_id = activity["activityId"]
    try:
        detail = client.get_activity(activity_id)
    except Exception as e:
        print(f"  [plongee] impossible de charger le détail de l'activité {activity_id}: {e}")
        detail = {}

    summary = detail.get("summaryDTO") or {}

    # First dive ever seen this run: dump the raw key names to the Action log
    # so field names can be recalibrated precisely if metrics come back empty.
    if not logged_raw_keys[0]:
        print("  [plongee] Clés disponibles dans summaryDTO (calibrage) :")
        print("   ", sorted(summary.keys()))
        logged_raw_keys[0] = True

    max_depth = pick(summary, "maxDepth", "maxDepthMeters", "maxDepthInMeters")
    avg_depth = pick(summary, "averageDepth", "avgDepth", "averageDepthMeters")
    water_temp = pick(summary, "waterTemperature", "avgWaterTemperature", "minWaterTemperature")
    bottom_time_s = pick(summary, "bottomTime", "bottomTimeSeconds", "duration")
    surface_interval_s = pick(summary, "surfaceInterval", "surfaceIntervalSeconds")
    dive_number = pick(summary, "diveNumber", "diveNumberInSeries")

    start = (summary.get("startTimeLocal") or activity.get("startTimeLocal") or "")[:10]

    return {
        "id": f"garmin_dive_{activity_id}",
        "date": start,
        "diveNumber": dive_number,
        "maxDepthM": round(max_depth, 1) if isinstance(max_depth, (int, float)) else max_depth,
        "avgDepthM": round(avg_depth, 1) if isinstance(avg_depth, (int, float)) else avg_depth,
        "waterTempC": water_temp,
        "bottomTimeMin": round(bottom_time_s / 60, 1) if isinstance(bottom_time_s, (int, float)) else None,
        "surfaceIntervalMin": round(surface_interval_s / 60) if isinstance(surface_interval_s, (int, float)) else None,
        "notes": activity.get("activityName") or "",
        "source": "garmin",
    }


def fetch_dives(client, dive_activities):
    logged_raw_keys = [False]
    dives = []
    for a in dive_activities:
        try:
            dives.append(to_dive_entry(client, a, logged_raw_keys))
        except Exception as e:
            print(f"  [plongee] entrée ignorée (erreur) pour activité {a.get('activityId')}: {e}")
    return dives


# ═══════════════════════════════════════════════════════════
# DAILY HEALTH — resting HR, HRV, sleep, body battery, stress, steps
# ═══════════════════════════════════════════════════════════

def fetch_health_day(client, day_iso):
    entry = {"date": day_iso}

    # Daily summary: resting HR, steps, calories, avg stress, body battery (single call)
    try:
        stats = client.get_stats(day_iso) or {}
        entry["restingHr"] = pick(stats, "restingHeartRate")
        entry["steps"] = pick(stats, "totalSteps")
        entry["calories"] = pick(stats, "totalKilocalories")
        entry["avgStress"] = pick(stats, "averageStressLevel")
        entry["bodyBattery"] = pick(stats, "bodyBatteryMostRecentValue")
    except Exception as e:
        print(f"  [sante {day_iso}] get_stats a échoué: {e}")

    # 24h heart rate profile — avg/min/max (not just resting)
    try:
        hr = client.get_heart_rates(day_iso) or {}
        entry["minHr24h"] = pick(hr, "minHeartRate")
        entry["maxHr24h"] = pick(hr, "maxHeartRate")
        values = hr.get("heartRateValues") or []
        # heartRateValues is [[timestamp_ms, bpm_or_null], ...]; average the real samples
        samples = [v[1] for v in values if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float))]
        entry["avgHr24h"] = round(sum(samples) / len(samples)) if samples else pick(hr, "restingHeartRate")
    except Exception as e:
        print(f"  [sante {day_iso}] get_heart_rates a échoué: {e}")

    # Sleep — duration, score, and stage breakdown (deep/light/rem/awake)
    try:
        sleep = client.get_sleep_data(day_iso) or {}
        daily = sleep.get("dailySleepDTO") or {}
        sleep_seconds = pick(daily, "sleepTimeSeconds")
        entry["sleepHours"] = round(sleep_seconds / 3600, 2) if sleep_seconds else None
        scores = daily.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        entry["sleepScore"] = pick(overall, "value")

        deep_s = pick(daily, "deepSleepSeconds")
        light_s = pick(daily, "lightSleepSeconds")
        rem_s = pick(daily, "remSleepSeconds")
        awake_s = pick(daily, "awakeSleepSeconds")
        entry["sleepDeepMin"] = round(deep_s / 60) if isinstance(deep_s, (int, float)) else None
        entry["sleepLightMin"] = round(light_s / 60) if isinstance(light_s, (int, float)) else None
        entry["sleepRemMin"] = round(rem_s / 60) if isinstance(rem_s, (int, float)) else None
        entry["sleepAwakeMin"] = round(awake_s / 60) if isinstance(awake_s, (int, float)) else None
    except Exception as e:
        print(f"  [sante {day_iso}] get_sleep_data a échoué: {e}")

    # HRV
    try:
        hrv = client.get_hrv_data(day_iso) or {}
        summary = hrv.get("hrvSummary") or {}
        entry["hrv"] = pick(summary, "lastNightAvg", "weeklyAvg")
        entry["hrvStatus"] = summary.get("status")
    except Exception as e:
        print(f"  [sante {day_iso}] get_hrv_data a échoué: {e}")

    return entry


def fetch_health(client):
    end = datetime.now().date()
    days = [(end - timedelta(days=i)).isoformat() for i in range(HEALTH_DAYS_BACK)]
    out = []
    for d in days:
        out.append(fetch_health_day(client, d))
    return out


# ═══════════════════════════════════════════════════════════
# BODY COMPOSITION — Index S2 weigh-ins
# ═══════════════════════════════════════════════════════════

def fetch_body_composition(client):
    end = datetime.now().date()
    start = end - timedelta(days=BODYCOMP_DAYS_BACK)
    try:
        raw = client.get_body_composition(start.isoformat(), end.isoformat()) or {}
    except Exception as e:
        print(f"  [composition] get_body_composition a échoué: {e}")
        return []

    entries = []
    for item in raw.get("dateWeightList") or []:
        weight_g = item.get("weight")
        cal_date = item.get("calendarDate") or (item.get("date") and
                   datetime.utcfromtimestamp(item["date"] / 1000).date().isoformat())
        entries.append({
            "date": cal_date,
            "weightKg": round(weight_g / 1000, 1) if weight_g else None,
            "bmi": item.get("bmi"),
            "bodyFatPercent": item.get("bodyFat"),
            "bodyWaterPercent": item.get("bodyWater"),
            "muscleMassKg": round(item["muscleMass"] / 1000, 1) if item.get("muscleMass") else None,
            "boneMassKg": round(item["boneMass"] / 1000, 2) if item.get("boneMass") else None,
            "visceralFat": item.get("visceralFat"),
            "metabolicAge": item.get("metabolicAge"),
            "source": "garmin",
        })
    return entries


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    token_dir = restore_tokens()
    client = garminconnect.Garmin()
    client.login(tokenstore=str(token_dir))

    print("→ Activités...")
    activities, dive_activities = fetch_activities(client)
    print(f"  {len(activities)} activités, dont {len(dive_activities)} plongée(s)")

    print("→ Plongées (détail)...")
    dives = fetch_dives(client, dive_activities)

    print("→ Santé quotidienne...")
    health = fetch_health(client)

    print("→ Composition corporelle...")
    body_composition = fetch_body_composition(client)

    payload = {
        "syncedAt": datetime.utcnow().isoformat() + "Z",
        "activities": activities,
        "dives": dives,
        "health": health,
        "bodyComposition": body_composition,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n{len(activities)} activités / {len(dives)} plongées / "
          f"{len(health)} jours santé / {len(body_composition)} pesées "
          f"→ écrit dans {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Erreur de synchro Garmin :", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
