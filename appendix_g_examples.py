
from datetime import datetime

from gads_derating import Event, analyze, round_half_up

NDC = NMC = 600.0
YEAR = 2023


def t(month, day, hhmm):
    h, m = hhmm.split(":")
    return datetime(YEAR, month, day, int(h), int(m))


def ev(num, etype, start, end, nac=None, dominant=False, cause=None, ws=None, wc=None):
    return Event(num, etype, start, end, nac, dominant, cause, ws, wc)


# Each example: events as reported, plus the values the appendix publishes.
#   edh        : Equivalent Derated Hours per derating event (2 dp)
#   hours      : outage / reserve-shutdown clock hours per event (2 dp)
#   nac_profile: unit Net Available Capacity implied by the narrative / figure
EXAMPLES = {
    "1": dict(
        title="Simple Outage",
        events=[ev("0001", "U1", t(1, 3, "04:30"), t(1, 8, "17:00"), cause="4240",
                   ws=t(1, 3, "08:00"), wc=t(1, 8, "09:30"))],
        edh={}, hours={"0001": 132.50},
    ),
    "2": dict(
        title="Simple Derating",
        events=[ev("0002", "D4", t(1, 10, "08:00"), t(1, 11, "16:00"), 200, cause="1492",
                   ws=t(1, 10, "08:00"), wc=t(1, 10, "16:00"))],
        edh={"0002": 21.33}, hours={},
        nac_profile=[200],
    ),
    "3A": dict(
        title="Overlapping deratings - second begins and ends during first (additive)",
        events=[ev("0003", "D1", t(3, 9, "08:45"), t(3, 9, "18:00"), 525, cause="0253"),
                ev("0004", "D1", t(3, 9, "10:00"), t(3, 9, "11:00"), 450, cause="0253")],
        edh={"0003": 1.16, "0004": 0.13}, hours={},
        nac_profile=[525, 450, 525],
    ),
    "3B": dict(
        title="Overlapping deratings - dominant second derating partially shadows first",
        events=[ev("0005", "D4", t(7, 3, "14:30"), t(7, 23, "11:45"), 575, cause="3112",
                   ws=t(7, 13, "08:00"), wc=t(7, 23, "11:45")),
                ev("0006", "D1", t(7, 19, "11:15"), t(7, 19, "12:00"), 360, dominant=True,
                   cause="3410")],
        edh={"0005": 19.85, "0006": 0.30}, hours={},
        nac_profile=[575, 360, 575],
    ),
    "3B-alt": dict(
        title="Example 3B as published for the case where 'B' is NOT dominant (additive)",
        events=[ev("0005", "D4", t(7, 3, "14:30"), t(7, 23, "11:45"), 575, cause="3112"),
                ev("0006", "D1", t(7, 19, "11:15"), t(7, 19, "12:00"), 360, cause="3410")],
        edh={"0005": 19.89, "0006": 0.27}, hours={},
        nac_profile=[575, 360, 575],
    ),
    "3C": dict(
        title="Overlapping deratings - first ends before second, capability changes",
        events=[ev("0007", "PD", t(1, 13, "08:00"), t(1, 13, "13:00"), 550, cause="3340",
                   ws=t(1, 13, "08:30"), wc=t(1, 13, "13:00")),
                ev("0008", "D1", t(1, 13, "10:00"), t(1, 14, "20:00"), 350, cause="0320")],
        edh={"0007": 0.42, "0008": 11.33}, hours={},
        nac_profile=[550, 350, 400],
    ),
    "3D": dict(
        title="Overlapping deratings - first ends before second, capability does not change",
        events=[ev("0009", "D1", t(3, 10, "06:30"), t(3, 10, "10:30"), 500, cause="3661",
                   ws=t(3, 10, "08:00"), wc=t(3, 10, "10:30")),
                ev("0010", "D1", t(3, 10, "07:45"), t(3, 10, "19:30"), 360, dominant=True,
                   cause="3260", ws=t(3, 10, "08:15"), wc=t(3, 10, "19:30"))],
        edh={"0009": 0.21, "0010": 4.70}, hours={},
        nac_profile=[500, 360],
    ),
    "4": dict(
        title="Derating during a dominant derating (fully shadowed)",
        events=[ev("0012", "D1", t(4, 10, "06:30"), t(4, 10, "19:00"), 300, dominant=True,
                   cause="1400", ws=t(4, 10, "08:00"), wc=t(4, 10, "19:00")),
                ev("0013", "D1", t(4, 10, "08:30"), t(4, 10, "15:45"), 500, cause="3352",
                   ws=t(4, 10, "09:00"), wc=t(4, 10, "15:45"))],
        edh={"0012": 6.25, "0013": 0.00}, hours={},
        nac_profile=[300],
    ),
    "5": dict(
        title="Derating during a reserve shutdown",
        events=[ev("0014", "RS", t(5, 31, "19:30"), t(6, 3, "08:30")),
                ev("0015", "D4", t(6, 1, "08:00"), t(6, 2, "15:30"), 400, cause="3412",
                   ws=t(6, 1, "08:00"), wc=t(6, 2, "15:30"))],
        edh={"0015": 10.50}, hours={"0014": 61.00},
        nac_profile=[600, 400, 600],
    ),
    "6A": dict(
        title="Derating overlapped by a full outage - derating ends before outage",
        events=[ev("0016", "D1", t(2, 27, "09:45"), t(3, 4, "18:30"), 500, cause="3340",
                   ws=t(3, 2, "08:00"), wc=t(3, 4, "18:30")),
                ev("0017", "U1", t(3, 2, "01:15"), t(3, 5, "09:22"), cause="1050",
                   ws=t(3, 2, "12:00"), wc=t(3, 4, "22:00"))],
        edh={"0016": 10.58}, hours={"0017": 80.12},
        nac_profile=[500, 0],
    ),
    "6B": dict(
        title="Derating overlapped by a full outage - outage begins and ends during derating",
        events=[ev("0018", "D1", t(5, 18, "09:45"), t(5, 25, "14:30"), 500, cause="0253"),
                ev("0019", "U1", t(5, 20, "18:45"), t(5, 24, "02:42"), cause="1000")],
        edh={"0018": 15.47}, hours={"0019": 79.95},
        nac_profile=[500, 0, 500],
    ),
    "7": dict(
        title="Startup failure",
        events=[ev("0022", "SF", t(10, 1, "22:00"), t(10, 3, "03:00"), cause="4030",
                   ws=t(10, 1, "23:00"), wc=t(10, 2, "16:00"))],
        edh={}, hours={"0022": 29.00},
    ),
    "8": dict(
        title="Fuel conservation - forced derating then forced outage",
        events=[ev("0020", "D1", t(8, 25, "05:00"), t(9, 3, "21:00"), 300, cause="9130"),
                ev("0021", "U1", t(9, 3, "21:00"), t(9, 6, "16:00"), cause="9130")],
        edh={"0020": 116.00}, hours={"0021": 67.00},
        nac_profile=[300, 0],
    ),
    "9": dict(
        title="Event transitions - U2 to RS to SF",
        events=[ev("0023", "U2", t(12, 3, "15:30"), t(12, 5, "21:30"), cause="8262",
                   ws=t(12, 3, "16:00"), wc=t(12, 5, "06:30")),
                ev("0024", "RS", t(12, 5, "21:30"), t(12, 6, "09:00")),
                ev("0025", "SF", t(12, 6, "09:00"), t(12, 9, "17:00"), cause="1000")],
        edh={}, hours={"0023": 54.00, "0024": 11.50, "0025": 80.00},
    ),
}


