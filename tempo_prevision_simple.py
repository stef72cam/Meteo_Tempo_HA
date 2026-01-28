#!/usr/bin/env python3
import sys
import json
import base64
import datetime as dt
import math
from collections import defaultdict
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from datetime import datetime

CLIENT_ID = "REMPLIR ICI"
CLIENT_SECRET = "REMPLIR ICI"
ENABLE_METEO = True          #Inscrire FALSE si non utilisé
ENABLE_DEBUG = False         #Inscrire TRUE si utilisé


TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
CONS_SHORT_URL = "https://digital.iservices.rte-france.com/open_api/consumption/v1/short_term"
CONS_WEEK_URL = "https://digital.iservices.rte-france.com/open_api/consumption/v1/weekly_forecasts"
GEN_FORECAST_URL = "https://digital.iservices.rte-france.com/open_api/generation_forecast/v3/forecasts"
CONS_ANNUAL_URL = "https://digital.iservices.rte-france.com/open_api/consumption/v1/annual_forecasts"

Z_WHITE_EXPANSION = 0.30         # décale le seuil bleu/blanc vers le bas
WHITE_MIDZONE_BOOST = 1.2        # boost du score blanc quand Z est médian
BLUE_MIDZONE_PENALTY = 0.85      # petite pénalité sur bleu dans la zone médiane
C_MEAN = 46050.0
C_STD = 2160.0

RED_TOTAL = 22
WHITE_TOTAL = 43
BLUE_TOTAL = 300

SEASON_LENGTH = 151  # 01/11 -> 31/03
TARGET_RED_DENSITY = RED_TOTAL / SEASON_LENGTH   # densité "normale" de rouges


# Valeurs par défaut (si aucun argument n'est passé)
RED_REMAINING = RED_TOTAL
WHITE_REMAINING = WHITE_TOTAL
BLUE_REMAINING = BLUE_TOTAL
REAL_J1 = None

# 1) Lecture des rouges / blancs restants
try:
    if len(sys.argv) >= 2:
        RED_REMAINING = int(sys.argv[1])
    if len(sys.argv) >= 3:
        WHITE_REMAINING = int(sys.argv[2])
except Exception:
    RED_REMAINING = RED_TOTAL
    WHITE_REMAINING = WHITE_TOTAL

# 2) Lecture éventuelle du bleu restant ET/OU de la couleur réelle J+1
if len(sys.argv) >= 4:
    arg3 = sys.argv[3].strip().lower()
    try:
        BLUE_REMAINING = int(arg3)
        if len(sys.argv) >= 5:
            arg4 = sys.argv[4].strip().lower()
            if arg4 in ("bleu", "blanc", "rouge"):
                REAL_J1 = arg4
    except ValueError:
        if arg3 in ("bleu", "blanc", "rouge"):
            REAL_J1 = arg3


def base_decision_from_probs(pB: float, pW: float, pR: float) -> str:
    if pB >= pW and pB >= pR:
        return "bleu"
    if pR >= pW:
        return "rouge"
    return "blanc"


def http_post_token():
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    data = urlencode({"grant_type": "client_credentials"}).encode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = Request(TOKEN_URL, data=data, headers=headers)
    with urlopen(req, timeout=20) as r:
        return json.load(r)


def get_token():
    return http_post_token()["access_token"]


def http_get_json(url, token, params=None):
    if params:
        url = url + "?" + urlencode(params)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as r:
        return json.load(r)


def get_annual(token):
    """
    Récupère les prévisions annuelles.

    - Du 01/01 au 30/11 : appel sans paramètres.
    - Décembre : concat N + N+1.
    - Janvier : concat N-1 + N.
    """
    today = dt.date.today()
    tz = dt.timezone(dt.timedelta(hours=1))  # Europe/Paris hiver

    def _fetch_year(year: int):
        start_dt = dt.datetime(year, 1, 1, 0, 0, 0, tzinfo=tz)
        end_dt = dt.datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
        params = {"start_date": start_dt.isoformat(), "end_date": end_dt.isoformat()}
        return http_get_json(CONS_ANNUAL_URL, token, params)

    if today.month == 12:
        y = today.year
        a = _fetch_year(y)
        b = _fetch_year(y + 1)
        forecasts = []
        forecasts += a.get("annual_forecasts", []) if isinstance(a, dict) else []
        forecasts += b.get("annual_forecasts", []) if isinstance(b, dict) else []
        return {"annual_forecasts": forecasts}

    if today.month == 1:
        y = today.year
        a = _fetch_year(y - 1)
        b = _fetch_year(y)
        forecasts = []
        forecasts += a.get("annual_forecasts", []) if isinstance(a, dict) else []
        forecasts += b.get("annual_forecasts", []) if isinstance(b, dict) else []
        return {"annual_forecasts": forecasts}

    return http_get_json(CONS_ANNUAL_URL, token)


def _quantile(values, p: float) -> float:
    if not values:
        raise ValueError("Liste vide pour le calcul de quantile")
    vals = sorted(values)
    n = len(vals)
    if n == 1:
        return vals[0]
    pos = p * (n - 1)
    i_low = int(math.floor(pos))
    i_high = int(math.ceil(pos))
    if i_low == i_high:
        return vals[i_low]
    frac = pos - i_low
    return vals[i_low] + frac * (vals[i_high] - vals[i_low])


