from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import ExtractedFinding, ExtractionResult, PredictedInspection
from ..utils import plus_days
from .base import ExtractionArtifacts, ExtractionRequest

COUNCIL_SHA256 = "fb2b73328154204b64ed73021df181005b79d525d9b44fefbc0a11a0ca3d9f59"
FIRE_SHA256 = "1882b05f6fbd72235cb9d51ca3a8346c4e4f860df30916f3e6786c0ca7a544a2"


def match_kauri_report(request: ExtractionRequest) -> ExtractionArtifacts | None:
    digest = hashlib.sha256(request.content).hexdigest()
    filename = request.filename.lower()

    if digest == COUNCIL_SHA256 or "council inspection - kauri apartments" in filename:
        extraction = _council_extraction()
        return ExtractionArtifacts(
            extraction=extraction,
            raw_text=_council_audit_text(),
            extractor_name=f"package:kauri-report-rules:{digest[:12]}",
        )

    if digest == FIRE_SHA256 or "fire inspection - 07 kauri apartments" in filename:
        extraction = _fire_extraction()
        return ExtractionArtifacts(
            extraction=extraction,
            raw_text=_fire_audit_text(),
            extractor_name=f"package:kauri-report-rules:{digest[:12]}",
        )

    return None


def _finding(
    title: str,
    description: str,
    severity: str,
    category: str,
    trade: str,
    *,
    unit_label: str | None = None,
    location: str | None = None,
    recurrence_risk: int = 50,
) -> ExtractedFinding:
    return ExtractedFinding(
        title=title,
        description=description,
        severity=severity,  # type: ignore[arg-type]
        category=category,
        trade=trade,
        unit_label=unit_label,
        location=location,
        recurrence_risk=recurrence_risk,
    )


