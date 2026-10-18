"""
Shared address helpers for C5: normalization, part-by-part similarity, distance.

No external dependencies (rapidfuzz is used for string similarity if installed,
otherwise Python's difflib).
"""

import math
import re
from difflib import SequenceMatcher

try:
    from rapidfuzz.fuzz import ratio as _rf_ratio

    def text_sim(a, b):
        return _rf_ratio(a, b) / 100.0
except ImportError:                                   # pragma: no cover
    def text_sim(a, b):
        return SequenceMatcher(None, a, b).ratio()

ABBREV = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD", "ROAD": "RD", "DRIVE": "DR",
    "LANE": "LN", "COURT": "CT", "PLACE": "PL", "PARKWAY": "PKWY", "HIGHWAY": "HWY", "CIRCLE": "CIR",
    "TERRACE": "TER", "SQUARE": "SQ", "WAY": "WAY", "PLAZA": "PLZ", "CENTER": "CTR", "CENTRE": "CTR",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHWEST": "NW", "NORTHEAST": "NE", "SOUTHWEST": "SW", "SOUTHEAST": "SE",
    "SUITE": "STE", "UNIT": "STE", "APARTMENT": "STE", "APT": "STE", "ROOM": "STE", "RM": "STE",
    "BUILDING": "BLDG", "FLOOR": "FL", "MOUNT": "MT", "SAINT": "ST",
}
UNIT_RE = re.compile(r"\b(?:STE|BLDG|FL)\s*#?\s*([A-Z0-9-]+)\b|#\s*([A-Z0-9-]+)\b")
STREET_TYPES = "ST|AVE|BLVD|RD|DR|LN|CT|PL|PKWY|CIR|TER|SQ|WAY|PLZ|CTR|N|S|E|W|NE|NW|SE|SW"
BARE_UNIT_RE = re.compile(rf"\b({STREET_TYPES})\s+([A-Z]?\d+[A-Z]?|\d+-?[A-Z]|[A-Z]-?\d+)\s*$")
POBOX_RE = re.compile(r"\bP\s*O\s*BOX\b|\bPOST OFFICE BOX\b|\bPO BOX\b|\bBOX\s+\d+\b")


def _clean(s):
    s = str(s or "").upper()
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"[^A-Z0-9#/ -]", " ", s)
    words = [ABBREV.get(w, w) for w in s.split()]
    out = " ".join(words)
    return re.sub(r"\b([NS]) ([EW])\b", r"\1\2", out)      # "N W" -> "NW"


def normalize_address(address_1, address_2="", city="", state="", zip_code=""):
    """Return the address split into comparable parts plus keys."""
    a1, a2 = _clean(address_1), _clean(address_2)
    line = f"{a1} {a2}".strip()
    po_box = bool(POBOX_RE.search(line))
    unit = ""
    m = UNIT_RE.search(line)
    if m:
        unit = (m.group(1) or m.group(2) or "").strip("-")
        line_wo_unit = UNIT_RE.sub(" ", line)
    else:
        line_wo_unit = line
        # bare suite after the street type, e.g. "2001 SANTA MONICA BLVD 990W" -> unit 990W
        mb = BARE_UNIT_RE.search(line)
        if mb:
            unit = mb.group(2)
            line_wo_unit = line[:mb.start(2)]
    line_wo_unit = re.sub(r"\s+", " ", line_wo_unit).strip()
    num = ""
    mnum = re.match(r"^(\d+[A-Z]?)\b\s*(.*)$", line_wo_unit)
    street = line_wo_unit
    if mnum:
        num, street = mnum.group(1), mnum.group(2)
    z = re.sub(r"\D", "", str(zip_code or ""))
    zip5 = z[:5]
    c = _clean(city)
    st = _clean(state)[:2]
    street_key = re.sub(r"\s+", " ", street).strip()
    return {
        "number": num, "street": street_key, "unit": unit, "city": c, "state": st,
        "zip5": zip5, "po_box": po_box,
        # same building key (ignores suite) and full key (with suite)
        "site_key": f"{num}|{street_key}|{zip5}",
        "full_key": f"{num}|{street_key}|{unit}|{c}|{st}|{zip5}",
    }


WEIGHTS = {"number": 0.30, "street": 0.25, "zip5": 0.20, "city": 0.10, "state": 0.05, "unit": 0.10}


def similarity(a, b):
    """Part-by-part similarity of two normalized addresses (0..1).
    number, zip, state: exact; street, city: fuzzy; unit: exact, neutral (0.5) if one side missing."""
    if not a or not b:
        return 0.0
    s = 0.0
    s += WEIGHTS["number"] * (1.0 if a["number"] and a["number"] == b["number"] else 0.0)
    s += WEIGHTS["street"] * text_sim(a["street"], b["street"])
    s += WEIGHTS["zip5"] * (1.0 if a["zip5"] and a["zip5"] == b["zip5"] else 0.0)
    s += WEIGHTS["city"] * (text_sim(a["city"], b["city"]) if a["city"] and b["city"] else 0.0)
    s += WEIGHTS["state"] * (1.0 if a["state"] and a["state"] == b["state"] else 0.0)
    if a["unit"] and b["unit"]:
        s += WEIGHTS["unit"] * (1.0 if a["unit"] == b["unit"] else 0.0)
    else:
        s += WEIGHTS["unit"] * 0.5
    return round(s, 4)


def haversine_km(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def _same_street(a, b, threshold=0.85):
    """Fuzzy street match used only when house number and ZIP already agree.
    Catches truncation / non-standard abbreviations ("SANTA MARGARITA PK" vs
    "SANTA MARGARITA PKWY") but never merges different directions (N vs S) or
    different numbered streets (5TH vs 6TH)."""
    if a == b:
        return True
    if not a or not b:
        return False
    ta, tb = a.split(), b.split()
    if {t for t in ta if t in DIRECTIONS} != {t for t in tb if t in DIRECTIONS}:
        return False
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    return text_sim(a, b) >= threshold


def canonical_site_map(site_keys):
    """{site_key: canonical site_key}. Keys with the same house number and ZIP whose
    streets are the same street written differently are mapped to the most common
    spelling, so all scripts treat them as ONE site."""
    import collections
    counts = collections.Counter(k for k in site_keys if k)
    buckets = collections.defaultdict(list)
    for k in counts:
        num, street, z = (k.split("|") + ["", "", ""])[:3]
        if num and z:
            buckets[(num, z)].append(k)
    out = {k: k for k in counts}
    for keys in buckets.values():
        if len(keys) < 2:
            continue
        keys.sort(key=lambda k: -counts[k])          # most common spelling first
        clusters = []
        for k in keys:
            st = k.split("|")[1]
            for c in clusters:
                if _same_street(st, c[0].split("|")[1]):
                    c.append(k)
                    break
            else:
                clusters.append([k])
        for c in clusters:
            for k in c:
                out[k] = c[0]
    return out