def _extract_load_value(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val


def build_daily_data_from_annual_forecasts(annual_json):
    daily_data = []
    forecasts = annual_json.get("annual_forecasts", [])
    if not forecasts:
        return daily_data

    for year_block in forecasts:
        if not isinstance(year_block, dict):
            continue

        weekly_values = year_block.get("values", [])
        if not isinstance(weekly_values, list):
            continue

        weekly_values = sorted(weekly_values, key=lambda w: w.get("start_date", ""))
        block_data = []

        for w in weekly_values:
            sd = w.get("start_date")
            if not sd:
                continue
            try:
                start_dt = dt.datetime.fromisoformat(sd.replace("Z", "+00:00"))
            except Exception:
                continue

            start_date = start_dt.date()

            mean_load = _extract_load_value(w.get("average_load_monday_to_sunday"))
            if mean_load is None:
                mean_load = _extract_load_value(w.get("average_load_saturday_to_friday"))

            if mean_load is None:
                weekly_min = _extract_load_value(w.get("weekly_minimum"))
                weekly_max = _extract_load_value(w.get("weekly_maximum"))
                if weekly_min is not None and weekly_max is not None:
                    mean_load = 0.5 * (weekly_min + weekly_max)

            if mean_load is None:
                continue

            for k in range(7):
                d = start_date + dt.timedelta(days=k)
                block_data.append({"date": d.isoformat(), "c_net_mean": float(mean_load)})

        try:
            start_block = dt.datetime.fromisoformat(year_block["start_date"].replace("Z", "+00:00")).date()
            end_block = dt.datetime.fromisoformat(year_block["end_date"].replace("Z", "+00:00")).date()
            block_data = [
                row for row in block_data
                if start_block <= dt.date.fromisoformat(row["date"]) < end_block
            ]
        except Exception:
            pass

        daily_data.extend(block_data)

    by_date = {}
    for row in daily_data:
        by_date[row["date"]] = row

    return sorted(by_date.values(), key=lambda x: x["date"])


def compute_z(c_net):
    return (c_net - C_MEAN) / C_STD


def compute_z_rte_like(target_date, c_net_today, daily_data, window_days: int = 365, return_debug: bool = False):
    if isinstance(target_date, dt.date) is False:
        target_date = datetime.strptime(str(target_date), "%Y-%m-%d").date()

    history_vals = []
    used_first = None
    used_last = None

    for entry in daily_data:
        try:
            d = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        except Exception:
            continue

        if d >= target_date:
            break

        if (target_date - d).days <= window_days:
            v = entry.get("c_net_mean")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v > 0:
                history_vals.append(v)
                if used_first is None:
                    used_first = d
                used_last = d

    debug = {
        "window_days": window_days,
        "window_end_inclusive": target_date.isoformat(),
        "window_start_inclusive": (target_date - dt.timedelta(days=window_days)).isoformat(),
        "history_count": len(history_vals),
        "history_first_date": used_first.isoformat() if used_first else None,
        "history_last_date": used_last.isoformat() if used_last else None,
    }

    if len(history_vals) < 3:
        m = sum(history_vals) / len(history_vals) if history_vals else c_net_today
        var = (
            sum((x - m) ** 2 for x in history_vals) / max(1, len(history_vals) - 1)
            if len(history_vals) > 1 else 1.0
        )
        std = math.sqrt(var) or 1.0
        z = (c_net_today - m) / std
        debug["method"] = "fallback_std_small_sample"
        debug["mean"] = m
        debug["std"] = std
        return (z, debug) if return_debug else z

    q40 = _quantile(history_vals, 0.4)
    q80 = _quantile(history_vals, 0.8)

    denom = q80 - q40
    if abs(denom) < 1e-6:
        denom = 1e-6

    z = (c_net_today - q40) / denom
    debug["method"] = "rte_like_quantiles"
    debug["q40"] = q40
    debug["q80"] = q80
    debug["denom"] = denom

    return (z, debug) if return_debug else z


def group_daily_avg(values):
    by_day = defaultdict(list)
    for v in values:
        if not isinstance(v, dict):
            continue
        val = v.get("value")
        sd = v.get("start_date")
        if val is None or sd is None:
            continue
        try:
            d = dt.datetime.fromisoformat(sd.replace("Z", "+00:00")).date()
        except Exception:
            continue
        try:
            by_day[d].append(float(val))
        except Exception:
            continue
    return {d: sum(lst) / len(lst) for d, lst in by_day.items() if lst}


def get_generation(token):
    params = {"type": "D-1,D-2,D-3"}
    data = http_get_json(GEN_FORECAST_URL, token, params)

    per_ts = {}
    blocks = data.get("forecasts", [])
    if isinstance(blocks, dict):
        blocks = [blocks]

    for block in blocks:
        if not isinstance(block, dict):
            continue
        vals = block.get("values", [])
        if isinstance(vals, dict):
            vals = [vals]
        if not isinstance(vals, list):
            continue

        for v in vals:
            if not isinstance(v, dict):
                continue
            sd = v.get("start_date")
            val = v.get("value")
            if sd is None or val is None:
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            per_ts[sd] = per_ts.get(sd, 0.0) + fv

    merged = [{"start_date": sd, "value": fv} for sd, fv in per_ts.items()]
    return group_daily_avg(merged)


def get_short_term(token):
    data = http_get_json(CONS_SHORT_URL, token)
    values = []
    blocks = data.get("short_term", [])
    if isinstance(blocks, dict):
        blocks = [blocks]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        vals = block.get("values", [])
        if isinstance(vals, dict):
            vals = [vals]
        if isinstance(vals, list):
            values.extend([v for v in vals if isinstance(v, dict)])
    return group_daily_avg(values)


def get_weekly(token):
    data = http_get_json(CONS_WEEK_URL, token)
    values = []
    blocks = data.get("weekly_forecasts", [])
    if isinstance(blocks, dict):
        blocks = [blocks]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        vals = block.get("values", [])
        if isinstance(vals, dict):
            vals = [vals]
        if isinstance(vals, list):
            values.extend([v for v in vals if isinstance(v, dict)])
    return group_daily_avg(values)


def tempo_year_start(d):
    s = dt.date(d.year, 9, 1)
    return s if d >= s else dt.date(d.year - 1, 9, 1)


def tempo_year_end_inclusive(d: dt.date) -> dt.date:
    start = tempo_year_start(d)
    return dt.date(start.year + 1, 8, 31)


def day_index(d):
    return (d - tempo_year_start(d)).days + 1


def thresholds(j, red_rem, white_rem):
    stock_br = red_rem + white_rem
    s_br = 4.00 - 0.015 * j - 0.026 * stock_br
    s_r = 3.15 - 0.010 * j - 0.031 * red_rem
    return s_br, s_r


def is_french_public_holiday(d: dt.date) -> bool:
    year = d.year
    fixed = {
        dt.date(year, 1, 1),
        dt.date(year, 5, 1),
        dt.date(year, 5, 8),
        dt.date(year, 7, 14),
        dt.date(year, 8, 15),
        dt.date(year, 11, 1),
        dt.date(year, 11, 11),
        dt.date(year, 12, 25),
    }

    a = year // 100
    b = year % 100
    c = (3 * (a + 25)) // 4
    d0 = (3 * (a + 25)) % 4
    e = (8 * (a + 11)) // 25
    f = (5 * a + b) % 19
    g = (19 * f + c - e) % 30
    h = (f + 11 * g) // 319
    j = (60 * (5 - d0) + b) % 4
    k = (60 * (5 - d0) + b) // 4
    m = (2 * j + k - g - h) % 7
    n = (g - h + m + 114) // 31
    p = (g - h + m + 114) % 31
    easter = dt.date(year, n, p + 1)

    easter_monday = easter + dt.timedelta(days=1)
    ascension = easter + dt.timedelta(days=39)
    pentecost_monday = easter + dt.timedelta(days=50)

    movable = {easter_monday, ascension, pentecost_monday}
    return d in fixed or d in movable


def allowed(color, d):
    wd = d.weekday()
    month = d.month
    if color == "rouge":
        if wd >= 5 or is_french_public_holiday(d):
            return False
        if month not in (11, 12, 1, 2, 3):
            return False
        return True
    if color == "blanc":
        return wd != 6
    return True


def red_season_bounds(d: dt.date):
    if d.month >= 11:
        start = dt.date(d.year, 11, 1)
        end = dt.date(d.year + 1, 3, 31)
    else:
        start = dt.date(d.year - 1, 11, 1)
        end = dt.date(d.year, 3, 31)
    return start, end


def count_red_eligible_days_left(start_date: dt.date, end_date: dt.date) -> int:
    if end_date < start_date:
        return 0
    n = 0
    d = start_date
    while d <= end_date:
        if allowed("rouge", d):
            n += 1
        d += dt.timedelta(days=1)
    return n


def red_target_used_fraction(d: dt.date) -> float:
    start, end = red_season_bounds(d)
    y = start.year
    d1 = dt.date(y, 12, 10)
    d2 = dt.date(y + 1, 1, 31)
    d3 = dt.date(y + 1, 2, 20)
    d4 = end

    def lerp(a_date, b_date, a_val, b_val):
        if d <= a_date:
            return a_val
        if d >= b_date:
            return b_val
        t = (d - a_date).days / max(1, (b_date - a_date).days)
        return a_val + t * (b_val - a_val)

    if d <= d1:
        return lerp(start, d1, 0.00, 0.05)
    if d <= d2:
        return lerp(d1, d2, 0.05, 0.65)
    if d <= d3:
        return lerp(d2, d3, 0.65, 0.95)
    return lerp(d3, d4, 0.95, 1.00)


def red_floor_from_z(z: float) -> float:
    if z is None:
        return 0.0
    if z < 1:
        return 0.00
    if z < 1.10:
        return 0.10
    if z < 1.20:
        return 0.12
    if z < 1.25:
        return 0.15
    if z < 1.30:
        return 0.18
    return 0.22


# --- Indicateur national type EDF (degrés-jours) via Open-Meteo ---
T_BASE = 17.0


AIRPORTS = [
    ("Abbeville", 50.1360, 1.8340, 1.0),
    ("Bale_Mulhouse", 47.5896, 7.5299, 2.0),
    ("Bordeaux_Merignac", 44.8283, -0.7156, 4.0),
    ("Boulogne", 50.6589, 1.6246, 1.0),
    ("Bourges", 47.0581, 2.3703, 4.2),
    ("Bourg_St_Maurice", 45.6170, 6.7680, 2.75),
    ("Brest_Guipavas", 48.4479, -4.4185, 4.2),
    ("Caen_Carpiquet", 49.1733, -0.4500, 2.5),
    ("Clermont_Ferrand_Aulnat", 45.7867, 3.1692, 2.75),
    ("Dijon_Longvic", 47.2689, 5.0883, 1.0),
    ("Le_Luc_Le_Cannet", 43.3847, 6.3872, 1.2),
    ("Lille_Lesquin", 50.5619, 3.0894, 3.0),
    ("Limoges_Bellegarde", 45.8614, 1.1794, 3.2),
    ("Lyon_St_Exupery", 45.7256, 5.0811, 5.5),
    ("Marseille_Marignane", 43.4372, 5.2150, 2.4),
    ("Montpellier_Frejorgues", 43.5762, 3.9630, 1.6),
    ("Nancy_Essey", 48.6921, 6.2303, 3.0),
    ("Nantes_Atlantique", 47.1532, -1.6107, 4.2),
    ("Nevers_Marzy", 46.9990, 3.1130, 1.5),
    ("Nice_Cote_dAzur", 43.6653, 7.2150, 3.6),
    ("Nimes_Courbessac", 43.8564, 4.4050, 2.4),
    ("Orange_Caritat", 44.1405, 4.8667, 1.2),
    ("Paris_Montsouris", 48.8218, 2.3376, 11.25),
    ("Perpignan_Rivesaltes", 42.7404, 2.8707, 1.6),
    ("Rennes_St_Jacques", 48.0695, -1.7348, 4.2),
    ("Saint_Auban", 44.0583, 5.9917, 1.2),
    ("Strasbourg_Entzheim", 48.5383, 7.6282, 1.0),
    ("Tarbes_Lourdes", 43.1786, -0.0064, 4.0),
    ("Toulouse_Blagnac", 43.6306, 1.3638, 1.6),
    ("Tours_Parcay_Meslay", 47.4322, 0.7276, 4.2),
    ("Trappes", 48.7742, 1.9936, 11.25),
    ("Troyes_Barberey", 48.3239, 4.0179, 1.5),
]
def fetch_open_meteo_daily_tmin_tmax(latitudes, longitudes, forecast_days=8):
    base_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": ",".join(f"{x:.4f}" for x in latitudes),
        "longitude": ",".join(f"{x:.4f}" for x in longitudes),
        "daily": "temperature_2m_min,temperature_2m_max",
        "timezone": "Europe/Paris",
        "forecast_days": str(forecast_days),
    }
    url = f"{base_url}?{urlencode(params)}"
    with urlopen(url, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))

    # si Open-Meteo renvoie une erreur explicite
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"Open-Meteo error: {data.get('reason')}")

    return data


