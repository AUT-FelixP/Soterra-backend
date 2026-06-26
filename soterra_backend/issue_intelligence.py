from __future__ import annotations

import re
from collections import Counter
from typing import Any


ACTION_TERMS = (
    "missing",
    "incomplete",
    "loose",
    "not compliant",
    "non-compliant",
    "defect",
    "remediated",
    "rectify",
    "required",
    "requires",
    "will need",
    "needs to",
    "less than",
    "below",
    "exceeds",
    "excessive",
    "tolerance",
    "reinstate",
    "outstanding",
    "not installed",
    "close-out",
    "flashing",
    "flashings",
    "cavity",
    "membrane",
    "threshold",
    "ducting",
    "duct",
    "clashing",
    "clash",
    "pressed",
    "tight",
    "lagging",
    "clearance",
    "cabling",
)

STRONG_ACTION_TERMS = (
    "fail",
    "failed",
    "missing",
    "incomplete",
    "loose",
    "not compliant",
    "non-compliant",
    "defect",
    "remediated",
    "rectify",
    "less than",
    "below",
    "exceeds",
    "excessive",
    "tolerance",
    "reinstate",
    "outstanding",
    "not installed",
    "close-out",
    "flashing",
    "cavity",
    "membrane",
    "ducting",
    "clashing",
    "clearance",
)

POSITIVE_OBSERVATIONS = (
    "looks okay",
    "looked okay",
    "acceptable",
    "carried out as per",
    "installed around the opening",
    "installation is carried out",
    "installation looks okay",
)

NON_ACTIONABLE_PHRASES = (
    "not applicable",
    "refer items below",
    "site meeting photos inspection",
    "next inspection required site meeting photos",
    "fail inspection outcome work completed in accordance",
    "work completed in accordance with plans yes",
    "work completed in accordance with plans: yes",
    "building wrap: joinery tape flashings installed as per pass",
    "pipe penetrations and installation pass",
    "we conducted a site inspection",
    "we note the following elements discussed on site",
    "had been installed",
    "after missing previously",
    "numerous recurring issues with mechanical ducting and evidence of poor coordination",
    "whilst products",
    "products.tdy",
    "drawingmaybea",
)

DEFECT_CUES = (
    "fail",
    "failed",
    "missing",
    "incomplete",
    "loose",
    "not compliant",
    "non-compliant",
    "defect",
    "rectify",
    "required",
    "requires",
    "outstanding",
    "not installed",
    "close-out",
    "less than",
    "below",
    "query",
    "to complete",
    "needs",
)

TABLE_NOISE = (
    "figure ",
    "table ",
    "assessment summary",
    "refertotable",
    "products ltd",
    "products.tdy",
    "legal proceedings",
    "termsa",
    "drawingmaybea",
    "whilst products",
)

POSITIVE_DEFECT_CUES = (
    "defect will be remediated",
    "defect was remediated",
    "not compliant",
    "non-compliant",
    "missing",
    "annular gap was less than 5mm",
    "it was noted that annular gap was less than",
    "annular gap around",
    "exceeds approved system tolerance",
)

# Construction taxonomy terms are based on NZ construction inspection language
# and NZ Building Code compliance domains. Keep these lists auditable and extend
# them as new missed terms are found in real reports.
PASSIVE_FIRE_KEYWORDS = (
    "passive fire",
    "fire stop",
    "fire-stop",
    "firestop",
    "fire stopping",
    "fire-stopping",
    "fire stopping system",
    "fire rated",
    "fire-rated",
    "fire separation",
    "firecell",
    "fire cell",
    "fire resistance rating",
    "frr",
    "fire damper",
    "fire door",
    "fire wall",
    "firewall",
    "fire alarm",
    "sprinkler",
    "warning system",
    "escape route",
    "fire engineer",
    "fire report",
    "fire seal",
    "fire sealant",
    "breakaway",
    "collar",
    "intumescent",
    "plasterboard lining",
    "bulkhead",
    "penetration seal",
    "annular gap",
    "smoke seal",
)

