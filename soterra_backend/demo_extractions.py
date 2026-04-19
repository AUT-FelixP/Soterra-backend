from __future__ import annotations

import re
from pathlib import Path

from .models import ExtractedFinding, ExtractionResult, PredictedInspection
from .utils import parse_report_date, plus_days


def _predicted(
    inspection_type: str,
    site_name: str,
    report_date: str,
    risk_level: str,
    source: str,
    offset_days: int,
) -> PredictedInspection:
    return PredictedInspection(
        inspection_type=inspection_type,
        site_name=site_name,
        expected_date=plus_days(report_date, offset_days),
        risk_level=risk_level,  # type: ignore[arg-type]
        source=source,
    )


def _finding(
    title: str,
    description: str,
    severity: str,
    category: str,
    trade: str,
    *,
    location: str | None = None,
    recurrence_risk: int = 30,
) -> ExtractedFinding:
    return ExtractedFinding(
        title=title,
        description=description,
        severity=severity,  # type: ignore[arg-type]
        category=category,
        trade=trade,
        location=location,
        recurrence_risk=recurrence_risk,
    )


def match_demo_extraction(filename: str, text: str) -> ExtractionResult | None:
    lowered = filename.lower()

    if "council inspection" in lowered or "cavity wrap" in text.lower():
        report_date = parse_report_date(_search(text, r"Date of Inspection\s+([0-9\-./]+)"), "2024-04-09")
        return ExtractionResult(
            project_name="Kauri Apartments",
            site_name="24 Kauri Road, Henderson",
            address="24 Kauri Road, Henderson, Auckland 0614",
            inspection_type="Council - Cavity Wrap",
            trade="Envelope",
            inspector=_search(text, r"Inspector's name\s+([A-Za-z ]+)", "Council Inspector").strip(),
            report_date=report_date,
            summary=(
                "Council cavity wrap inspection for Kauri Apartments. The report records multiple failed "
                "junction, flashing, and deck-balcony waterproofing items and calls for a full recheck."
            ),
            overall_outcome="Reviewing",
            units=["1/1", "1/2", "1/3", "1/4", "1/5", "1/6", "1/7"],
            findings=[
                _finding(
                    "Junction flashings and support bars failed",
                    "Head, sill, jamb flashings and WANZ support bars were marked as failed in the inspection summary.",
                    "Critical",
                    "Flashing",
                    "Envelope",
                    recurrence_risk=85,
                ),
                _finding(
                    "Cavity battens not installed correctly",
                    "The checklist shows cavity battens failed against the plan requirements.",
                    "High",
                    "Cavity system",
                    "Envelope",
                    recurrence_risk=70,
                ),
                _finding(
                    "Deck saddle flashing installed incorrectly",
                    "Deck and balcony saddle flashing was recorded as a failed item.",
                    "High",
                    "Deck / balcony",
                    "Envelope",
                    recurrence_risk=68,
                ),
                _finding(
                    "Threshold step-down does not match plan",
                    "The threshold step-down for the deck or balcony failed against the approved detail.",
                    "High",
                    "Deck / balcony",
                    "Envelope",
                    recurrence_risk=62,
                ),
                _finding(
                    "Membrane upstand below minimum height",
                    "The deck or balcony membrane support upstand was below the 150mm minimum requirement.",
                    "Critical",
                    "Waterproofing",
                    "Envelope",
                    recurrence_risk=90,
                ),
                _finding(
                    "Timber-to-concrete junction left exposed",
                    "Additional comments note exposed timber-to-concrete butt joins without the required flashing protection.",
                    "High",
                    "Waterproofing",
                    "Envelope",
                    recurrence_risk=75,
                ),
                _finding(
                    "Membrane upstands not lapped onto RAB board",
                    "Membrane upstands stop below the bottom plate and rely only on tape at the junction.",
                    "Critical",
                    "Waterproofing",
                    "Envelope",
                    recurrence_risk=88,
                ),
            ],
            predicted_inspections=[
                _predicted(
                    "Council Recheck",
                    "24 Kauri Road, Henderson",
                    report_date,
                    "High",
                    "Failed cavity wrap inspection requires a full recheck.",
                    7,
                )
            ],
        )

    if "fire inspection" in lowered or "can fire" in text.lower():
        report_date = parse_report_date(_search(text, r"Date:\s+([0-9A-Za-z ]+)"), "2024-04-09")
        return ExtractionResult(
            project_name="Kauri Apartments",
            site_name="Ground level corridor",
            address="Kauri Apartments, Auckland",
            inspection_type="Fire Inspection",
            trade="Passive Fire",
            inspector="Fire Consultant",
            report_date=report_date,
            summary=(
                "Passive fire stopping inspection for the ground floor corridor. The report highlights "
                "non-compliant damper fixings, incomplete plasterboard lining installation, and close-out items."
            ),
            overall_outcome="Reviewing",
            findings=[
                _finding(
                    "Breakaway joint fixings on fire dampers are non-compliant",
                    "The consultant recorded non-compliant fixings on fire damper breakaway joints.",
                    "Critical",
                    "Damper compliance",
                    "Passive Fire",
                    recurrence_risk=82,
                ),
                _finding(
                    "Top plasterboard lining fixings missing",
                    "Topmost plasterboard linings were missing fixings and need fire sealing at the slab rib junction.",
                    "High",
                    "Plasterboard lining",
                    "Passive Fire",
                    recurrence_risk=71,
                ),
                _finding(
                    "Bottom plasterboard lining fixings missing",
                    "Bottom fixings on plasterboard linings were missing and need correction.",
                    "High",
                    "Plasterboard lining",
                    "Passive Fire",
                    recurrence_risk=65,
                ),
                _finding(
                    "Close-out evidence required for previous CAN fire items",
                    "The report requests close-out photos for items 3, 4, 5 and 10 from the prior consultant advice notice.",
                    "Medium",
                    "Documentation",
                    "Passive Fire",
                    recurrence_risk=54,
                ),
                _finding(
                    "Lift door frame fire stop detail pending confirmation",
                    "The lift shaft response notes that the final fire stop solution must comply with the referenced Ryanfire drawings.",
                    "Medium",
                    "Lift shaft fire stop",
                    "Passive Fire",
                    recurrence_risk=48,
                ),
            ],
            predicted_inspections=[
                _predicted(
                    "Fire Reinspection",
                    "Ground level corridor",
                    report_date,
                    "High",
                    "Passive fire defects and close-out photos still need confirmation.",
                    10,
                )
            ],
        )

    if "services inspection" in lowered or "#55 mechanical" in text.lower():
        report_date = parse_report_date(_search(text, r"DATE\s+([0-9/.\-]+)"), "2024-04-09")
        findings = [
            _finding(
                "Ducting pressed hard against framing",
                "Main contractor to investigate trimming the frame so the duct has acceptable clearance.",
                "High",
                "Mechanical ducting",
                "Mechanical",
                recurrence_risk=67,
            ),
            _finding(
                "Cabling crushed against framing by duct route",
                "Ducting and cabling are too tight and risk wearing down the cabling.",
                "Critical",
                "Service coordination",
                "Mechanical",
                recurrence_risk=82,
            ),
            _finding(
                "Flexi duct supported from framing and electrical services",
                "Flexi duct is resting on framing and cable-tied to electrical services, which is not acceptable.",
                "High",
                "Mechanical support",
                "Mechanical",
                recurrence_risk=74,
            ),
            _finding(
                "Sealing tape loose on ductwork",
                "Loose sealing tape needs correcting to restore a compliant duct seal.",
                "Medium",
                "Mechanical sealing",
                "Mechanical",
                recurrence_risk=39,
            ),
            _finding(
                "Duct clashes with other services before grille connection",
                "Mechanical ductwork needs rerouting to provide a smooth future grille connection.",
                "High",
                "Service coordination",
                "Mechanical",
                recurrence_risk=78,
            ),
            _finding(
                "Kitchen extract flexi route squeezed by pipework",
                "Current flexi route to kitchen extract is restricted by nearby pipework across several apartments.",
                "Critical",
                "Service coordination",
                "Mechanical",
                recurrence_risk=87,
            ),
            _finding(
                "Excessive looping of flexi ductwork",
                "Flexi ductwork has excessive looping and must be rerouted.",
                "Medium",
                "Mechanical ducting",
                "Mechanical",
                recurrence_risk=52,
            ),
            _finding(
                "Flexi duct compressed by hydraulics support",
                "Flexi duct requires rerouting so it is not compressed and seismic clearances are maintained.",
                "High",
                "Mechanical ducting",
                "Mechanical",
                recurrence_risk=71,
            ),
            _finding(
                "No isolation provided on apartment water supply",
                "Water supplies to apartments do not have isolation as required by the drawings.",
                "High",
                "Plumbing isolation",
                "Plumbing",
                recurrence_risk=64,
            ),
            _finding(
                "Services lack the required 50mm vertical clearance",
                "Services do not have sufficient clearance as required by NZS 4219.",
                "High",
                "Service coordination",
                "Plumbing",
                recurrence_risk=76,
            ),
            _finding(
                "Drainage pipework lagging issue recurring on level 2",
                "Lagging requirements are not met on drainage pipework visible on level 2.",
                "Medium",
                "Plumbing lagging",
                "Plumbing",
                recurrence_risk=58,
            ),
            _finding(
                "Hot water cylinder metering needs verification",
                "Metering requires a site check, photo evidence, and inclusion in as-built information.",
                "Medium",
                "Documentation",
                "Plumbing",
                recurrence_risk=42,
            ),
            _finding(
                "Pipework into fire-rated risers not confirmed as fire-collared",
                "Pipework between floors and into fire-rated risers must be appropriately fire-collared.",
                "High",
                "Passive fire interface",
                "Plumbing",
                recurrence_risk=73,
            ),
            _finding(
                "Pipework acoustic lagging incomplete",
                "All drainage pipework needs acoustic lagging in line with the hydraulics specification.",
                "Medium",
                "Plumbing lagging",
                "Plumbing",
                recurrence_risk=55,
            ),
            _finding(
                "Ducting supported by cable ties",
                "Cable ties are being used as a recurring support method for ducting and are not acceptable.",
                "High",
                "Mechanical support",
                "Mechanical",
                recurrence_risk=79,
            ),
            _finding(
                "AC pipework not yet installed for level 2 apartments",
                "AC pipework remains outstanding across level 2 apartments.",
                "High",
                "Mechanical installation",
                "Mechanical",
                recurrence_risk=66,
            ),
        ]
        return ExtractionResult(
            project_name="Kauri Apartments",
            site_name="Level 3 Mechanical and Hydraulics / Level 2 Data",
            address="Kauri Apartments, Auckland",
            inspection_type="Services Inspection",
            trade="Mechanical",
            inspector="Services Consultant",
            report_date=report_date,
            summary=(
                "Services coordination inspection covering mechanical, plumbing, and electrical works. "
                "The report identifies recurring ductwork coordination failures, support problems, and incomplete plumbing items."
            ),
            overall_outcome="Reviewing",
            findings=findings,
            predicted_inspections=[
                _predicted(
                    "Services Reinspection",
                    "Level 3 Mechanical and Hydraulics / Level 2 Data",
                    report_date,
                    "High",
                    "Recurring coordination issues across mechanical and plumbing services.",
                    14,
                ),
                _predicted(
                    "Fire Interface Check",
                    "Level 3 Mechanical and Hydraulics / Level 2 Data",
                    report_date,
                    "Medium",
                    "Pipework into fire-rated risers still needs compliant collar verification.",
                    21,
                ),
            ],
        )

    return None