def compute_national_tmoy(today: dt.date, offsets=range(1, 4)):
    """
    Retourne {date: Tmoy_nationale} avec Tmoy=(Tmin+Tmax)/2 pondéré par station.
    """
    latitudes = [a[1] for a in AIRPORTS]
    longitudes = [a[2] for a in AIRPORTS]
    poids_total = sum(a[3] for a in AIRPORTS)

    data = fetch_open_meteo_daily_tmin_tmax(latitudes, longitudes, forecast_days=max(offsets) + 1)

    # Open-Meteo peut renvoyer dict (mono) ou liste (multi)
    if isinstance(data, dict):
        data = [data]

    acc = {off: 0.0 for off in offsets}
    used = {off: 0.0 for off in offsets}

    for station_data, (_, _, _, poids) in zip(data, AIRPORTS):
        daily = station_data.get("daily", {})
        tmins = daily.get("temperature_2m_min", [])
        tmaxs = daily.get("temperature_2m_max", [])
        if len(tmins) < (max(offsets) + 1) or len(tmaxs) < (max(offsets) + 1):
            continue

        for off in offsets:
            tmoy = (tmins[off] + tmaxs[off]) / 2.0
            acc[off] += tmoy * poids
            used[off] += poids

    out = {}
    for off in offsets:
        d = today + dt.timedelta(days=off)
        out[d] = round(acc[off] / used[off], 2) if used[off] > 0 else None
    return out

def meteo_strength_from_delta(delta_T: float | None) -> float:
    """
    Transforme delta_T (T_future - T_norm) en intensité de shift sur les probas.
    Valeurs petites et bornées (pas de magie).
    """
    if delta_T is None:
        return 0.0
    a = abs(delta_T)
    if a < 0.5:
        return 0.00
    if a < 1.5:
        return 0.03
    if a < 3.0:
        return 0.06
    return 0.09

def apply_meteo_shift_probs(pB: float, pW: float, pR: float, delta_T: float | None):
    """
    Applique un biais météo en CHAÎNE:
    - doux (delta_T > 0): R->W puis W->B
    - froid (delta_T < 0): B->W puis W->R
    Jamais de transfert direct rouge<->bleu.
    """
    if any(v is None for v in (pB, pW, pR)) or delta_T is None:
        return pB, pW, pR, 0.0

    s = meteo_strength_from_delta(delta_T)
    if s <= 0:
        return pB, pW, pR, 0.0

    # Copie
    b, w, r = float(pB), float(pW), float(pR)

    if delta_T > 0:
        # doux : rouge -> blanc
        x1 = min(s, r)
        r -= x1
        w += x1
        # puis blanc -> bleu
        x2 = min(s, w)
        w -= x2
        b += x2
        applied = x1 + x2
    else:
        # froid : bleu -> blanc
        x1 = min(s, b)
        b -= x1
        w += x1
        # puis blanc -> rouge
        x2 = min(s, w)
        w -= x2
        r += x2
        applied = x1 + x2

    # renormalisation
    tot = b + w + r
    if tot > 0:
        b, w, r = b / tot, w / tot, r / tot

    return b, w, r, round(applied, 4)


def rte_norm_temp(d: dt.date) -> float | None:
    """
    Norme RTE 1991-2020 pour le jour calendaire.
    """
    key = (d.month, d.day)
    NORMS = {
        (11, 1): 11.14,
        (11, 2): 10.08,
        (11, 3): 10.92,
        (11, 4): 10.26,
        (11, 5): 9.69,
        (11, 6): 9.16,
        (11, 7): 9.16,
        (11, 8): 9.63,
        (11, 9): 9.71,
        (11, 10): 9.51,
        (11, 11): 9.59,
        (11, 12): 9.43,
        (11, 13): 9.33,
        (11, 14): 8.63,
        (11, 15): 7.74,
        (11, 16): 7.92,
        (11, 17): 7.61,
        (11, 18): 7.57,
        (11, 19): 7.44,
        (11, 20): 7.30,
        (11, 21): 7.18,
        (11, 22): 7.01,
        (11, 23): 6.89,
        (11, 24): 6.84,
        (11, 25): 7.16,
        (11, 26): 6.93,
        (11, 27): 6.60,
        (11, 28): 6.36,
        (11, 29): 6.53,
        (11, 30): 6.73,
        (12, 1): 6.53,
        (12, 2): 6.41,
        (12, 3): 6.42,
        (12, 4): 6.63,
        (12, 5): 6.79,
        (12, 6): 6.20,
        (12, 7): 5.84,
        (12, 8): 5.96,
        (12, 9): 5.72,
        (12, 10): 5.35,
        (12, 11): 5.26,
        (12, 12): 5.37,
        (12, 13): 5.28,
        (12, 14): 4.72,
        (12, 15): 4.62,
        (12, 16): 5.27,
        (12, 17): 5.48,
        (12, 18): 5.59,
        (12, 19): 5.75,
        (12, 20): 5.45,
        (12, 21): 5.51,
        (12, 22): 5.61,
        (12, 23): 5.41,
        (12, 24): 5.49,
        (12, 25): 5.39,
        (12, 26): 4.85,
        (12, 27): 4.71,
        (12, 28): 4.58,
        (12, 29): 4.61,
        (12, 30): 4.96,
        (12, 31): 4.81,
        (1, 1): 4.73,
        (1, 2): 5.10,
        (1, 3): 4.88,
        (1, 4): 4.57,
        (1, 5): 4.79,
        (1, 6): 5.12,
        (1, 7): 4.35,
        (1, 8): 4.31,
        (1, 9): 4.85,
        (1, 10): 4.92,
        (1, 11): 4.83,
        (1, 12): 4.30,
        (1, 13): 4.54,
        (1, 14): 4.71,
        (1, 15): 4.59,
        (1, 16): 4.85,
        (1, 17): 5.31,
        (1, 18): 5.23,
        (1, 19): 5.70,
        (1, 20): 5.64,
        (1, 21): 5.55,
        (1, 22): 5.73,
        (1, 23): 5.38,
        (1, 24): 5.00,
        (1, 25): 4.80,
        (1, 26): 4.49,
        (1, 27): 4.49,
        (1, 28): 4.91,
        (1, 29): 4.87,
        (1, 30): 4.68,
        (1, 31): 4.57,
        (2, 1): 5.01,
        (2, 2): 5.13,
        (2, 3): 5.57,
        (2, 4): 5.80,
        (2, 5): 6.17,
        (2, 6): 6.34,
        (2, 7): 5.96,
        (2, 8): 6.11,
        (2, 9): 5.77,
        (2, 10): 5.19,
        (2, 11): 5.27,
        (2, 12): 5.54,
        (2, 13): 4.76,
        (2, 14): 4.76,
        (2, 15): 4.90,
        (2, 16): 4.73,
        (2, 17): 5.04,
        (2, 18): 5.31,
        (2, 19): 5.24,
        (2, 20): 5.40,
        (2, 21): 5.30,
        (2, 22): 5.46,
        (2, 23): 6.00,
        (2, 24): 6.16,
        (2, 25): 6.27,
        (2, 26): 6.39,
        (2, 27): 6.52,
        (2, 28): 7.03,
        (3, 1): 6.90,
        (3, 2): 6.85,
        (3, 3): 6.78,
        (3, 4): 6.65,
        (3, 5): 6.61,
        (3, 6): 6.97,
        (3, 7): 7.46,
        (3, 8): 7.83,
        (3, 9): 7.96,
        (3, 10): 8.03,
        (3, 11): 8.45,
        (3, 12): 8.47,
        (3, 13): 8.30,
        (3, 14): 8.65,
        (3, 15): 9.04,
        (3, 16): 9.32,
        (3, 17): 9.24,
        (3, 18): 9.32,
        (3, 19): 9.12,
        (3, 20): 9.34,
        (3, 21): 9.39,
        (3, 22): 9.23,
        (3, 23): 9.38,
        (3, 24): 9.63,
        (3, 25): 9.51,
        (3, 26): 9.23,
        (3, 27): 9.26,
        (3, 28): 9.23,
        (3, 29): 9.37,
        (3, 30): 9.35,
        (3, 31): 9.90,
    }

    # Année bissextile : si 29/02 absent, on prend 28/02
    if key == (2, 29):
        return NORMS.get((2, 29), NORMS.get((2, 28)))

    return NORMS.get(key)


def shrink_probs(pB: float, pW: float, pR: float, lam: float):
    u = 1.0 / 3.0
    pB2 = (1 - lam) * pB + lam * u
    pW2 = (1 - lam) * pW + lam * u
    pR2 = (1 - lam) * pR + lam * u
    s = pB2 + pW2 + pR2
    if s <= 0:
        return u, u, u
    return pB2 / s, pW2 / s, pR2 / s