FIRE_DAMPER_CONTEXT_TERMS = (
    "fire rated",
    "fire-rated",
    "fire stopping",
    "fire-stopping",
    "fire separation",
    "frr",
    "fire engineer",
    "fire seal",
    "fire sealant",
)

SURFACE_WATER_KEYWORDS = (
    "surface water",
    "stormwater",
    "rainwater",
    "roof drainage",
    "site drainage",
    "gutter",
    "gutters",
    "downpipe",
    "downpipes",
    "spouting",
    "sump",
    "cesspit",
    "channel drain",
    "soakage",
    "overland flow",
    "drainage outlet",
)

WET_AREA_KEYWORDS = (
    "internal moisture",
    "wet area",
    "wet-area",
    "shower",
    "bathroom",
    "laundry",
    "bath",
    "basin",
    "tub",
    "sink",
    "impervious",
    "waterproof lining",
    "waterproof membrane",
    "water splash",
    "floor waste",
    "tile",
    "tiles",
    "grout",
    "sealant",
    "silicone",
    "sanitary fixture",
)

WATERPROOFING_KEYWORDS = (
    "waterproof",
    "waterproofing",
    "membrane",
    "tanking",
    "upstand",
    "threshold",
    "step down",
    "step-down",
    "deck/balcony",
    "deck balcony",
    "deck wall",
    "deck/wall",
    "saddle flashing",
    "drain back",
    "water entry",
    "ponding",
)

ENVELOPE_KEYWORDS = (
    "envelope",
    "roof cladding",
    "wall cladding",
    "roof",
    "cladding",
    "external opening",
    "junction",
    "junctions",
    "penetration",
    "penetrations",
    "building wrap",
    "cavity wrap",
    "wall wrap",
    "rigid air barrier",
    "rab",
    "drained cavity",
    "cavity",
    "rainscreen",
    "parapet",
    "soffit",
    "scriber",
    "batten",
    "battens",
    "flashing",
    "flashings",
    "head flashing",
    "sill flashing",
    "jamb flashing",
    "corner flashing",
    "apron flashing",
    "kick-out flashing",
    "verge flashing",
    "barge flashing",
    "wanz",
    "joinery",
    "window",
    "door opening",
    "weatherboard",
    "weathertight",
    "weather-tight",
)

MECHANICAL_KEYWORDS = (
    "mechanical",
    "mechanical ventilation",
    "natural ventilation",
    "ventilation rate",
    "air purity",
    "airflow",
    "return air",
    "supply air",
    "duct",
    "ducting",
    "ductwork",
    "flexi duct",
    "flex duct",
    "hvac",
    "ventilation",
    "grille",
    "diffuser",
    "extract fan",
    "rangehood",
    "bathroom fan",
    "toilet fan",
    "laundry fan",
    "flue",
    "gas appliance",
    "gas-fuel appliance",
    "volume control damper",
    "lagging",
    "acoustic lagging",
    "clearance",
    "services coordination",
    "clash",
    "clashing",
)

ELECTRICAL_KEYWORDS = (
    "electricity",
    "electrical",
    "electrical installation",
    "electrical supply",
    "essential service",
    "cable",
    "cabling",
    "conduit",
    "data cabling",
    "data cable",
    "data conduit",
    "metering",
    "switchboard",
    "socket",
    "outlet",
    "light switch",
    "distribution board",
    "db board",
    "rcd",
    "earth bonding",
    "earthing",
    "emergency lighting",
    "power",
    "lighting",
)

PLUMBING_KEYWORDS = (
    "plumbing",
    "sanitary plumbing",
    "hydraulic",
    "hydraulics",
    "pipework",
    "pipe work",
    "drainage",
    "waste pipe",
    "water supply",
    "cold water",
    "potable water",
    "backflow",
    "valve",
    "tap",
    "tapware",
    "water heater",
    "foul water",
    "wastewater",
    "soil pipe",
    "trap",
    "vent pipe",
    "discharge pipe",
    "sewer",
    "hot water",
    "hwc",
    "sanitary",
)