def fallback_demo_extraction(filename: str, text: str) -> ExtractionResult:
    project_name = _search(text, r"(Kauri Apartments)", "Uploaded inspection")
    report_date = parse_report_date(_search(text, r"([0-9]{2}[-/.][0-9]{2}[-/.][0-9]{2,4})"), "2024-04-09")
    inspection_type = _search(text, r"Inspection Type(?: Code)?\s+([A-Za-z /()-]+)", Path(filename).stem)
    trade = "General"
    summary = (
        "Fallback extraction was used because this file does not match one of the curated demo report profiles. "
        "The document should still be reviewed manually before production use."
    )

    descriptions = re.findall(r"Description\s+(.+)", text)
    findings = [
        _finding(
            title=description[:80],
            description=description,
            severity="Medium",
            category="General",
            trade=trade,
            recurrence_risk=35,
        )
        for description in descriptions[:8]
    ]

    if not findings:
        findings = [
            _finding(
                "Manual review required",
                "No structured issue lines were found in the fallback extractor. Review the uploaded PDF before trusting the metrics.",
                "Medium",
                "General",
                trade,
                recurrence_risk=20,
            )
        ]

    return ExtractionResult(
        project_name=project_name,
        site_name="Uploaded inspection",
        address=None,
        inspection_type=inspection_type,
        trade=trade,
        inspector="Soterra Demo Extractor",
        report_date=report_date,
        summary=summary,
        overall_outcome="Reviewing",
        findings=findings,
        predicted_inspections=[],
    )


def _search(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return default
    value = next((item for item in match.groups() if item), default)
    return value.strip()