def mask_probs_by_calendar(d, pB: float, pW: float, pR: float):
    wd = d.weekday()
    if wd == 6:
        return 1.0, 0.0, 0.0
    if not allowed("rouge", d):
        pR = 0.0
    if not allowed("blanc", d):
        pW = 0.0
    if wd == 5:
        pR = 0.0
    s = pB + pW + pR
    if s <= 0:
        if wd == 5:
            return 0.65, 0.35, 0.0
        return 0.8, 0.2, 0.0
    return pB / s, pW / s, pR / s


def decide_color_with_probs(d, z, j, red_rem, white_rem, blue_rem):
    s_br, s_r = thresholds(j, red_rem, white_rem)
    wd = d.weekday()

    # Seuils ajustés : on élargit la zone blanche vers le bas
    s_br_adj = s_br - Z_WHITE_EXPANSION
    s_r_adj = s_r

    # Dimanche 100% bleu
    if wd == 6:
        return "bleu", red_rem, white_rem, blue_rem, 1.0, 0.0, 0.0

    # Couleurs autorisées
    allowed_red = allowed("rouge", d) and red_rem > 0
    allowed_white = allowed("blanc", d) and white_rem > 0

    # Scores de départ : on réduit un peu l'avantage natif du bleu,
    # et on donne un petit socle à blanc.
    score_b = 0.9
    score_w = 0.1
    score_r = 0.0

    month, day = d.month, d.day
    coeur_hiver = (
        (month == 12 and day >= 10) or
        (month == 1) or
        (month == 2 and day <= 20)
    )

    if coeur_hiver and wd < 5 and not is_french_public_holiday(d):
        score_b = 0.6
        score_w = 0.25
        score_r = 0.15

    # Scoring brut selon Z (avec seuils ajustés)
    if z is not None:
        if z < s_br_adj:
            # Zone clairement bleue
            score_b += (s_br_adj - z) * 0.8
            if allowed_white:
                score_w += max(0.0, z - (s_br_adj - 1.5)) * 0.4

        elif z < s_r_adj:
            # Zone médiane : blanc probable, bleu encore en lice
            delta_bw = z - s_br_adj

            # Bleu gagne un peu quand on est proche de s_br_adj, mais moins qu'avant
            score_b += max(0.0, s_r_adj - z) * 0.25

            # Blanc a un socle plus élevé et une pente un peu plus forte
            if allowed_white:
                score_w += 0.45 + delta_bw * 0.85

            # Rouge commence à apparaître, mais plus doucement
            if allowed_red:
                score_r += max(0.0, z - (s_r_adj - 0.5)) * 0.5

        else:
            # Zone tendue : blanc/rouge en concurrence, bleu décroît
            if allowed_white:
                score_w += 0.4 + max(0.0, z - s_br_adj) * 0.55
            if allowed_red:
                score_r += 0.35 + max(0.0, z - s_r_adj) * 1.0
            score_b += max(0.0, (s_r_adj + 1.0) - z) * 0.25

        # Effet B : boost BLANC + légère pénalité BLEU en zone médiane
        if s_br_adj <= z < s_r_adj and allowed_white:
            score_w *= WHITE_MIDZONE_BOOST
            score_b *= BLUE_MIDZONE_PENALTY

        # BOOST BLANC hors cœur d'hiver quand Z est modéré
        if (
            not coeur_hiver
            and 0.3 <= z <= 1
            and score_r < 0.15  # rouge quasi absent
            and allowed_white
        ):
            if score_w < score_b:
                score_w += (score_b - score_w) * 0.35

    # Respect strict des contraintes calendrier + stock
    if not allowed_red:
        score_r = 0.0
    if not allowed_white:
        score_w = 0.0

    # Filtre "EDF-like" sur le rouge : saison + historique d'usage
    edf_red_factor = 1.0

    # Profil saisonnier des jours rouges (basé sur l'historique EDF)
    if d.month == 11:
        edf_red_factor *= 0.7
    elif d.month == 12:
        if d.day < 10:
            edf_red_factor *= 0.5
        elif d.day < 20:
            edf_red_factor *= 0.7
        else:
            edf_red_factor *= 1
    elif d.month in (1, 2):
        edf_red_factor *= 1.05
    elif d.month == 3:
        if d.day <= 15:
            edf_red_factor *= 0.7
        else:
            edf_red_factor *= 0.4

 
    
    # Ajustement en fonction du stock "attendu" de rouges (courbe cible non linéaire)
    target_used_frac = red_target_used_fraction(d)
    expected_red_used = RED_TOTAL * target_used_frac
    expected_red_remaining = max(0.0, RED_TOTAL - expected_red_used)

    #  pression réelle vs pression cible sur les jours éligibles restants ---
    _, red_end = red_season_bounds(d)

    # Jours "posables" restants (lun-ven hors fériés, dans la saison rouge)
    eligible_left = count_red_eligible_days_left(d, red_end)
    eligible_left = max(1, eligible_left)  # sécurité

    # Pression réelle = combien de rouges il faut poser par jour éligible restant
    pressure_real = red_rem / eligible_left

    # Pression cible = combien de rouges il "devrait" rester (selon courbe) par jour éligible restant
    pressure_target = expected_red_remaining / eligible_left

    # Delta pression : >0 => on est en retard (trop de rouges pour trop peu de jours)
    delta_pressure = pressure_real - pressure_target

    # Cas extrême : plus de rouges que de jours éligibles => deadline immédiate
    if red_rem > eligible_left:
        delta_pressure = max(delta_pressure, 0.20)  # force un "retard" clair


    # Périodes
    debut_saison = (d.month == 11) or (d.month == 12 and d.day < 10)
    coeur_hiver_local = ((d.month == 12 and d.day >= 10) or (d.month in (1, 2) and d.day <= 15))
    fin_saison_local = (d.month == 2 and d.day >= 20) or (d.month == 3)

    # delta_pressure > 0  => retard (il faut "poser" plus souvent)
    # delta_pressure < 0  => avance (on a déjà trop consommé)
    if debut_saison:
        if delta_pressure > 0.03:
            edf_red_factor *= 0.70   # on calme en début de saison même si retard léger
        elif delta_pressure < -0.03:
            edf_red_factor *= 0.85

    elif coeur_hiver_local:
        if delta_pressure > 0.03:
            edf_red_factor *= 1.25
        elif delta_pressure < -0.03:
            edf_red_factor *= 0.80

    elif fin_saison_local:
        if delta_pressure > 0.02:
            edf_red_factor *= 1.35   # fin de saison = deadline, donc boost plus net
        elif delta_pressure < -0.03:
            edf_red_factor *= 0.85

    else:
        if delta_pressure > 0.04:
            edf_red_factor *= 1.08
        elif delta_pressure < -0.04:
            edf_red_factor *= 0.92

    if red_rem < 4 and d.month not in (1, 2):
        edf_red_factor *= 0.5

    score_r *= edf_red_factor

    # Samedi : jamais de rouge dans la décision finale
    if wd == 5:
        score_r = 0.0

    # Petit biais sur le score bleu en fonction du stock de jours bleus restants
    # Année Tempo = 01/09 -> 31/08
    tempo_start = tempo_year_start(d)
    tempo_end = tempo_year_end_inclusive(d)
    total_days_tempo = (tempo_end - tempo_start).days + 1

    if total_days_tempo > 0:
        progress = (d - tempo_start).days / total_days_tempo
        expected_blue_used = BLUE_TOTAL * progress
        expected_blue_remaining = BLUE_TOTAL - expected_blue_used
        delta_blue = blue_rem - expected_blue_remaining

        if delta_blue > 20:
            score_b *= 1.08
        elif delta_blue < -20:
            score_b *= 0.88

    # ============================================================
    # PATCH : Renforcer BLANC/ROUGE quand Z est élevé
    # (version sur les scores, avant passage en probabilités)
    # ============================================================
    if z is not None:
        # 1) Blanc renforcé quand Z dépasse s_br_adj + 0.3,
        #    mais avant la zone franchement rouge (z < s_r_adj)
        if (
            allowed_white
            and z >= s_br_adj + 0.3
            and z < s_r_adj
            and score_w < score_b
        ):
            # plus on se rapproche de s_r_adj, plus on donne sa chance au blanc
            delta_base = max(0.0, s_r_adj - z)  # >= 0
            delta = min(0.08, delta_base * 0.05)
            score_w = max(score_w, score_b + delta)

        # 2) Rouge : réveil progressif seulement très près du seuil rouge
        if allowed_red:
            # zone d'approche finale du seuil rouge : [s_r_adj - 0.07 ; s_r_adj]
            start = s_r_adj - 0.12
            if start <= z < s_r_adj and score_r < score_w:
                # t: 0 -> 1 quand on approche s_r_adj
                t = (z - start) / max(1e-6, (s_r_adj - start))
                # rouge remonte un peu, mais ne dépasse pas le blanc en zone médiane
                score_r = max(
                    score_r,
                    score_w * (0.45 + 0.55 * t)  # 0.45 -> 0.55 du blanc
                )


    # Passage en probabilités brutes
    total = score_b + score_w + score_r
    if total <= 0:
        score_b, score_w, score_r = 0.7, 0.2, 0.1
        total = 1.0

    p_bleu = score_b / total
    p_blanc = score_w / total
    p_rouge = score_r / total

    # ------------------------------------------------------------------
    # Effet "stock" sur les trois couleurs (bleu / blanc / rouge)
    # en fonction des jours restants dans l'année Tempo.
    # ------------------------------------------------------------------
    tempo_start = tempo_year_start(d)
    tempo_end = tempo_year_end_inclusive(d)
    total_days_tempo = (tempo_end - tempo_start).days + 1
    days_left = max(1, total_days_tempo - j + 1)

    # Estimation du stock bleu restant :
    blue_remaining = max(0, blue_rem)

    frac_blue = blue_remaining / days_left
    frac_white = white_rem / days_left if days_left > 0 else 0.0
    frac_red = red_rem / days_left if days_left > 0 else 0.0

    # 1) Beaucoup de bleus à "caser"
    if frac_blue > 0.8 and p_bleu < 0.8:
        shift = min(0.05, p_blanc * 0.3)
        if shift > 0:
            p_bleu += shift
            p_blanc -= shift

    # 2) Peu de bleus restants
    if frac_blue < 0.4:
        shift = min(0.05, p_bleu * 0.4)
        if shift > 0:
            p_bleu -= shift
            non_blue = p_blanc + p_rouge
            if non_blue > 0:
                p_blanc += shift * (p_blanc / non_blue)
                p_rouge += shift * (p_rouge / non_blue)

    # Ajustement hiver global (01/11–31/03) basé sur le stock de blancs
    if d.month in (11, 12, 1, 2, 3) and z is not None and z < 9.5:
        if white_rem >= 38:
            if p_rouge > 0.0:
                r_cut = p_rouge * 0.25
                p_rouge -= r_cut
                p_bleu += r_cut * 0.4
                p_blanc += r_cut * 0.6

            # Utilisation du seuil ajusté pour "près du bleu"
            if z < s_br_adj + 0.5 and p_blanc > 0.45:
                w_shift = min(0.08, p_blanc - 0.40)
                if w_shift > 0:
                    p_blanc -= w_shift
                    p_bleu += w_shift

            s = p_bleu + p_blanc + p_rouge
            if s > 0:
                p_bleu /= s
                p_blanc /= s
                p_rouge /= s

        elif white_rem <= 10:
            if p_bleu > 0.30:
                b_shift = min(0.15, p_bleu - 0.25)
                if b_shift > 0:
                    p_bleu -= b_shift
                    p_blanc += b_shift

            s = p_bleu + p_blanc + p_rouge
            if s > 0:
                p_bleu /= s
                p_blanc /= s
                p_rouge /= s

    # PATCH fin de saison (mi-février → mars)
    if z is not None and ((d.month == 2 and d.day >= 15) or d.month == 3):
        year_fs = red_season_bounds(d)[0].year  # année de début de saison rouge (01/11)

        red_start_fs, red_end_fs = red_season_bounds(d)

        if d <= red_start_fs:
            red_progress_fs = 0.0
        elif d >= red_end_fs:
            red_progress_fs = 1.0
        else:
            red_progress_fs = (d - red_start_fs).days / (red_end_fs - red_start_fs).days

        expected_red_used_fs = RED_TOTAL * red_progress_fs
        expected_red_remaining_fs = RED_TOTAL - expected_red_used_fs

        white_start_fs = dt.date(year_fs, 11, 1)
        white_end_fs = dt.date(year_fs + 1, 4, 30)

        if d <= white_start_fs:
            white_progress_fs = 0.0
        elif d >= white_end_fs:
            white_progress_fs = 1.0
        else:
            white_progress_fs = (d - white_start_fs).days / (white_end_fs - white_start_fs).days

        expected_white_used_fs = WHITE_TOTAL * white_progress_fs
        expected_white_remaining_fs = WHITE_TOTAL - expected_white_used_fs

        boosted_fs = False

        if white_rem > expected_white_remaining_fs + 3:
            p_blanc = max(p_blanc, 0.25)
            boosted_fs = True

        if (
            red_rem > expected_red_remaining_fs + 2
            and red_rem > 5
            and allowed_red
            and wd < 5
        ):
            p_rouge = max(p_rouge, 0.20)
            boosted_fs = True

        if boosted_fs:
            s_fs = p_bleu + p_blanc + p_rouge
            if s_fs > 0:
                p_bleu /= s_fs
                p_blanc /= s_fs
                p_rouge /= s_fs


    # PATCH bonus fin de saison (10 → 31 mars)
    if d.month == 3 and d.day >= 10:
        bonus = False
        if white_rem > 3 and p_blanc is not None:
            p_blanc = max(p_blanc, 0.30)
            bonus = True
        if red_rem > 5 and p_rouge is not None and allowed_red and wd < 5:
            p_rouge = max(p_rouge, 0.22)
            bonus = True
        if bonus:
            s_b = p_bleu + p_blanc + p_rouge
            if s_b > 0:
                p_bleu /= s_b
                p_blanc /= s_b
                p_rouge /= s_b

    # BOOST ROUGE fin de saison (mars) quand Z > 0.8
    if (
        d.month == 3
        and allowed_red
        and wd < 5
        and z is not None
        and z >= 0.7
    ):
        add = min(0.12, (p_bleu + p_blanc) * 0.4)
        if add > 0:
            p_rouge += add
            p_bleu -= add * 0.5
            p_blanc -= add * 0.5

        s = p_bleu + p_blanc + p_rouge
        if s > 0:
            p_bleu /= s
            p_blanc /= s
            p_rouge /= s

    # ============================================================
    # PATCH Z / coeur d'hiver : transfert BLEU/BLANC -> ROUGE
    # quand la tension est forte (Z) sur jours ouvrés.
    # ============================================================
    if (
        z is not None
        and d.month in (12, 1, 2)   # coeur d'hiver
        and wd < 5                  # lundi -> vendredi
        and allowed_red
        and red_rem > 0
    ):
        non_red = p_bleu + p_blanc

        # 1) On calcule combien on peut transférer vers le rouge
        if z >= 1.30:
            max_shift = 0.30
        elif z >= 1.00:
            max_shift = 0.10
        elif z >= 0.70:
            max_shift = 0.05
        else:
            max_shift = 0.03

        if max_shift > 0.0 and non_red > 0.0:
            shift = min(max_shift, non_red * 0.5)

            # On prélève sur bleu+blanc proportionnellement
            p_bleu  -= shift * (p_bleu  / non_red)
            p_blanc -= shift * (p_blanc / non_red)
            p_rouge += shift

            s_z = p_bleu + p_blanc + p_rouge
            if s_z > 0:
                p_bleu  /= s_z
                p_blanc /= s_z
                p_rouge /= s_z

        # 2) Pour Z >= 1.2 : rouge ne doit plus être derrière bleu
        if z >= 1.20 and p_rouge < p_bleu:
            diff = (p_bleu - p_rouge) * 0.6
            p_bleu  -= diff
            p_rouge += diff

            s_z2 = p_bleu + p_blanc + p_rouge
            if s_z2 > 0:
                p_bleu  /= s_z2
                p_blanc /= s_z2
                p_rouge /= s_z2

        # 3) Plancher rouge dynamique selon Z (au lieu d'un plancher fixe)
        floor_r = red_floor_from_z(z)

        if floor_r > 0.0 and p_rouge < floor_r:
            non_red = p_bleu + p_blanc
            if non_red > 0:
                add = min(floor_r - p_rouge, non_red * 0.4)
                if add > 0:
                    p_bleu  -= add * (p_bleu  / non_red)
                    p_blanc -= add * (p_blanc / non_red)
                    p_rouge += add

            s_z3 = p_bleu + p_blanc + p_rouge
            if s_z3 > 0:
                p_bleu  /= s_z3
                p_blanc /= s_z3
                p_rouge /= s_z3


    # BOOST ROUGE plein hiver dans la zone Z ≈ 1.25 –1.9
    if (
        z is not None
        and coeur_hiver
        and 1.25 <= z <= 1.9
        and allowed_red
        and wd < 5
    ):
        if p_rouge < p_blanc:
            # rouge doit pouvoir dépasser blanc dans cette zone
            diff = (p_blanc - p_rouge) * 0.45
            p_rouge += diff
            p_blanc -= diff

    # ============================================================
    # PATCH Z bas en plein coeur d'hiver :
    # si la tension est faible (Z <= 0.6), rouge quasi nul,
    # on penche légèrement vers BLANC plutôt que BLEU.
    # ============================================================
    if (
        z is not None
        and d.month in (12, 1, 2)
        and wd < 5
        and p_rouge < 0.05
        and 0.0 <= z <= 0.6
        and p_bleu > p_blanc
    ):
        gap = p_bleu - p_blanc
        shift = min(gap * 0.5, 0.08)
        if shift > 0:
            p_bleu  -= shift
            p_blanc += shift

        s_l = p_bleu + p_blanc + p_rouge
        if s_l > 0:
            p_bleu  /= s_l
            p_blanc /= s_l
            p_rouge /= s_l

     # BOOST BLEU global si pas de rouge (duel bleu/blanc)
    # Désactivé en plein cœur d'hiver quand Z est déjà un peu élevé
    if (
        p_rouge == 0.0
        and p_blanc > 0.55
        and not (
            coeur_hiver
            and z is not None
            and z >= 0.62   # à partir de là, on arrête d'aider le bleu
        )
    ):
        if d.month in (11, 3):
            base_boost = 0.2
        else:
            base_boost = 0.15

        gap_nb = abs(p_blanc - p_bleu)
        if gap_nb < 0.03:
            boost_factor = base_boost * 0.7
        else:
            boost_factor = base_boost

        surplus = p_blanc - 0.45
        if surplus > 0:
            shift = surplus * boost_factor
            p_blanc -= shift
            p_bleu += shift
    # PRIOR samedi (après novembre)
    if wd == 5 and p_rouge == 0.0:
        total_nb = p_bleu + p_blanc
        if total_nb > 0:
            factor = 1.0 / total_nb
            p_bleu *= factor
            p_blanc *= factor

        if d.month != 11:
            # Hors novembre : on évite les samedis "extrêmes"
            if p_bleu > 0.65:
                exc = p_bleu - 0.65
                p_bleu = 0.65
                p_blanc += exc
            elif p_blanc > 0.70:
                exc = p_blanc - 0.70
                p_blanc = 0.70
                p_bleu += exc
        else:
            # Novembre : forte préférence structurelle pour le bleu,
            # mais on évite l'effet "toujours 70/30".
            frac_blue_local = blue_rem / days_left
            frac_white_local = white_rem / days_left if days_left > 0 else 0.0

            base_boost = 0.20
            if frac_blue_local >= 0.6 and frac_white_local <= 0.25:
                base_boost = 0.24

            ideal_white_floor = 0.30
            if p_blanc < ideal_white_floor:
                deficit = ideal_white_floor - p_blanc
                shift = min(base_boost, deficit * 0.6)
                if shift > 0:
                    p_blanc += shift
                    p_bleu -= shift

            if z is not None and z <= 5.3 and p_blanc > p_bleu:
                gap = p_blanc - p_bleu
                shift = gap * 0.7
                p_blanc -= shift
                p_bleu += shift

        s2 = p_bleu + p_blanc + p_rouge

        if s2 > 0:
            p_bleu /= s2
            p_blanc /= s2
            p_rouge /= s2

    # Sécurité : si du rouge traîne encore un samedi (au cas où)
    if wd == 5 and p_rouge > 0.0:
        p_rouge = 0.0
        nb = p_bleu + p_blanc
        if nb > 0:
            factor = 1.0 / nb
            p_bleu *= factor
            p_blanc *= factor