STRUCTURE_KEYWORDS = (
    "structural",
    "structure",
    "foundation",
    "footing",
    "slab",
    "subfloor",
    "floor",
    "wall framing",
    "roof framing",
    "framing",
    "frame",
    "stud",
    "joist",
    "rafter",
    "truss",
    "blocking",
    "nog",
    "beam",
    "lintel",
    "masonry",
    "blockwork",
    "steel",
    "retaining wall",
    "concrete",
    "timber-to-concrete",
    "seismic",
    "bracing",
)

DURABILITY_KEYWORDS = (
    "durability",
    "durable",
    "corrosion",
    "corroded",
    "treated timber",
    "galvanised",
    "galvanized",
    "stainless steel",
    "fixing durability",
    "5 years",
    "15 years",
    "50 years",
    "b2 durability",
)

ACCESS_SAFETY_KEYWORDS = (
    "access route",
    "stairs",
    "stair",
    "ramp",
    "landing",
    "handrail",
    "balustrade",
    "barrier",
    "guard",
    "slip resistance",
    "slip-resistant",
    "fall height",
    "fall from height",
    "threshold height",
    "opening restrictor",
)

ENERGY_EFFICIENCY_KEYWORDS = (
    "energy efficiency",
    "thermal envelope",
    "thermal resistance",
    "insulation",
    "r-value",
    "r value",
    "airtightness",
    "uncontrolled airflow",
    "hot water system",
    "thermal bridge",
)


def enrich_finding(finding: dict[str, Any]) -> dict[str, Any]:
    text = _source_text(finding)
    title = summarize_issue_title(text)
    category = categorize_issue({**finding, "title": title, "description": text})
    trade = _trade_label({**finding, "display_category": category, "title": title, "description": text})
    summary = plain_english_summary({**finding, "title": title, "description": text})
    actionable, reason = is_actionable_issue(finding)
    return {
        **finding,
        "display_title": title,
        "displayTitle": title,
        "plain_english_summary": summary,
        "plainEnglishSummary": summary,
        "trade": trade,
        "display_trade": trade,
        "displayTrade": trade,
        "display_category": category,
        "displayCategory": category,
        "is_actionable": actionable,
        "isActionable": actionable,
        "non_actionable_reason": reason,
        "nonActionableReason": reason,
    }


def enrich_findings(findings: list[dict[str, Any]], *, actionable_only: bool = False) -> list[dict[str, Any]]:
    enriched = [enrich_finding(item) for item in findings]
    if actionable_only:
        return [item for item in enriched if item["is_actionable"]]
    return enriched