def run(example_id):
    ex = EXAMPLES[example_id]
    return analyze(ex["events"], NDC, NMC)


def report():
    ok = True
    print(f"{'Ex':<7}{'Event':<6}{'Type':<5}{'Dom':<4}{'Reduction':>10}{'Charged h':>11}"
          f"{'Shadowed h':>11}{'Published':>11}{'Computed':>10}  Result")
    print("-" * 84)
    for ex_id, ex in EXAMPLES.items():
        res = run(ex_id)
        for num, r in res.results.items():
            if num in ex["edh"]:
                pub, got = ex["edh"][num], round_half_up(r.equivalent_hours)
            else:
                pub, got = ex["hours"][num], round_half_up(r.duration_hours)
            good = abs(pub - got) < 1e-9
            ok &= good
            red = f"{r.reduction_mw:.0f} MW" if r.reduction_mw is not None else "-"
            chg = f"{r.charged_hours:.2f}" if r.event.is_derating else "-"
            shd = f"{r.shadowed_hours:.2f}" if r.event.is_derating else "-"
            print(f"{ex_id:<7}{num:<6}{r.event.etype:<5}{'D' if r.event.dominant else '':<4}"
                  f"{red:>10}{chg:>11}{shd:>11}{pub:>11.2f}{got:>10.2f}  "
                  f"{'PASS' if good else 'FAIL'}")
        for w in res.warnings:
            print(f"        warning: {w}")
    print("-" * 84)
    print("ALL APPENDIX G VALUES REPRODUCED" if ok else "MISMATCHES FOUND")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if report() else 1)