#    # PATCH A : rouge trop faible en plein hiver quand Z ≈ 1
    if (
        z is not None
        and d.month in (12, 1, 2)
        and wd < 5
        and allowed_red
        and z >= 1.10
    ):
        # On impose un plancher = max(p_blanc, p_rouge)
        target = max(p_rouge, min(p_blanc, red_floor_from_z(z)))
        if p_rouge < target:
            diff = target - p_rouge
            # on prélève moitié sur bleu, moitié sur blanc
            take_b = diff * 0.5
            take_w = diff * 0.3
            if p_bleu >= take_b:
                p_bleu -= take_b
            if p_blanc >= take_w:
                p_blanc -= take_w
            p_rouge += diff

        # Renormalisation
        s = p_bleu + p_blanc + p_rouge
        if s > 0:
            p_bleu /= s
            p_blanc /= s
            p_rouge /= s

    # PATCH B : Rouge minimal en novembre quand Z est élevé
    if (
        z is not None
        and d.month == 11
        and allowed_red
        and z >= 0.75
        and p_rouge < 0.10  # plancher rouge obligatoire
    ):
        add = 0.10 - p_rouge
        non_red = p_bleu + p_blanc
        if non_red > 0:
            p_bleu  -= add * (p_bleu  / non_red)
            p_blanc -= add * (p_blanc / non_red)
        p_rouge += add

        # Renormalisation
        s = p_bleu + p_blanc + p_rouge
        if s > 0:
            p_bleu /= s
            p_blanc /= s
            p_rouge /= s

    # PATCH C : Blanc minimal en novembre dès que Z > 0.2
    if (
        z is not None
        and d.month == 11
        and allowed_white
        and z >= 0.20
        and p_blanc < 0.35
    ):
        add = 0.35 - p_blanc
        # on prend sur bleu uniquement
        if p_bleu >= add:
            p_bleu -= add
            p_blanc += add

        # renormalisation
        s = p_bleu + p_blanc + p_rouge
        if s > 0:
            p_bleu /= s
            p_blanc /= s
            p_rouge /= s


    # Choix de la couleur dominante
    couleur = "bleu"
    max_p = p_bleu
    if p_blanc > max_p:
        couleur = "blanc"
        max_p = p_blanc
    if p_rouge > max_p:
        couleur = "rouge"
        max_p = p_rouge

    # Mise à jour des stocks
    if couleur == "rouge":
        red_rem = max(0, red_rem - 1)
    elif couleur == "blanc":
        white_rem = max(0, white_rem - 1)
    else:  # bleu
        blue_rem = max(0, blue_rem - 1)

    return couleur, red_rem, white_rem, blue_rem, p_bleu, p_blanc, p_rouge