def summarize_issue_title(text: str) -> str:
    cleaned = _clean_text(text)
    lowered = cleaned.lower()

    if "building wrap meets junctions" in lowered or "flashings at junctions" in lowered:
        return "Junction flashings failed inspection"
    if (
        "flashings above, below and at the sides" in lowered
        or ("head" in lowered and "sill" in lowered and "jamb" in lowered and "wanz" in lowered)
    ):
        return "Window and door flashings/support bars failed"
    if "cavity battens behind the cladding" in lowered or "cavity battens" in lowered:
        return "Cavity battens do not match consented plans"
    if "saddle flashings where decks" in lowered or "saddle flashing" in lowered:
        return "Deck/balcony saddle flashings failed inspection"
    if "height difference between the deck" in lowered or "threshold step-down" in lowered or "threshold step down" in lowered:
        return "Deck/balcony threshold step-down failed inspection"
    if "step down" in lowered and "deck/balcony" in lowered:
        return "Deck/balcony threshold step-down failed inspection"
    if "waterproof membrane at the deck" in lowered or "membrane upstand" in lowered:
        return "Deck/balcony membrane upstand is too low"
    if "timber-to-concrete" in lowered or "timber to concrete" in lowered:
        return "Timber-to-concrete junction needs flashing protection"
    if "close-out photos requested" in lowered:
        return "Close-out photos requested"
    if "kitchen conduit" in lowered and "passive fire" in lowered:
        return "Kitchen conduit passive fire close-out required"
    if "passive fire in hwc cupboard" in lowered:
        return "HWC cupboard passive fire close-out required"
    if "ryanmesh" in lowered and "fire engineer" in lowered:
        return "Ryanmesh fire separation detail requires confirmation"
    if "failed cavity wrap inspection" in lowered or "full recheck for level 1" in lowered:
        return "Failed level 1 cavity wrap junction details"
    if "deck/balcony" in lowered and "fail" in lowered:
        return "Failed deck/balcony flashing and threshold details"
    if "flashings at junctions" in lowered and "fail" in lowered:
        return "Failed flashings and cavity batten items"
    if "missing" in lowered and "close-out" in lowered and "photo" in lowered:
        return "Missing close-out photo"
    if "breakaway" in lowered and "damper" in lowered:
        return "Non-compliant fire damper breakaway fixings"
    if "fixing" in lowered and "missing" in lowered and "plasterboard" in lowered:
        return "Missing plasterboard lining fixings"
    if "annular gap" in lowered and ("less than" in lowered or "below" in lowered):
        return "Pipe penetration annular gap below approved detail"
    if "annular gap" in lowered and ("exceeds" in lowered or "excessive" in lowered or "tolerance" in lowered):
        return "Fire-stopped penetration annular gap exceeds approved tolerance"
    if "ducting and cabling too tight" in lowered:
        return "Ducting and cabling too tight against framing"
    if "flexi duct being compressed by hydraulics support" in lowered:
        return "Flexi duct compressed by hydraulics support"
    if "ducting hard pressed against frame" in lowered:
        return "Ducting pressed against frame without clearance"
    if "duct" in lowered and ("jammed against" in lowered or "no working clearance" in lowered or "insufficient clearance" in lowered):
        return "Ducting pressed against frame without clearance"
    if "duct clashing with other services" in lowered:
        return "Duct clash requires re-routing to grille"
    if "duct is squeezed by pipework" in lowered:
        return "Duct squeezed by pipework on level 2"
    if "ductwork occurring" in lowered and "re-routed" in lowered:
        return "Ductwork clash requires re-routing"
    if "flexi duct is sitting" in lowered and "not suitable" in lowered:
        return "Flexi duct support installation is not suitable"
    if "sealing tape on ductwork loose" in lowered:
        return "Loose sealing tape on ductwork"
    if "lift shaft" in lowered and "later stage" in lowered:
        return "Lift shaft penetration fire stopping pending inspection"
    if "lift door" in lowered and "gap" in lowered:
        return "Lift door frame fire stopping detail to confirm"
    if "fire rated bulkhead" in lowered or "fire-rated bulkhead" in lowered:
        return "Fire-rated bulkhead and penetrations require close-out"
    if "penetration" in lowered and "fire stop" in lowered:
        return "Fire-stopped penetration requires verification"
    if "close-out" in lowered and "photo" in lowered:
        return "Close-out evidence requested"

    sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    sentence = re.sub(r"^(further to item \d+,?\s*)", "", sentence, flags=re.IGNORECASE)
    return _clip_phrase(sentence, 120) or "Recorded inspection issue"


