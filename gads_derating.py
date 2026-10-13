
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional

# --------------------------------------------------------------------------
# Event type classification (GADS conventional event types)
# --------------------------------------------------------------------------
FORCED_DERATINGS = {"D1", "D2", "D3"}
MAINTENANCE_DERATINGS = {"D4", "DM"}
PLANNED_DERATINGS = {"PD", "DP"}
LEGACY_EXT_DERATINGS = {"DE"}  # pre-2020s "derating extension" (PD or D4 parent)
DERATINGS = (
    FORCED_DERATINGS | MAINTENANCE_DERATINGS | PLANNED_DERATINGS | LEGACY_EXT_DERATINGS
)

FORCED_OUTAGES = {"U1", "U2", "U3", "SF"}
MAINTENANCE_OUTAGES = {"MO", "ME"}
PLANNED_OUTAGES = {"PO", "PE"}
LEGACY_EXT_OUTAGES = {"SE"}
OUTAGES = FORCED_OUTAGES | MAINTENANCE_OUTAGES | PLANNED_OUTAGES | LEGACY_EXT_OUTAGES

RESERVE_SHUTDOWN = {"RS"}
# Reported but with no effect on this analysis (non-curtailing, inactive states)
IGNORED = {"NC", "IR", "MB", "RU", "PU", "CO"}


def category(event_type: str) -> str:
    t = event_type.upper()
    if t in FORCED_DERATINGS:
        return "forced_derating"
    if t in MAINTENANCE_DERATINGS:
        return "maintenance_derating"
    if t in PLANNED_DERATINGS:
        return "planned_derating"
    if t in LEGACY_EXT_DERATINGS:
        return "extension_derating"
    if t in FORCED_OUTAGES:
        return "forced_outage"
    if t in MAINTENANCE_OUTAGES:
        return "maintenance_outage"
    if t in PLANNED_OUTAGES:
        return "planned_outage"
    if t in LEGACY_EXT_OUTAGES:
        return "extension_outage"
    if t in RESERVE_SHUTDOWN:
        return "reserve_shutdown"
    return "other"


def round_half_up(value: float, places: int = 2) -> float:
    """Round the way the DRI's worked examples do (0.125 -> 0.13).

    Python's built-in round() uses banker's rounding and would give 0.12.
    """
    q = Decimal(1).scaleb(-places)
    # pre-round to strip binary float noise (e.g. 0.12499999999999999)
    return float(Decimal(repr(round(float(value), 9))).quantize(q, rounding=ROUND_HALF_UP))


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    """One GADS Event Report (07), records 01-03, reduced to what matters here."""

    event_number: str
    event_type: str
    start: datetime
    end: datetime
    nac: Optional[float] = None          # Net Available Capacity as a result of the event (MW)
    dominant: bool = False               # Record 01 dominant derating column = "D"
    cause_code: Optional[str] = None
    work_started: Optional[datetime] = None
    work_completed: Optional[datetime] = None
    man_hours: Optional[float] = None

    @property
    def etype(self) -> str:
        return self.event_type.upper()

    @property
    def is_derating(self) -> bool:
        return self.etype in DERATINGS

    @property
    def is_outage(self) -> bool:
        return self.etype in OUTAGES

    @property
    def is_rs(self) -> bool:
        return self.etype in RESERVE_SHUTDOWN


@dataclass
class EventResult:
    event: Event
    category: str
    duration_hours: float
    repair_hours: Optional[float]
    # Derating-only fields ---------------------------------------------------
    reference_nac: Optional[float] = None   # capacity the reduction is measured from
    reduction_mw: Optional[float] = None    # size of reduction (constant over the event)
    charged_hours: float = 0.0              # hours actually charged to this event
    shadowed_hours: float = 0.0             # hours hidden by an outage / dominant derating
    shadowed_by: List[str] = field(default_factory=list)
    mwh_lost: float = 0.0
    equivalent_hours: float = 0.0           # Equivalent Derated Hours (all)
    equivalent_hours_rs: float = 0.0        # ...portion that fell during reserve shutdown
    warnings: List[str] = field(default_factory=list)

    @property
    def equivalent_hours_in_service(self) -> float:
        return self.equivalent_hours - self.equivalent_hours_rs


@dataclass(frozen=True)
class Segment:
    """A slice of time over which nothing starts or ends."""

    start: datetime
    end: datetime
    unit_nac: float
    status: str                      # OUTAGE | RESERVE_SHUTDOWN | AVAILABLE
    charged_to: tuple                # event numbers bearing the loss in this slice

    @property
    def hours(self) -> float:
        return _hours(self.start, self.end)