def decide_color_with_wrappers_no_stock(d, z, j, red_rem, white_rem, blue_rem):
    """
    Wrapper qui:
    - force bleu dimanche
    - interdit rouge si non autorisé
    - MAIS ne touche PAS aux stocks
    """
    couleur, _, _, _, pB, pW, pR = decide_color_with_probs(d, z, j, red_rem, white_rem, blue_rem)

    if d.weekday() == 6:
        return "bleu", 1.0, 0.0, 0.0

    if couleur == "rouge" and not allowed("rouge", d):
        pR = 0.0
        s = pB + pW
        if s <= 0:
            pB, pW = 0.8, 0.2
            s = 1.0
        else:
            pB /= s
            pW /= s
        couleur = "bleu" if pB >= pW else "blanc"

    return couleur, pB, pW, pR


def _confidence_label(score):
    if score is None:
        return "Indisponible"
    if score >= 4.5:
        return "Très forte"
    if score >= 3.5:
        return "Forte"
    if score >= 2.5:
        return "Moyenne"
    if score >= 1.5:
        return "Faible"
    return "Très faible"

def _compute_confidence_score(couleur, p_bleu, p_blanc, p_rouge, red_rem, white_rem, offset, gen_source=None):
    if any(v is None for v in (p_bleu, p_blanc, p_rouge)):
        return None

    probs = {
        "bleu": p_bleu,
        "blanc": p_blanc,
        "rouge": p_rouge,
    }

    ordered = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    (c1, p1), (c2, p2), (c3, p3) = ordered
    gap = p1 - p2

    raw = 0.0

    # 1) Force de la couleur gagnante
    if p1 >= 0.75:
        raw += 3.0
    elif p1 >= 0.65:
        raw += 2.5
    elif p1 >= 0.55:
        raw += 2.0
    elif p1 >= 0.48:
        raw += 1.5
    elif p1 >= 0.45:
        raw += 1.2
    else:
        raw += 0.7

    # 2) Écart gagnant / second
    if gap >= 0.25:
        raw += 1.0
    elif gap >= 0.15:
        raw += 0.5
    elif gap >= 0.08:
        raw += 0.0
    elif gap >= 0.04:
        raw -= 0.5
    else:
        raw -= 1.0

    # 3) Distance temporelle (J+1 plus fiable que J+6)
    if offset == 1:
        raw += 1.5
    elif offset == 2:
        raw += 1.0
    elif offset == 3:
        raw += 0.5
    elif offset == 5:
        raw -= 0.5
    elif offset >= 6:
        raw -= 0.8

    # 4) Effet saisonnier large
    today = dt.date.today()
    d = today + dt.timedelta(days=offset)
    month = d.month
    day = d.day
    
    # Dimanche : bleu certain → score 5
    if couleur == "bleu" and (d.weekday() == 6):
        return 5

    if couleur == "rouge":
        if month in (1, 2):
            raw += 0.3
        elif month in (12, 3):
            raw += 0.0
        elif month == 11:
            raw -= 0.45
        else:
            raw -= 1.0

    elif couleur == "blanc":
        if month in (11, 12, 1, 2, 3):
            raw += 0.1
        elif month in (4, 10):
            raw += 0.0
        else:
            raw -= 0.6

    elif couleur == "bleu":
        if month in (5, 6, 7, 8, 9):
            raw += 0.4
        elif month in (4, 10, 11):
            raw += 0.1
        elif month in (12, 1, 2, 3):
            raw -= 0.1

    # 4 bis) Affinage rouge (mois / jour / stock)
    if couleur == "rouge":
        if month == 11:
            raw -= 0.4
        elif month == 12:
            if day < 10:
                raw -= 0.3
            elif day > 28:
                if red_rem <= 6:
                    raw -= 0.4
                else:
                    raw -= 0.1
        elif month == 2:
            if day > 25 and red_rem <= 8:
                raw -= 0.5
            elif day > 20 and red_rem <= 8:
                raw -= 0.3
        elif month == 3:
            if day > 20 and red_rem <= 8:
                raw -= 0.8
            elif day > 15 and red_rem <= 8:
                raw -= 0.5

    # 5) Effet "stock restant" rouge / blanc

    red_start, red_end = red_season_bounds(d)

    if d <= red_start:
        red_progress = 0.0
    elif d >= red_end:
        red_progress = 1.0
    else:
        red_progress = (d - red_start).days / (red_end - red_start).days

    expected_red_used = RED_TOTAL * red_progress
    expected_red_remaining = RED_TOTAL - expected_red_used
    delta_red = red_rem - expected_red_remaining

    if delta_red > 4 and couleur != "rouge":
        raw -= 0.4
    elif delta_red < -4 and couleur == "rouge":
        raw -= 0.4
    year = red_season_bounds(d)[0].year  # IX : année de saison (01/11)
    white_start = dt.date(year, 11, 1)
    white_end = dt.date(year + 1, 4, 30)

    if d <= white_start:
        white_progress = 0.0
    elif d >= white_end:
        white_progress = 1.0
    else:
        white_progress = (d - white_start).days / (white_end - white_start).days

    expected_white_used = WHITE_TOTAL * white_progress
    expected_white_remaining = WHITE_TOTAL - expected_white_used
    delta_white = white_rem - expected_white_remaining

    if delta_white > 6 and couleur == "bleu":
        raw -= 0.3
    if delta_white < -6 and couleur in ("blanc", "rouge"):
        raw -= 0.3

    # 5 bis) NOUVEAU : duel bleu/blanc serré ⇒ on baisse un peu la confiance
    if (
        p_rouge is not None and p_rouge < 0.15
        and p_bleu is not None and p_blanc is not None
    ):
        if max(p_bleu, p_blanc) >= 0.45 and abs(p_bleu - p_blanc) < 0.15:
            # Assez net pour trancher, mais historiquement c'est là
            # que EDF peut basculer d'une couleur à l'autre.
            raw -= 0.4

    # 5 ter) Fiabilité des données ENR (impact fort sur J+4..J+6)
    if gen_source in ("carryover_last_known", "estime_ratio_J+1"):
        if offset >= 4:
            raw -= 1.0   # gros doute : on recycle/estime l'ENR
        elif offset == 3:
            raw -= 0.6
        else:
            raw -= 0.3


    # 6) Petit boost J+1 / J+2 si le modèle est assez net
    if offset in (1, 2):
        if p1 >= 0.46 and gap >= 0.03 and raw < 3.0:
            raw += 0.5

    # 7) Conversion en score 1..5
    if raw <= 0.5:
        score = 1          # Très faible
    elif raw <= 1.5:
        score = 2          # Faible
    elif raw <= 2.5:
        score = 3          # Moyenne
    elif raw <= 3.5:
        score = 4          # Forte
    else:
        score = 5          # Très forte
       
       
    return score