def categorize_issue(finding: dict[str, Any]) -> str:
    text = _source_text(finding).lower()
    inspection = str(finding.get("inspection_type") or "").lower()
    combined = f"{text} {inspection}"
    passive_fire_context = _has_passive_fire_context(combined, inspection)
    if (passive_fire_context and _has_any_term(combined, ("damper", "breakaway"))) or _has_any_term(combined, ("fire damper",)):
        return "Passive Fire - Dampers"
    if passive_fire_context and _has_any_term(combined, ("plasterboard", "bulkhead")):
        return "Passive Fire - Linings"
    if passive_fire_context and _has_any_term(combined, ("penetration", "collar", "sealant", "annular gap")):
        return "Passive Fire - Penetrations"
    if passive_fire_context and _has_any_term(combined, ("lift",)) and _has_any_term(combined, ("gap",)):
        return "Passive Fire - Lift Interfaces"
    if _has_any_term(combined, SURFACE_WATER_KEYWORDS):
        return "Surface Water / Stormwater"
    if _has_any_term(combined, WET_AREA_KEYWORDS):
        return "Internal Moisture / Wet Areas"
    if _has_any_term(combined, WATERPROOFING_KEYWORDS):
        return "Waterproofing"
    if _has_any_term(combined, ENVELOPE_KEYWORDS):
        return "Envelope"
    if _has_any_term(combined, MECHANICAL_KEYWORDS):
        return "Mechanical / Ventilation"
    if _has_any_term(combined, ELECTRICAL_KEYWORDS):
        return "Electrical"
    if _has_any_term(combined, PLUMBING_KEYWORDS):
        return "Plumbing / Drainage"
    if _has_any_term(combined, STRUCTURE_KEYWORDS):
        return "Structure"
    if _has_any_term(combined, DURABILITY_KEYWORDS):
        return "Durability"
    if _has_any_term(combined, ACCESS_SAFETY_KEYWORDS):
        return "Access / Safety"
    if _has_any_term(combined, ENERGY_EFFICIENCY_KEYWORDS):
        return "Energy Efficiency"
    return str(finding.get("category") or finding.get("trade") or "General")


def plain_english_summary(finding: dict[str, Any]) -> str:
    title = summarize_issue_title(_source_text(finding))
    location = _clean_location(finding.get("location") or finding.get("unit_label"))
    fix = _specific_required_fix(title, finding.get("required_fix"))
    if location:
        return f"{title} at {location}. {fix}"
    return f"{title}. Exact project location was not stated in the report. {fix}"


def _specific_required_fix(title: str, extracted_fix: Any) -> str:
    if isinstance(extracted_fix, str) and extracted_fix.strip():
        return extracted_fix.strip()
    lowered = title.lower()
    if "junction flashings" in lowered:
        return "Redo the junction flashings to the approved building-wrap details and provide close-out photos for council review."
    if "window and door flashings" in lowered:
        return "Install head, sill and jamb flashings plus WANZ support bars to the approved details before cavity closure."
    if "cavity battens" in lowered:
        return "Reinstall cavity battens to the consented size, spacing, treatment and fixing requirements."
    if "saddle flashings" in lowered:
        return "Install compliant saddle flashings at deck and balcony wall junctions, then request reinspection."
    if "threshold step-down" in lowered:
        return "Adjust the deck or balcony threshold step-down to match the consented detail and document the measurement."
    if "ducting pressed against frame" in lowered:
        return "Create the required duct clearance by trimming the frame if approved or rerouting the duct, then confirm the clearance with QA photos."
    if "membrane upstand" in lowered:
        return "Extend the waterproof membrane upstand to the required height and photograph it with a tape measure."
    if "timber-to-concrete" in lowered:
        return "Add the required flashing protection to exposed timber-to-concrete junctions before reinspection."
    return "Assign an owner, complete the fix, and upload close-out evidence."