@dataclass
class UnitAnalysis:
    ndc: float
    nmc: float
    results: Dict[str, EventResult]
    segments: List[Segment]
    warnings: List[str]

    def __getitem__(self, event_number: str) -> EventResult:
        return self.results[str(event_number)]

    def summary(self) -> Dict[str, float]:
        """Roll-up in Appendix F vocabulary (hours)."""
        s = {k: 0.0 for k in (
            "EFDH", "EMDH", "EPDH", "EXTDH", "EUDH", "ESDH", "EDH",
            "EFDHRS", "EMDHRS", "EPDHRS",
            "FOH", "MOH", "POH", "RSH",
        )}
        for r in self.results.values():
            c = r.category
            if c == "forced_derating":
                s["EFDH"] += r.equivalent_hours
                s["EFDHRS"] += r.equivalent_hours_rs
            elif c == "maintenance_derating":
                s["EMDH"] += r.equivalent_hours
                s["EMDHRS"] += r.equivalent_hours_rs
            elif c == "planned_derating":
                s["EPDH"] += r.equivalent_hours
                s["EPDHRS"] += r.equivalent_hours_rs
            elif c == "extension_derating":
                s["EXTDH"] += r.equivalent_hours
            elif c == "forced_outage":
                s["FOH"] += r.duration_hours
            elif c in ("maintenance_outage", "extension_outage"):
                s["MOH"] += r.duration_hours
            elif c == "planned_outage":
                s["POH"] += r.duration_hours
            elif c == "reserve_shutdown":
                s["RSH"] += r.duration_hours
        s["EUDH"] = s["EFDH"] + s["EMDH"]                 # unplanned
        s["ESDH"] = s["EMDH"] + s["EPDH"] + s["EXTDH"]    # scheduled
        s["EDH"] = s["EFDH"] + s["EMDH"] + s["EPDH"] + s["EXTDH"]
        return s

    def nac_profile(self) -> List[tuple]:
        """Unit NAC over time with adjacent equal slices merged: (start, end, MW)."""
        out: List[list] = []
        for seg in self.segments:
            if out and out[-1][1] == seg.start and out[-1][2] == seg.unit_nac:
                out[-1][1] = seg.end
            else:
                out.append([seg.start, seg.end, seg.unit_nac])
        return [tuple(x) for x in out]


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
def analyze(events: Iterable[Event], ndc: float, nmc: Optional[float] = None) -> UnitAnalysis:
    """Analyze one unit's events.

    ndc : Net Dependable Capacity (MW) - reductions are measured from this.
    nmc : Net Maximum Capacity (MW)   - equivalent hours are divided by this.
          Defaults to ndc.
    """
    nmc = ndc if nmc is None else nmc
    if ndc <= 0 or nmc <= 0:
        raise ValueError("ndc and nmc must be positive")

    evs = sorted(events, key=lambda e: (e.start, str(e.event_number)))
    unit_warnings: List[str] = []
    results: Dict[str, EventResult] = {}

    for e in evs:
        key = str(e.event_number)
        if key in results:
            raise ValueError(f"duplicate event number {key}")
        if e.end <= e.start:
            raise ValueError(f"event {key}: end must be after start")
        repair = None
        if not e.is_rs or e.cause_code:
            ws = e.work_started or e.start      # blank work times -> event times (Ex. 3A)
            wc = e.work_completed or e.end
            repair = _hours(ws, wc)
        r = EventResult(e, category(e.etype), _hours(e.start, e.end), repair)
        if r.category == "other" and e.etype not in IGNORED:
            r.warnings.append(f"unrecognized event type {e.event_type!r}; ignored")
        results[key] = r

    deratings = [e for e in evs if e.is_derating]
    outages = [e for e in evs if e.is_outage]
    rs_events = [e for e in evs if e.is_rs]

    # ---- 1. Size of reduction for each derating (fixed at its start) ---------
    for e in deratings:
        r = results[str(e.event_number)]
        if e.nac is None:
            r.warnings.append("derating has no Net Available Capacity; treated as zero reduction")
            r.reference_nac, r.reduction_mw = ndc, 0.0
            continue
        if e.dominant:
            ref = ndc                                   # dominant always starts at NDC
        else:
            # Additive stack: NDC less every ordinary derating already in effect.
            # Dominant deratings are not part of the stack, so a derating that
            # begins under a dominant one is sized as if it "had occurred alone"
            # (Ex. 4).
            ref = ndc - sum(
                results[str(o.event_number)].reduction_mw or 0.0
                for o in deratings
                if o is not e and not o.dominant
                and str(o.event_number) in results
                and results[str(o.event_number)].reduction_mw is not None
                and o.start <= e.start < o.end
            )
        red = ref - e.nac
        if red <= 0:
            r.warnings.append(
                f"reported NAC {e.nac:g} MW is not below the {ref:g} MW available when the "
                "event began; no reduction charged (missing dominant flag or wrong NAC?)"
            )
            red = 0.0
        r.reference_nac, r.reduction_mw = ref, red

    # ---- 2. Sweep the timeline and attribute the loss in each slice ---------
    points = sorted({t for e in evs for t in (e.start, e.end)})
    segments: List[Segment] = []
    seen_overlap = set()

    def active(group, t0, t1):
        return [e for e in group if e.start <= t0 and e.end >= t1]

    for t0, t1 in zip(points, points[1:]):
        dt = _hours(t0, t1)
        act_out = active(outages, t0, t1)
        act_der = active(deratings, t0, t1)
        in_rs = bool(active(rs_events, t0, t1))
        act_dom = [e for e in act_der if e.dominant]
        act_add = [e for e in act_der if not e.dominant]
        stack_nac = ndc - sum(results[str(e.event_number)].reduction_mw for e in act_add)

        if len(act_out) > 1:
            pair = tuple(sorted(str(e.event_number) for e in act_out))
            if pair not in seen_overlap:
                seen_overlap.add(pair)
                unit_warnings.append(f"overlapping outages {', '.join(pair)}")

        if act_out:
            shadower = act_out[0]
            charged, losers = (), act_der
            unit_nac, status = 0.0, "OUTAGE"
        elif act_dom:
            shadower = min(act_dom, key=lambda e: (e.nac, e.start, str(e.event_number)))
            charged, losers = (shadower,), [e for e in act_der if e is not shadower]
            unit_nac = min(shadower.nac, stack_nac)
            if stack_nac < shadower.nac:
                w = ("additive deratings alone take the unit below this dominant derating's "
                     "NAC; verify the dominant flag")
                rw = results[str(shadower.event_number)].warnings
                if w not in rw:
                    rw.append(w)
            status = "RESERVE_SHUTDOWN" if in_rs else "AVAILABLE"
        else:
            shadower = None
            charged, losers = tuple(act_add), []
            unit_nac = stack_nac
            status = "RESERVE_SHUTDOWN" if in_rs else "AVAILABLE"

        for e in charged:
            r = results[str(e.event_number)]
            mwh = r.reduction_mw * dt
            r.charged_hours += dt
            r.mwh_lost += mwh
            r.equivalent_hours += mwh / nmc
            if in_rs:
                r.equivalent_hours_rs += mwh / nmc
        for e in losers:
            r = results[str(e.event_number)]
            r.shadowed_hours += dt
            sb = str(shadower.event_number)
            if sb not in r.shadowed_by:
                r.shadowed_by.append(sb)

        if unit_nac < 0:
            unit_warnings.append(f"negative unit NAC between {t0} and {t1}")
        segments.append(Segment(
            t0, t1, max(unit_nac, 0.0), status,
            tuple(str(e.event_number) for e in (act_out[:1] if act_out else charged)),
        ))

    for r in results.values():
        unit_warnings.extend(f"event {r.event.event_number}: {w}" for w in r.warnings)
    return UnitAnalysis(ndc, nmc, results, segments, unit_warnings)