def _build_confidence_comment(couleur, p_bleu, p_blanc, p_rouge, score, offset):
    probs = {
        "bleu": p_bleu,
        "blanc": p_blanc,
        "rouge": p_rouge,
    }

    ordered = sorted(probs.items(), key=lambda x: (x[1] is None, x[1]), reverse=True)
    (c1, p1), (c2, p2), (c3, p3) = ordered
    if p1 is None or p2 is None:
        return "Indice de confiance indisponible (données manquantes)."

    gap = p1 - p2
    
    # Dimanche  : bleu certain
    today = dt.date.today()
    d = today + dt.timedelta(days=offset)
    if couleur == "bleu" and (d.weekday() == 6):
        return "Dimanche, bleu certain."


    # Jour proche / moyen / lointain
    if offset <= 2:
        dist = "proche"
    elif offset <= 3:
        dist = "moyen"
    else:
        dist = "lointain"

    cname = lambda c: c

    # ------------------------------------------------------------------
    # 0) Cas spéciaux : duel bleu/blanc et risque de rouge
    # ------------------------------------------------------------------

    # Duel bleu / blanc (rouge faible, deux couleurs proches)
    if (
        p_rouge is not None and p_rouge < 0.15
        and p_bleu is not None and p_blanc is not None
        and max(p_bleu, p_blanc) >= 0.45
        and abs(p_bleu - p_blanc) < 0.15
    ):
        # on force un message "duel", quel que soit le score (2–4)
        if score >= 3:
            return f"Duel bleu/blanc :  avance {cname(c1)}."
        else:
            return "Duel bleu/blanc très serré, prévision sensible."

    # Rouge non gagnant mais significatif → on le mentionne clairement
    if couleur != "rouge" and p_rouge is not None and p_rouge >= 0.25:
        if score >= 3:
            return f"{cname(c1)} devant, mais risque rouge en embuscade."
        else:
            return "Risque rouge marqué, plusieurs scénarios restent ouverts."

    # ------------------------------------------------------------------
    # 1) Cas standards (inchangé, mais profite des nouveaux scores)
    # ------------------------------------------------------------------

    # score 5 : confiance très forte
    if score == 5:
        if dist == "proche":
            return f"Jour proche, {cname(c1)} nettement probable."
        elif dist == "moyen":
            return f"Couleur clairement en tête, à surveiller."
        else:
            return f"Couleur en tête. Prévision lointaine."

    # score 4 : confiance forte
    if score == 4 or score == 4.5:
        if dist == "proche":
            return f"Jour proche : {cname(c1)} clairement favori."
        elif dist == "moyen":
            return f"Couleur en tête, à surveiller."
        else:
            return f"Prévision lointaine, une autre couleur reste crédible."

    # score 3 : confiance moyenne
    if score == 3 or score == 3.5:
        if abs(gap) <= 0.15:
            return f"Duel {cname(c1)}/{cname(c2)}, prévision sensible."
        elif dist == "lointain":
            return f"Jour éloigné, {cname(c1)} probable, bascule possible."
        else:
            return f"{cname(c1)} probable, mais bascule possible."

    # score 2 : confiance faible
    if score == 2 or score == 2.5:
        if dist == "lointain":
            return "Jour lointain, beaucoup d’incertitude."
        elif abs(gap) <= 0.08:
            return f"Écart faible : {cname(c2)} reste une vraie option."
        else:
            return f"Plusieurs scénarios restent ouverts."

    # score 1 : confiance très faible
    if dist == "lointain":
        return f"Prévision très lointaine, fiabilité très faible."
    else:
        return f"Couleurs très proches, modèle hésitant."