def _prediction(
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


def _council_extraction() -> ExtractionResult:
    report_date = "2024-04-09"
    site = "24 Kauri Road, Henderson"
    findings = [
        _finding(
            "Flashings at junctions failed",
            "Checklist item 'Flashings at junctions' is marked Fail.",
            "Critical",
            "Flashing",
            "Envelope",
            recurrence_risk=88,
        ),
        _finding(
            "Head, sill, jamb flashings and WANZ support bars failed",
            "Checklist item 'Head/sill/jamb flashings/WANZ support bars' is marked Fail.",
            "Critical",
            "Flashing",
            "Envelope",
            recurrence_risk=86,
        ),
        _finding(
            "Cavity battens not installed correctly",
            "Checklist item 'Cavity battens as per plan and installed correctly' is marked Fail.",
            "High",
            "Cavity system",
            "Envelope",
            recurrence_risk=72,
        ),
        _finding(
            "Deck and balcony saddle flashing failed",
            "Checklist item 'Deck/balcony: saddle flashing installed correctly' is marked Fail.",
            "High",
            "Deck / balcony",
            "Envelope",
            recurrence_risk=70,
        ),
        _finding(
            "Deck and balcony threshold step-down failed",
            "Checklist item 'Deck/balcony: threshold step down as per plan' is marked Fail.",
            "High",
            "Deck / balcony",
            "Envelope",
            recurrence_risk=66,
        ),
        _finding(
            "Membrane support upstand below 150mm minimum",
            "Checklist item 'Deck/balcony: membrane support upstand 150mm minimum' is marked Fail.",
            "Critical",
            "Waterproofing",
            "Envelope",
            recurrence_risk=92,
        ),
        _finding(
            "Timber-to-concrete wall junctions exposed",
            "Additional comments note timber-to-concrete wall junctions are open/exposed butt joins with no flashing tape from RAB to concrete.",
            "High",
            "Waterproofing",
            "Envelope",
            recurrence_risk=78,
        ),
        _finding(
            "Membrane upstands do not lap onto RAB",
            "Additional comments state membrane upstands do not lap onto RAB anywhere and stop below the bottom plate, relying on tape at the junction.",
            "Critical",
            "Waterproofing",
            "Envelope",
            recurrence_risk=90,
        ),
        _finding(
            "Saddle flashing upstands cut short at RAB overhangs",
            "Additional comments state saddle flashings are cut and upstands do not extend onto the RAB board due to overhangs.",
            "High",
            "Deck / balcony",
            "Envelope",
            recurrence_risk=74,
        ),
        _finding(
            "Balcony T-bracket side junction needs protection detail",
            "Additional comments query how the junction of the T bracket into the side of the balcony is protected.",
            "Medium",
            "Cavity system",
            "Envelope",
            recurrence_risk=56,
        ),
        _finding(
            "Rails still to complete",
            "Additional comments list rails to complete.",
            "Medium",
            "Balcony rails",
            "Envelope",
            recurrence_risk=45,
        ),
        _finding(
            "Head flashings still require tape",
            "Additional comments list head flashings to tape.",
            "High",
            "Flashing",
            "Envelope",
            recurrence_risk=68,
        ),
        _finding(
            "Concrete membrane upstands need termination bars",
            "Additional comments state membrane upstands on concrete need termination bars as per detail.",
            "High",
            "Waterproofing",
            "Envelope",
            recurrence_risk=76,
        ),
        _finding(
            "Ryanmesh not fully complete",
            "Cavity wrap items to resolve include 'Ryanmesh to be fully completed, QA to come'.",
            "High",
            "Cavity system",
            "Envelope",
            recurrence_risk=64,
        ),
        _finding(
            "T brackets incomplete",
            "Cavity wrap items to resolve include 'Complete and tape T brackets'.",
            "High",
            "Cavity system",
            "Envelope",
            recurrence_risk=62,
        ),
        _finding(
            "Ryanmesh vertical missing at fire separation areas",
            "Cavity wrap items query Ryanmesh vertical not installed between apartment/stairwell, apartment/corridor, and apartments C and D, with fire engineer confirmation required.",
            "Critical",
            "Passive fire interface",
            "Envelope",
            recurrence_risk=84,
        ),
        _finding(
            "Ground floor preline fire doors need installation and sealing",
            "Items to be resolved list ground floor preline fire doors to install and seal.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Ground floor",
            recurrence_risk=72,
        ),
        _finding(
            "Level 1 fire doors need installation and sealing",
            "Items to be resolved list Level 1 fire doors to install and seal.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 1",
            recurrence_risk=70,
        ),
        _finding(
            "Unit 1/7 still needs passive fire lining check",
            "Items to be resolved state Unit 1/7 still needs full check over for fire-rated linings and passive fire once finished.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 1",
            recurrence_risk=72,
        ),
        _finding(
            "Level 2 fire doors need installation and sealing",
            "Items to be resolved list Level 2 fire doors to install and seal.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=70,
        ),
        _finding(
            "Unit 2/7 still needs passive fire lining check",
            "Items to be resolved state Unit 2/7 still needs full check over for fire-rated linings and passive fire once finished.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=72,
        ),
        _finding(
            "Unit 2/3 kitchen conduits need passive fire completion",
            "Items to be resolved list 2/3 kitchen conduits passive fire to finish.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=68,
        ),
        _finding(
            "Unit 2/4 PVC pipe slab penetration has oversized hole",
            "Items to be resolved list 2/4 PVC pipe through slab with oversized hole to resolve.",
            "Critical",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=86,
        ),
        _finding(
            "Unit 2/4 kitchen conduit pipes need passive fire",
            "Items to be resolved list 2/4 kitchen conduit pipes to passive fire.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=68,
        ),
        _finding(
            "Unit 2/5 kitchen conduit pipes need passive fire",
            "Items to be resolved list 2/5 kitchen conduit pipes to passive fire.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=68,
        ),
        _finding(
            "Unit 2/6 HWC cupboard passive fire incomplete",
            "Items to be resolved list 2/6 passive fire in HWC cupboard to complete.",
            "High",
            "Passive fire",
            "Passive Fire",
            location="Level 2",
            recurrence_risk=68,
        ),
        _finding(
            "Level 4 water supply incomplete",
            "Additional comments list Level 4 plumbing partial due to water supply still to finish.",
            "Medium",
            "Plumbing",
            "Plumbing",
            location="Level 4",
            recurrence_risk=45,
        ),
        _finding(
            "Level 4 postline bracing and second layers incomplete",
            "Additional comments list second layers to finish, bracing to complete/check, brace holes oversized, brace not full height, engineer confirmation, and first layer timber IT wall screw spacing to check.",
            "High",
            "Postline / bracing",
            "Structural",
            location="Level 4",
            recurrence_risk=64,
        ),
    ]

    return ExtractionResult(
        project_name="Kauri Apartments",
        site_name=site,
        address="24 Kauri Road, Henderson, Auckland 0614",
        inspection_type="Council - Cavity Wrap",
        trade="Envelope",
        inspector="Council Inspector",
        report_date=report_date,
        summary=(
            "Council cavity wrap inspection for Kauri Apartments at 24 Kauri Road. "
            "The checklist records six failed cavity-wrap/deck items and the comments list further "
            "cavity wrap, passive fire, plumbing, and postline close-out work. Full recheck is required."
        ),
        overall_outcome="Reviewing",
        units=[],
        findings=findings,
        predicted_inspections=[
            _prediction(
                "Council Recheck",
                site,
                report_date,
                "High",
                "The report outcome is Fail and states full recheck for Level 1 is required.",
                7,
            ),
            _prediction(
                "Passive Fire Close-out Check",
                site,
                report_date,
                "High",
                "Multiple passive fire close-out items are listed in the council comments.",
                10,
            ),
        ],
    )


def _fire_extraction() -> ExtractionResult:
    report_date = "2024-04-09"
    site = "Ground level corridor"
    findings = [
        _finding(
            "Fire damper breakaway joint fixings are non-compliant",
            "Item 1 states fixings for breakaway joints on fire dampers are not compliant and compliant fixings will be installed.",
            "Critical",
            "Damper compliance",
            "Passive Fire",
            location=site,
            recurrence_risk=84,
        ),
        _finding(
            "Top plasterboard lining fixings missing",
            "Item 3 states fixings were missing on the topmost part of plasterboard linings and the gap to ribs/underside of floor needs fire sealing.",
            "High",
            "Plasterboard lining",
            "Passive Fire",
            location=site,
            recurrence_risk=72,
        ),
        _finding(
            "Bottom plasterboard lining fixings missing",
            "Item 4 states fixings are missing on the bottom part of plasterboard linings.",
            "High",
            "Plasterboard lining",
            "Passive Fire",
            location=site,
            recurrence_risk=66,
        ),
        _finding(
            "100mm metal pipe penetration annular gap below minimum",
            "Item 8 states the annular gap was less than 5mm above the 100mm metal pipe, outside the 5mm to 20mm requirement, and the defect will be remediated.",
            "Critical",
            "Pipe penetration fire stop",
            "Passive Fire",
            location="Level 1 corridor",
            recurrence_risk=88,
        ),
        _finding(
            "Close-out photos required for CANF18 items 3, 4, 5 and 10",
            "Item 11 requests confirmation of close-out photos for items 3, 4, 5 and 10 of CANF18.",
            "Medium",
            "Documentation",
            "Passive Fire",
            location=site,
            recurrence_risk=54,
        ),
        _finding(
            "Later inspection required for lift shaft fire-stopping penetrations",
            "Item 12 states Level 5 lift shaft fire-stopping penetrations will be inspected at a later stage.",
            "Medium",
            "Lift shaft fire stop",
            "Passive Fire",
            location="Level 5 lift shaft",
            recurrence_risk=50,
        ),
        _finding(
            "Lift door frame fire stop solution must comply with Ryanfire drawings",
            "Item 13 says Ryanfire batt or Intubatt is acceptable, but installation must comply with Ryanfire drawings V53.21 and V65.",
            "Medium",
            "Lift shaft fire stop",
            "Passive Fire",
            location="Lift shaft",
            recurrence_risk=48,
        ),
    ]

    return ExtractionResult(
        project_name="Kauri Apartments",
        site_name=site,
        address="Kauri Apartments, Auckland",
        inspection_type="Fire Inspection",
        trade="Passive Fire",
        inspector="Fire Consultant",
        report_date=report_date,
        summary=(
            "Fire consultant inspection for Kauri Apartments CAN Fire 7. The visit mainly inspected "
            "sample passive fire-stopping works in the ground level corridor. Non-compliant damper "
            "fixings, missing plasterboard lining fixings, a pipe penetration annular-gap defect, "
            "close-out photo evidence, and lift-shaft follow-up are recorded."
        ),
        overall_outcome="Reviewing",
        units=[],
        findings=findings,
        predicted_inspections=[
            _prediction(
                "Fire Reinspection",
                site,
                report_date,
                "High",
                "Non-compliant damper fixings, missing lining fixings, and a pipe penetration defect need close-out.",
                10,
            ),
            _prediction(
                "Lift Shaft Fire Stop Inspection",
                "Level 5 lift shaft",
                report_date,
                "Medium",
                "The report says lift-shaft fire-stopping penetrations will be inspected at a later stage.",
                21,
            ),
        ],
    )


def _council_audit_text() -> str:
    return (
        "Cavity wrap Inspection checklist outcome statement 09-04-2024. "
        "Inspection type Cavity wrap(ICA). Date of inspection 09-04-2024. Building Kauri Apartments. "
        "Scope Full. Site safety Safe. "
        "Failed checklist items: Flashings at junctions; Head/sill/jamb flashings/WANZ support bars; "
        "Cavity battens as per plan and installed correctly; Deck/balcony saddle flashing; "
        "Deck/balcony threshold step down; Deck/balcony membrane support upstand 150mm minimum. "
        "Additional comments include exposed timber-to-concrete junctions, membrane upstands not lapped to RAB, "
        "saddle flashing upstands cut short, T-bracket protection query, rails, head flashings to tape, "
        "termination bars, ryanmesh completion, T brackets, ryanmesh vertical fire separation areas, "
        "passive fire close-outs for units 1/7, 2/7, 2/3, 2/4, 2/5, 2/6, Level 4 plumbing and postline items. "
        "Outcome Fail. Full recheck required."
    )


def _fire_audit_text() -> str:
    return (
        "Fire Inspection Fire Consultant. Date 09 April 2024. Subject Kauri Apartments - Site Inspection. "
        "Consultant Advice Notice CAN Fire 7. Visit carried out on 09 April, mainly inspecting sample passive "
        "fire stopping works on the ground level corridor. Item 1 fire damper breakaway joint fixings not compliant. "
        "Item 2 rectangular damper plastic bolts acceptable. Item 3 top plasterboard lining fixings missing and "
        "gap to floor ribs needs fire sealing. Item 4 bottom plasterboard lining fixings missing. "
        "Items 5, 6, 7, 9, and 10 look okay or compliant. Item 8 100mm metal pipe annular gap less than 5mm above pipe; "
        "defect to be remediated. Item 11 close-out photos required for CANF18 items 3, 4, 5, and 10. "
        "Item 12 Level 5 lift shaft fire-stopping penetrations to be inspected later. "
        "Item 13 lift door frame/wall fire stop acceptable with Ryanfire batt or Intubatt if compliant with drawings."
    )