# --------------------------------------------------------------------------
# Optional pandas adapter
# --------------------------------------------------------------------------
def analyze_frame(df, capacities, unit_col="unit_id"):
    """Run `analyze` per unit over an event DataFrame.

    df columns : unit_id, event_number, event_type, start, end, and optionally
                 nac, dominant ('D'/True), cause_code, work_started, work_completed
    capacities : {unit_id: ndc} or {unit_id: (ndc, nmc)}
    Returns one row per event with the computed fields.
    """
    import pandas as pd

    def opt(row, name):
        v = row.get(name)
        return None if v is None or pd.isna(v) else v

    def ts(v):
        return None if v is None else pd.Timestamp(v).to_pydatetime()

    rows = []
    for unit, g in df.groupby(unit_col):
        cap = capacities[unit]
        ndc, nmc = cap if isinstance(cap, (tuple, list)) else (cap, None)
        events = []
        for row in g.to_dict("records"):
            dom = opt(row, "dominant")
            events.append(Event(
                event_number=str(row["event_number"]),
                event_type=str(row["event_type"]),
                start=ts(row["start"]),
                end=ts(row["end"]),
                nac=opt(row, "nac"),
                dominant=(dom is True) or (isinstance(dom, str) and dom.strip().upper() == "D"),
                cause_code=opt(row, "cause_code"),
                work_started=ts(opt(row, "work_started")),
                work_completed=ts(opt(row, "work_completed")),
            ))
        res = analyze(events, ndc, nmc)
        for key, r in res.results.items():
            rows.append({
                unit_col: unit,
                "event_number": key,
                "event_type": r.event.etype,
                "category": r.category,
                "dominant": r.event.dominant,
                "duration_hours": r.duration_hours,
                "reference_nac": r.reference_nac,
                "reduction_mw": r.reduction_mw,
                "charged_hours": r.charged_hours,
                "shadowed_hours": r.shadowed_hours,
                "shadowed_by": ",".join(r.shadowed_by),
                "mwh_lost": r.mwh_lost,
                "equivalent_derated_hours": r.equivalent_hours,
                "equivalent_derated_hours_rs": r.equivalent_hours_rs,
                "repair_hours": r.repair_hours,
                "warnings": "; ".join(r.warnings),
            })
    return pd.DataFrame(rows)