def build_forecast(real_j1=REAL_J1):
    token = get_token()

    cons_short = get_short_term(token)
    cons_week = get_weekly(token)
    gen = get_generation(token)

    try:
        annual = get_annual(token)
        daily_data = build_daily_data_from_annual_forecasts(annual)
        daily_data = sorted(daily_data, key=lambda x: x["date"])
    except Exception:
        daily_data = []

    today = dt.date.today()

    # --- Température nationale prévue (Tmoy) ---
    meteo_error = None

    if ENABLE_METEO:
        try:
            tnat_map = compute_national_tmoy(today, offsets=range(1, 4))
        except Exception as e:
            tnat_map = {}
            meteo_error = repr(e)
            print("Open-Meteo ERROR:", meteo_error, file=sys.stderr)
    else:
        tnat_map = {}
        meteo_error = "meteo_disabled_manual"



    red_rem = RED_REMAINING
    white_rem = WHITE_REMAINING
    blue_rem = BLUE_REMAINING

    days = []
    last_gen_val = None

    for offset in range(1, 7):
        d = today + dt.timedelta(days=offset)

        # init par jour (évite pollution)
        meteo_bias = 0.0

        conso = cons_short.get(d) or cons_week.get(d)

        gen_val = gen.get(d)
        gen_source = None

        if gen_val is not None:
            gen_source = "RTE"
            last_gen_val = gen_val

        if gen_val is None and conso is not None and offset in (2, 3):
            d_j1 = today + dt.timedelta(days=1)
            conso_j1 = cons_short.get(d_j1) or cons_week.get(d_j1)
            gen_j1 = gen.get(d_j1)

            if conso_j1 is not None and gen_j1 is not None and conso_j1 > 0:
                ratio = gen_j1 / conso_j1
                ratio = max(0.0, min(0.5, ratio))
                gen_val = conso * ratio
                gen_source = "estime_ratio_J+1"
                last_gen_val = gen_val

        if gen_val is None and conso is not None and last_gen_val is not None:
            gen_val = last_gen_val
            gen_source = gen_source or "carryover_last_known"

        T_nat = tnat_map.get(d)
        T_norm = rte_norm_temp(d)
        delta_T = (T_nat - T_norm) if (T_nat is not None and T_norm is not None) else None

        entry = {
            "offset": offset,
            "date": d,
            "conso": conso,
            "gen": gen_val,
            "gen_source": gen_source,
            "T_nat": T_nat,
            "T_norm": T_norm,
            "delta_T": delta_T,
            "meteo_bias": 0.0,  # sera mis à jour plus tard si appliqué
        }

        if ENABLE_DEBUG:
            entry["debug"] = {
                "annual_days_count": len(daily_data) if daily_data else 0,
                "annual_first_date": daily_data[0]["date"] if daily_data else None,
                "annual_last_date": daily_data[-1]["date"] if daily_data else None,
                "annual_sample_dates": (
                    [x.get("date") for x in (daily_data[:3] + daily_data[-3:])] if daily_data else None
                ),
                "z_try": None,
                "z_error": None,
                "z_error_type": None,
                "meteo_error": meteo_error,
            }



        if conso is not None:
            c_net = conso - gen_val if gen_val is not None else conso
            entry["c_net"] = c_net

            try:
                if daily_data:
                    if ENABLE_DEBUG and entry["debug"] is not None:
                        entry["debug"]["z_try"] = "annual_forecast"
                        z_raw, z_calib = compute_z_rte_like(
                            d, c_net, daily_data, window_days=365, return_debug=True
                        )
                        entry["debug"]["z_calibration"] = z_calib
                    else:
                        z_raw = compute_z_rte_like(
                            d, c_net, daily_data, window_days=365, return_debug=False
                        )
                    z_source = "annual_forecast"
                else:
                    if ENABLE_DEBUG and entry["debug"] is not None:
                        entry["debug"]["z_try"] = "fallback_std_no_daily_data"
                    z_raw = compute_z(c_net)
                    z_source = "fallback_std"
            except Exception as e:
                if ENABLE_DEBUG and entry["debug"] is not None:
                    entry["debug"]["z_error"] = repr(e)
                    entry["debug"]["z_error_type"] = type(e).__name__
                z_raw = compute_z(c_net)
                z_source = "fallback_std"


            entry["z"] = z_raw
            entry["z_source"] = z_source
            entry["z_raw"] = z_raw

        days.append(entry)

    result = {}

    for entry in days:
        d = entry["date"]
        offset = entry["offset"]
        label = f"J+{offset}"

        conso = entry.get("conso")
        gen_val = entry.get("gen")
        gen_source = entry.get("gen_source")
        meteo_bias = 0.0  # toujours défini

        # Snapshot stocks AVANT calcul du jour
        red0, white0, blue0 = red_rem, white_rem, blue_rem

        if conso is None:
            result[label] = {
                "date": d.isoformat(),
                "couleur": "inconnue",
                "modele": None,
                "c_mw": None,
                "gen_mw": round(gen_val, 1) if gen_val is not None else None,
                "gen_source": gen_source,
                "c_net_mw": None,
                "z": None,
                "red_remaining": red_rem,
                "white_remaining": white_rem,
                "blue_remaining": blue_rem,
                "p_bleu": None,
                "p_blanc": None,
                "p_rouge": None,
                "confidence_score": None,
                "confidence_label": "Indisponible",
                "confidence_comment": "Indice de confiance indisponible (données manquantes).",
                "z_source": entry.get("z_source"),
                "T_nat": entry.get("T_nat"),
                "T_norm": entry.get("T_norm"),
                "delta_T": entry.get("delta_T"),
                "meteo_bias": 0.0,
                "z_raw": round(entry.get("z_raw"), 3) if entry.get("z_raw") is not None else None,
                **({"z_debug": entry.get("debug")} if ENABLE_DEBUG else {}),
            }
            continue


        c_net = entry["c_net"]
        z = entry["z"]
        j = day_index(d)

        # 1) modèle de base (SANS décrément stocks)
        couleur_modele, p_bleu, p_blanc, p_rouge = decide_color_with_wrappers_no_stock(
            d, z, j, red0, white0, blue0
        )
        couleur = couleur_modele

        # 2) shrink / mask (peut changer couleur, mais pas stocks)
        if offset == 2:
            p_bleu, p_blanc, p_rouge = shrink_probs(p_bleu, p_blanc, p_rouge, 0.20)
            p_bleu, p_blanc, p_rouge = mask_probs_by_calendar(d, p_bleu, p_blanc, p_rouge)
            couleur = base_decision_from_probs(p_bleu, p_blanc, p_rouge)
        elif offset == 3:
            p_bleu, p_blanc, p_rouge = shrink_probs(p_bleu, p_blanc, p_rouge, 0.35)
            p_bleu, p_blanc, p_rouge = mask_probs_by_calendar(d, p_bleu, p_blanc, p_rouge)
            couleur = base_decision_from_probs(p_bleu, p_blanc, p_rouge)
        elif offset in (4, 5, 6):
            base_lam = {4: 0.40, 5: 0.50, 6: 0.60}[offset]
            extra = 0.10 if gen_source in ("carryover_last_known", "estime_ratio_J+1") else 0.0
            lam = min(0.75, base_lam + extra)
            p_bleu, p_blanc, p_rouge = shrink_probs(p_bleu, p_blanc, p_rouge, lam)
            p_bleu, p_blanc, p_rouge = mask_probs_by_calendar(d, p_bleu, p_blanc, p_rouge)
            couleur = base_decision_from_probs(p_bleu, p_blanc, p_rouge)

        # 3) météo (peut changer couleur)
        delta_T = entry.get("delta_T")
        p_bleu, p_blanc, p_rouge, meteo_bias = apply_meteo_shift_probs(p_bleu, p_blanc, p_rouge, delta_T)
        p_bleu, p_blanc, p_rouge = mask_probs_by_calendar(d, p_bleu, p_blanc, p_rouge)
        couleur = base_decision_from_probs(p_bleu, p_blanc, p_rouge)

        # 4) override J+1 après 06:45
        now_time = dt.datetime.now().time()
        if offset == 1 and real_j1 in ("bleu", "blanc", "rouge") and now_time >= dt.time(6, 45):
            couleur = real_j1
            p_bleu = 1.0 if couleur == "bleu" else 0.0
            p_blanc = 1.0 if couleur == "blanc" else 0.0
            p_rouge = 1.0 if couleur == "rouge" else 0.0

            # Décrément stocks UNE FOIS avec la couleur finale
            red_rem, white_rem, blue_rem = red0, white0, blue0
            if couleur == "rouge":
                red_rem = max(0, red_rem - 1)
            elif couleur == "blanc":
                white_rem = max(0, white_rem - 1)
            else:
                blue_rem = max(0, blue_rem - 1)

            result[label] = {
                "date": d.isoformat(),
                "couleur": couleur,
                "modele": "override_RTE",
                "c_mw": round(conso, 1),
                "gen_mw": round(gen_val, 1) if gen_val is not None else None,
                "gen_source": gen_source,
                "c_net_mw": round(c_net, 1),
                "z": round(z, 3),
                "red_remaining": red_rem,
                "white_remaining": white_rem,
                "blue_remaining": blue_rem,
                "p_bleu": p_bleu,
                "p_blanc": p_blanc,
                "p_rouge": p_rouge,
                "confidence_score": 5,
                "confidence_label": "Très forte",
                "confidence_comment": "Couleur J+1 confirmée par RTE.",
                "z_source": entry.get("z_source"),
                "T_nat": entry.get("T_nat"),
                "T_norm": entry.get("T_norm"),
                "delta_T": entry.get("delta_T"),
                "meteo_bias": meteo_bias,
                "z_raw": round(entry.get("z_raw"), 3) if entry.get("z_raw") is not None else None,
                **({"z_debug": entry.get("debug")} if ENABLE_DEBUG else {}),
            }
            continue

        # Décrément stocks UNE FOIS avec la couleur finale
        red_rem, white_rem, blue_rem = red0, white0, blue0
        if couleur == "rouge":
            red_rem = max(0, red_rem - 1)
        elif couleur == "blanc":
            white_rem = max(0, white_rem - 1)
        else:
            blue_rem = max(0, blue_rem - 1)

        # Score confiance (inchangé chez toi)
        score = _compute_confidence_score(
            couleur, p_bleu, p_blanc, p_rouge, red_rem, white_rem, offset, gen_source
        )
        conf_label = _confidence_label(score)
        conf_comment = _build_confidence_comment(couleur, p_bleu, p_blanc, p_rouge, score, offset)

        result[label] = {
            "date": d.isoformat(),
            "couleur": couleur,
            "modele": "modele_interne",
            "c_mw": round(conso, 1),
            "gen_mw": round(gen_val, 1) if gen_val is not None else None,
            "gen_source": gen_source,
            "c_net_mw": round(c_net, 1),
            "z": round(z, 3),
            "red_remaining": red_rem,
            "white_remaining": white_rem,
            "blue_remaining": blue_rem,
            "p_bleu": round(p_bleu, 3),
            "p_blanc": round(p_blanc, 3),
            "p_rouge": round(p_rouge, 3),
            "confidence_score": score,
            "confidence_label": conf_label,
            "confidence_comment": conf_comment,
            "z_source": entry.get("z_source"),
            "T_nat": entry.get("T_nat"),
            "T_norm": entry.get("T_norm"),
            "delta_T": entry.get("delta_T"),
            "meteo_bias": meteo_bias,
            "z_raw": round(entry.get("z_raw"), 3) if entry.get("z_raw") is not None else None,
            **({"z_debug": entry.get("debug")} if ENABLE_DEBUG else {}),
        }

    return result


if __name__ == "__main__":
    try:
        data = build_forecast()
        wrapper = {"generated_at": dt.datetime.now().isoformat(), **data}
        print(json.dumps(wrapper))
    except Exception as e:
        print("ERROR in build_forecast:", repr(e), file=sys.stderr)
        err = {"error": str(e), "generated_at": dt.datetime.now().isoformat()}
        print(json.dumps(err))
        sys.exit(0)
