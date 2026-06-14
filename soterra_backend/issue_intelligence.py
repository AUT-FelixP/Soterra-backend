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
)


def enrich_finding(finding: dict[str, Any]) -> dict[str, Any]:
    text = _source_text(finding)
    title = summarize_issue_title(text)
    category = categorize_issue({**finding, "title": title, "description": text})
    summary = plain_english_summary({**finding, "title": title, "description": text})
    actionable, reason = is_actionable_issue(finding)
    return {
        **finding,
        "display_title": title,
        "displayTitle": title,
        "plain_english_summary": summary,
        "plainEnglishSummary": summary,
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
    if "ducting and cabling too tight" in lowered:
        return "Ducting and cabling too tight against framing"
    if "flexi duct being compressed by hydraulics support" in lowered:
        return "Flexi duct compressed by hydraulics support"
    if "ducting hard pressed against frame" in lowered:
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
    return sentence[:120].rstrip(" ,.;:-") or "Recorded inspection issue"


def categorize_issue(finding: dict[str, Any]) -> str:
    text = _source_text(finding).lower()
    if "damper" in text or "breakaway" in text:
        return "Passive Fire - Dampers"
    if "plasterboard" in text or "bulkhead" in text:
        return "Passive Fire - Linings"
    if "penetration" in text or "collar" in text or "sealant" in text or "annular gap" in text:
        return "Passive Fire - Penetrations"
    if "lift" in text and "gap" in text:
        return "Passive Fire - Lift Interfaces"
    if "duct" in text:
        return "Mechanical Ducting"
    if "pipe" in text or "plumbing" in text:
        return "Plumbing"
    return str(finding.get("category") or finding.get("trade") or "General")


def plain_english_summary(finding: dict[str, Any]) -> str:
    title = summarize_issue_title(_source_text(finding))
    location = finding.get("location") or finding.get("unit_label")
    fix = finding.get("required_fix") or "Assign an owner, complete the fix, and upload close-out evidence."
    if location:
        return f"{title} at {location}. {fix}"
    return f"{title}. Exact project location was not stated in the report. {fix}"


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
    if len(cleaned) < 28 and not any(term in lowered for term in DEFECT_CUES):
        return False, "Too short to be a useful actionable issue."
    if any(term in lowered for term in ACTION_TERMS):
        return True, None
    if any(term in lowered for term in ("fire stopping", "penetration", "damper", "plasterboard")) and any(term in lowered for term in ("will", "check", "inspect", "confirm")):
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
    title = str(finding.get("issue_title") or finding.get("title") or "").strip()
    description = str(finding.get("description") or "").strip()
    if description and (len(_clean_text(title)) < 28 or title.lower() in {"suitable", "photo", "photos"}):
        return f"{title}. {description}" if title else description
    return str(
        finding.get("plain_english_summary")
        or title
        or description
        or ""
    )


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned.strip(" -*•")


def _looks_like_table_or_drawing_noise(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.lower()
    if any(term in lowered for term in STRONG_ACTION_TERMS):
        return False
    if "ryanfire" in lowered and any(token in lowered for token in ("cutt", "perimeter", "products ltd", "drawing", "legal proceedings")):
        return True
    if any(token in lowered for token in TABLE_NOISE):
        return True
    letters = re.sub(r"[^A-Za-z]+", "", cleaned)
    uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / max(len(letters), 1)
    return uppercase_ratio > 0.75 and len(cleaned) < 90 and not any(term in lowered for term in ACTION_TERMS)


def _looks_like_non_actionable_checklist_result(lowered: str) -> bool:
    if any(phrase in lowered for phrase in NON_ACTIONABLE_PHRASES):
        return True
    if "inspection outcome" in lowered and "work completed in accordance" in lowered:
        return True
    has_pass = " pass" in f" {lowered} " or "( pass" in lowered
    has_defect = any(term in lowered for term in DEFECT_CUES)
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