def is_actionable_issue(finding: dict[str, Any]) -> tuple[bool, str | None]:
    text = _source_text(finding)
    cleaned = _clean_text(text)
    lowered = text.lower()
    if _looks_like_table_or_drawing_noise(text):
        return False, "Looks like OCR/table/drawing noise rather than an inspection action."
    if _looks_like_non_actionable_checklist_result(lowered):
        return False, "Looks like a passed or not-applicable checklist row rather than an open issue."
    if any(term in lowered for term in POSITIVE_OBSERVATIONS) and not any(term in lowered for term in POSITIVE_DEFECT_CUES):
        return False, "Looks like a positive observation rather than a defect."
    if len(cleaned) < 28 and not _has_any_term(lowered, DEFECT_CUES):
        return False, "Too short to be a useful actionable issue."
    if _has_any_term(lowered, ACTION_TERMS):
        return True, None
    if _has_any_term(lowered, ("fire stopping", "penetration", "damper", "plasterboard")) and _has_any_term(lowered, ("will", "check", "inspect", "confirm")):
        return True, None
    return False, "No clear action, defect, or close-out requirement was detected."


def group_similar_issues(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = enrich_findings(findings, actionable_only=True)
    labels = _cluster_labels([item["display_title"] for item in enriched])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for label, item in zip(labels, enriched, strict=False):
        grouped.setdefault(label, []).append(item)
    rows = []
    for label, items in grouped.items():
        rows.append(
            {
                "label": label,
                "count": len(items),
                "issueIds": [item["id"] for item in items if item.get("id")],
                "category": Counter(item.get("display_category") for item in items).most_common(1)[0][0],
                "highestSeverity": _highest_severity(items),
            }
        )
    return sorted(rows, key=lambda row: (-row["count"], row["label"]))


def _cluster_labels(titles: list[str]) -> list[str]:
    if not titles:
        return []
    try:
        from sklearn.cluster import AgglomerativeClustering
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectors = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform(titles)
        if len(titles) < 2:
            return titles
        model = AgglomerativeClustering(n_clusters=None, distance_threshold=0.65, metric="cosine", linkage="average")
        clusters = model.fit_predict(vectors.toarray())
        grouped: dict[int, list[str]] = {}
        for cluster, title in zip(clusters, titles, strict=False):
            grouped.setdefault(int(cluster), []).append(title)
        return [_representative_label(grouped[int(cluster)]) for cluster in clusters]
    except Exception:
        return [_fallback_label(title) for title in titles]


def _representative_label(titles: list[str]) -> str:
    return Counter(titles).most_common(1)[0][0]


def _fallback_label(title: str) -> str:
    lowered = title.lower()
    if "damper" in lowered:
        return "Fire damper compliance"
    if "plasterboard" in lowered or "bulkhead" in lowered:
        return "Fire-rated linings"
    if "penetration" in lowered or "annular gap" in lowered:
        return "Fire-stopped penetrations"
    return title


def _source_text(finding: dict[str, Any]) -> str:
    title = str(finding.get("title") or finding.get("issue_title") or "").strip()
    description = str(finding.get("description") or "").strip()
    source_quote = str(finding.get("source_quote") or "").strip()
    if description and (
        len(_clean_text(title)) < 28
        or title.lower() in {"suitable", "photo", "photos"}
        or _looks_cut_off(title)
    ):
        return f"{title}. {description}" if title else description
    if source_quote and _looks_cut_off(title):
        return f"{title}. {source_quote}" if title else source_quote
    if title:
        return title
    return str(finding.get("plain_english_summary") or description or source_quote or "")


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned.strip(" -*•")


def _clean_location(value: Any) -> str | None:
    cleaned = _clean_text(str(value or ""))
    if not cleaned:
        return None
    return cleaned.rstrip(" .;:")


def _trade_label(finding: dict[str, Any]) -> str:
    raw_trade = str(finding.get("trade") or "").strip()
    if raw_trade and raw_trade != "General":
        return raw_trade
    text = _source_text(finding).lower()
    inspection = str(finding.get("inspection_type") or "").lower()
    category = str(finding.get("display_category") or finding.get("category") or "").lower()
    combined = f"{text} {inspection} {category}"
    if category in {"plumbing / drainage", "surface water / stormwater", "internal moisture / wet areas"}:
        return "Plumbing"
    if category in {"envelope", "waterproofing"}:
        return "Envelope"
    if category == "mechanical / ventilation":
        return "Mechanical"
    if category == "electrical":
        return "Electrical"
    if category == "structure":
        return "Structure"
    if _has_passive_fire_context(combined, inspection) or "fire inspection" in inspection:
        return "Passive Fire"
    if _has_any_term(combined, MECHANICAL_KEYWORDS):
        return "Mechanical"
    if _has_any_term(combined, ELECTRICAL_KEYWORDS):
        return "Electrical"
    if _has_any_term(combined, PLUMBING_KEYWORDS):
        return "Plumbing"
    if _has_any_term(combined, WATERPROOFING_KEYWORDS) or _has_any_term(combined, ENVELOPE_KEYWORDS):
        return "Envelope"
    if _has_any_term(combined, STRUCTURE_KEYWORDS):
        return "Structure"
    return raw_trade or "General"


def _has_passive_fire_context(text: str, inspection: str) -> bool:
    if _has_any_term(text, ("passive fire", "fire damper")):
        return True
    if _has_any_term(inspection, ("fire", "passive fire")):
        return True
    return _has_any_term(text, FIRE_DAMPER_CONTEXT_TERMS)


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    normalized = _clean_text(text).lower()
    for term in terms:
        escaped = re.escape(term.lower())
        if " " in term or "-" in term or "/" in term:
            if re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", normalized):
                return True
        elif re.search(rf"\b{escaped}\b", normalized):
            return True
    return False


# TODO: replace first-match keyword ordering with a scored classifier using the
# extracted issue type, inspection type, source section, council inspection
# stage, and keyword confidence.


def _clip_phrase(text: str, limit: int) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned.rstrip(" ,.;:-")
    clipped = cleaned[:limit].rstrip()
    boundary = max(clipped.rfind("; "), clipped.rfind(", "), clipped.rfind(" - "), clipped.rfind(" "))
    if boundary > int(limit * 0.65):
        clipped = clipped[:boundary]
    clipped = clipped.rstrip(" ,.;:-")
    words = clipped.split()
    while words and _dangling_word(words[-1]):
        words.pop()
    return " ".join(words).rstrip(" ,.;:-")


def _looks_cut_off(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    words = cleaned.split()
    last = words[-1] if words else ""
    if _dangling_word(last):
        return True
    if len(cleaned) >= 115 and cleaned[-1] not in ".!?":
        return True
    return False


def _dangling_word(word: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9-]", "", word).lower()
    return len(normalized) == 1 or normalized in {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }


def _looks_like_table_or_drawing_noise(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.lower()
    if _has_any_term(lowered, STRONG_ACTION_TERMS):
        return False
    if "ryanfire" in lowered and any(token in lowered for token in ("cutt", "perimeter", "products ltd", "drawing", "legal proceedings")):
        return True
    if any(token in lowered for token in TABLE_NOISE):
        return True
    letters = re.sub(r"[^A-Za-z]+", "", cleaned)
    uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / max(len(letters), 1)
    return uppercase_ratio > 0.75 and len(cleaned) < 90 and not _has_any_term(lowered, ACTION_TERMS)


def _looks_like_non_actionable_checklist_result(lowered: str) -> bool:
    if any(phrase in lowered for phrase in NON_ACTIONABLE_PHRASES):
        return True
    if "inspection outcome" in lowered and "work completed in accordance" in lowered:
        return True
    has_pass = " pass" in f" {lowered} " or "( pass" in lowered
    has_defect = _has_any_term(lowered, DEFECT_CUES)
    if has_pass and not has_defect:
        return True
    return False


def _highest_severity(items: list[dict[str, Any]]) -> str:
    rank = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
    highest = "Low"
    for item in items:
        severity = str(item.get("severity") or "Low")
        if rank.get(severity, 1) > rank[highest]:
            highest = severity
    return highest
