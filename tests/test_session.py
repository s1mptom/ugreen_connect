"""Charging-session tracking: what starts a session, what ends it, what it accumulates."""

import ast
import itertools
from pathlib import Path

import pytest
from conftest import session as session_module

SessionTracker = session_module.SessionTracker

KEY = "charger"
PORT = "c1"

# The charger is polled every few seconds; the tests feed it at that cadence because
# both the debounce and the gap guard are only meaningful against a real sample rate.
STEP = 5.0


def reading(power, *, voltage=9.1, current=2.0, protocol="PD"):
    return {
        PORT: {
            "voltage": voltage,
            "current": current,
            "power": power,
            "protocol": protocol,
        }
    }


EMPTY = {PORT: {"voltage": 0.0, "current": 0.0, "power": 0.0, "protocol": "none"}}

# What a port with only a cable in it reports: this charger holds Vbus up and keeps
# the negotiated contract, so it is indistinguishable from an attached device that
# happens not to be drawing. Confirmed on C2 against the frame's own occupancy byte,
# which reads "present" for a bare cable too.
CABLE = {PORT: {"voltage": 5.1, "current": 0.0, "power": 0.0, "protocol": "PD"}}


def feed(tracker, start, seconds, values, step=STEP):
    """Poll ``tracker`` with the same reading for ``seconds``; return the last timestamp."""
    stamp = start
    while stamp <= start + seconds:
        tracker.update(stamp, KEY, values)
        stamp += step
    return stamp - step


def test_energy_accumulates_by_trapezoid_while_plugged():
    tracker = SessionTracker()
    tracker.update(0.0, KEY, reading(10.0))
    tracker.update(5.0, KEY, reading(20.0))

    assert tracker.session(KEY, PORT).energy_wh == pytest.approx(15.0 * 5 / 3600)


def test_unplugging_ends_the_session_but_keeps_the_number():
    tracker = SessionTracker()
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    feed(tracker, end + STEP, 60.0, EMPTY)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.energy_wh == pytest.approx(20.0 * 600 / 3600)


def test_plugging_in_again_starts_from_zero():
    tracker = SessionTracker()
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    end = feed(tracker, end + STEP, 60.0, EMPTY)

    tracker.update(end + STEP, KEY, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.active is True
    assert state.energy_wh == 0.0


def test_brief_protocol_dropout_does_not_end_the_session():
    """The charger reports "none" for a few seconds mid-renegotiation."""
    tracker = SessionTracker()
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    before = tracker.session(KEY, PORT).energy_wh

    feed(tracker, end + STEP, 10.0, EMPTY)
    feed(tracker, end + 20.0, 600.0, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.active is True
    # Two ten-minute stretches at the same power, not a counter restarted at zero.
    assert state.energy_wh == pytest.approx(before * 2, rel=0.05)


def test_reconnecting_with_a_different_protocol_starts_a_new_session():
    """Swapping a PD phone for a QC gamepad inside the debounce window is not a blip."""
    tracker = SessionTracker()
    end = feed(tracker, 0.0, 600.0, reading(20.0))

    feed(tracker, end + STEP, 10.0, EMPTY)
    tracker.update(end + 20.0, KEY, reading(20.0, protocol="QC"))

    assert tracker.session(KEY, PORT).energy_wh == 0.0


def test_a_gap_in_readings_is_not_integrated():
    """The cloud drops out for minutes at a time; stale power must not fill the hole."""
    tracker = SessionTracker()
    tracker.update(0.0, KEY, reading(20.0))
    tracker.update(5.0, KEY, reading(20.0))
    tracker.update(305.0, KEY, reading(20.0))
    tracker.update(310.0, KEY, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.active is True
    # Only the two observed 5 s intervals: 20 W over 10 s.
    assert state.energy_wh == pytest.approx(20.0 * 10 / 3600)


def test_a_long_poll_interval_still_accumulates():
    """``max_gap`` follows the configured scan interval, which reaches 900 s."""
    tracker = SessionTracker(max_gap=1800.0)
    tracker.update(0.0, KEY, reading(20.0))
    tracker.update(900.0, KEY, reading(20.0))

    assert tracker.session(KEY, PORT).energy_wh == pytest.approx(20.0 * 900 / 3600)


def test_maintenance_trickle_is_not_counted_as_charge():
    """A full phone left plugged in reads 0.1 A -- the current quantum, not charging.

    Observed on C1: ``power`` sits at 0.9 W for half of every idle poll, which would
    invent roughly 900 mAh over a night.
    """
    tracker = SessionTracker()
    feed(tracker, 0.0, 3600.0, reading(0.9, current=0.1))

    assert tracker.session(KEY, PORT).energy_wh == 0.0


def test_a_small_but_real_draw_is_counted():
    """The deadband has to stay under anything genuinely taking charge."""
    tracker = SessionTracker()
    feed(tracker, 0.0, 3600.0, reading(1.8, current=0.2))

    assert tracker.session(KEY, PORT).energy_wh == pytest.approx(1.8)


def test_a_session_records_when_it_ran_and_how_hard():
    tracker = SessionTracker()
    tracker.update(100.0, KEY, reading(20.0))
    end = feed(tracker, 105.0, 600.0, reading(45.0))
    feed(tracker, end + STEP, 60.0, EMPTY)

    state = tracker.session(KEY, PORT)
    assert state.started_at == 100.0
    assert state.peak_w == 45.0
    # The moment charge last flowed, not the poll that noticed the port had emptied.
    assert state.ended_at == end
    assert state.duration == pytest.approx(end - 100.0)


def test_an_unfinished_session_measures_up_to_the_last_reading():
    tracker = SessionTracker()
    feed(tracker, 100.0, 600.0, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.ended_at is None
    assert state.duration == pytest.approx(600.0)


def test_a_resumed_session_forgets_the_blip_that_looked_like_an_end():
    tracker = SessionTracker()
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    feed(tracker, end + STEP, 10.0, EMPTY)
    last = feed(tracker, end + 20.0, 600.0, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.ended_at is None
    assert state.duration == pytest.approx(last)


def test_energy_converts_to_an_approximate_battery_charge():
    """Delivered watt-hours, read back as charge into a nominal 3.85 V cell."""
    assert session_module.charge_mah(18.5, 3.85, 0.9) == pytest.approx(4324.7, abs=0.5)


def test_no_energy_is_no_charge():
    assert session_module.charge_mah(0.0, 3.85, 0.9) == 0.0


def test_a_restored_session_continues_when_the_same_device_is_still_there():
    """Home Assistant restarts mid-charge; the total on screen should not restart with it."""
    before = SessionTracker()
    before.update(100.0, KEY, reading(20.0))
    before.update(105.0, KEY, reading(20.0))
    saved = before.session(KEY, PORT).as_dict()

    after = SessionTracker()
    after.restore(KEY, PORT, saved)
    after.update(225.0, KEY, reading(20.0))
    after.update(230.0, KEY, reading(20.0))

    state = after.session(KEY, PORT)
    assert state.started_at == 100.0
    # The downtime itself is not integrated: only the 5 s observed either side of it.
    assert state.energy_wh == pytest.approx(20.0 * 10 / 3600)


def test_a_restored_session_is_dropped_when_a_different_device_is_there():
    """The phone was swapped for a gamepad while Home Assistant was down."""
    tracker = SessionTracker()
    tracker.restore(
        KEY, PORT, {"energy_wh": 12.0, "active": True, "protocol": "PD", "started_at": 100.0}
    )
    tracker.update(5000.0, KEY, reading(20.0, protocol="QC"))

    state = tracker.session(KEY, PORT)
    assert state.energy_wh == 0.0
    assert state.started_at == 5000.0


def test_a_restored_session_is_finished_when_the_port_is_empty():
    """The device was taken off during the downtime: keep the total, stop the session.

    `ended_at` is asserted because it is the one figure here that is not
    self-evident and the only one a reader would take on trust. It has to be
    when charge last flowed, not when Home Assistant came back and noticed --
    a bout that ended at 22:43 and is seen again at 01:43 would otherwise be
    published as three hours long at a fifth of its real average, and those
    figures are what the next restart restores from, so the error compounds.
    """
    tracker = SessionTracker()
    last_draw = 900.0
    tracker.restore(
        KEY, PORT,
        {"energy_wh": 12.0, "active": True, "protocol": "PD",
         "started_at": 100.0, "last_draw": last_draw},
    )
    tracker.update(5000.0, KEY, EMPTY)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.energy_wh == pytest.approx(12.0)
    assert state.ended_at == last_draw


def test_a_port_with_nothing_on_it_is_not_charging():
    """A socket nobody has used yet must not read as a session in progress."""
    tracker = SessionTracker()
    tracker.update(0.0, KEY, EMPTY)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.started_at is None
    assert state.ended_at is None


def test_a_port_that_never_reports_empty_still_ends_its_session():
    """The charger cannot say a device left, so a long quiet has to mean the same."""
    tracker = SessionTracker(idle_end=1800.0)
    end = feed(tracker, 0.0, 600.0, reading(20.0))

    feed(tracker, end + STEP, 3600.0, CABLE, step=60.0)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.energy_wh == pytest.approx(20.0 * 600 / 3600, rel=0.01)


def test_a_new_bout_after_a_long_quiet_starts_a_new_session():
    tracker = SessionTracker(idle_end=1800.0)
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    end = feed(tracker, end + STEP, 3600.0, CABLE, step=60.0)

    tracker.update(end + 60.0, KEY, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.active is True
    assert state.energy_wh == 0.0


def test_a_short_pause_does_not_split_a_session():
    """A device that stops drawing for a few minutes has not been swapped."""
    tracker = SessionTracker(idle_end=1800.0)
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    before = tracker.session(KEY, PORT).energy_wh

    end = feed(tracker, end + STEP, 600.0, CABLE)
    feed(tracker, end + STEP, 600.0, reading(20.0))

    state = tracker.session(KEY, PORT)
    assert state.active is True
    assert state.energy_wh == pytest.approx(before * 2, rel=0.05)


def test_a_cable_on_its_own_never_starts_a_session():
    tracker = SessionTracker(idle_end=1800.0)
    feed(tracker, 0.0, 7200.0, CABLE, step=60.0)

    state = tracker.session(KEY, PORT)
    assert state.started_at is None
    assert state.active is False
    assert state.energy_wh == 0.0


def test_duration_measures_the_bout_not_the_quiet_after_it():
    tracker = SessionTracker(idle_end=1800.0)
    end = feed(tracker, 0.0, 600.0, reading(20.0))

    feed(tracker, end + STEP, 3600.0, CABLE, step=60.0)

    state = tracker.session(KEY, PORT)
    assert state.ended_at == end
    assert state.duration == pytest.approx(600.0)


def test_a_cable_blip_does_not_replace_a_finished_session():
    """A stray 0.3 A that the charger itself calls 0 W must not start a bout.

    Seen on C5 with nothing but a cable in it; without this the blip would wipe the
    total the previous device left on screen.
    """
    tracker = SessionTracker(idle_end=1800.0)
    end = feed(tracker, 0.0, 600.0, reading(20.0))
    end = feed(tracker, end + STEP, 3600.0, CABLE, step=60.0)
    delivered = tracker.session(KEY, PORT).energy_wh

    blip = {PORT: {"voltage": 5.1, "current": 0.3, "power": 0.0, "protocol": "PD"}}
    feed(tracker, end + 60.0, 10.0, blip)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.energy_wh == pytest.approx(delivered)


def test_a_restored_session_is_finished_when_the_downtime_outlasted_the_bout():
    """Home Assistant was down for hours; whatever was charging is long done."""
    tracker = SessionTracker()
    tracker.restore(KEY, PORT, {
        "energy_wh": 12.0, "active": True, "protocol": "PD",
        "started_at": 100.0, "last_draw": 700.0,
    })

    tracker.update(40000.0, KEY, CABLE)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.energy_wh == pytest.approx(12.0)
    assert state.ended_at == 700.0


def test_a_phone_topping_itself_up_stays_one_session():
    """A phone left on the charger sips every so often; those sips are the same stay.

    Without this a 5 mAh trickle would start a "new session" and the charge that
    actually went into the phone would vanish off the card. The default window is
    what this test is about, so it deliberately does not pin one.
    """
    tracker = SessionTracker()
    end = feed(tracker, 0.0, 3600.0, reading(20.0))
    charged = tracker.session(KEY, PORT).energy_wh

    moment = end
    for _ in range(3):
        moment = feed(tracker, moment + STEP, 3000.0, CABLE, step=60.0)
        moment = feed(tracker, moment + STEP, 30.0, reading(2.5, current=0.5, voltage=5.1))

    state = tracker.session(KEY, PORT)
    assert state.active is True
    assert state.started_at == 0.0
    assert state.energy_wh > charged


def test_a_session_restored_without_a_last_draw_still_ends():
    """Sessions saved before last_draw existed must not be stuck running forever."""
    tracker = SessionTracker(idle_end=1800.0)
    tracker.restore(KEY, PORT, {
        "energy_wh": 90.0, "active": True, "protocol": "PD",
        "started_at": 0.0, "ended_at": None, "peak_w": 100.0,
    })

    feed(tracker, 100000.0, 120.0, CABLE)

    state = tracker.session(KEY, PORT)
    assert state.active is False
    assert state.energy_wh == pytest.approx(90.0)


def test_a_restored_session_that_never_started_has_no_end():
    tracker = SessionTracker()
    tracker.restore(KEY, PORT, {
        "energy_wh": 0.0, "active": False, "protocol": "none",
        "started_at": None, "ended_at": 500.0,
    })

    assert tracker.session(KEY, PORT).ended_at is None


def test_the_lifetime_total_is_the_sum_of_the_bouts():
    """Two numbers on one dashboard, and they have to agree.

    A bout does not stop delivering the moment it stops drawing: the ramp down
    to zero is integrated by the quiet branch, and that trapezoid used to land
    in the session and never in the lifetime -- every bout, always the same
    direction, for as long as the sensor existed. The Energy dashboard and the
    session sensors would then disagree permanently, and the one people check
    against the wall socket is the one that looked wrong.
    """
    tracker = SessionTracker()
    now, bouts = 1000.0, 0.0
    for _ in range(30):
        for _step in range(12):
            tracker.update(now, "dev", {"C1": {"voltage": 9.0, "current": 2.2,
                                               "power": 20.0, "protocol": "PD"}})
            now += 5
        # The tail: live, no longer drawing. This is the part that went missing.
        for _step in range(2):
            tracker.update(now, "dev", {"C1": {"voltage": 9.0, "current": 0.0,
                                               "power": 0.0, "protocol": "PD"}})
            now += 5
        session = tracker.session("dev", "C1")
        bouts += session.energy_wh if session else 0.0
        for _step in range(40):
            tracker.update(now, "dev", {"C1": {"voltage": 0.0, "current": 0.0,
                                               "power": 0.0, "protocol": "none"}})
            now += 5

    assert bouts > 0
    assert tracker.delivered_total("dev") == pytest.approx(bouts)


def test_charge_stops_flowing_long_before_the_bout_is_over():
    """Two questions, two answers, minutes to hours apart.

    `active` keeps a bout together for accounting: a phone sipping at 100%
    belongs in the same session rather than fragmenting the milliamp-hour
    figure, so it stays true for the whole idle window. A battery_charging
    sensor promises something else entirely -- reading "charging" for two hours
    after the charger stopped delivering is accurate about the bout and wrong
    about the question asked.
    """
    tracker = SessionTracker()
    now = 1000.0
    for _step in range(12):
        tracker.update(now, "dev", {"C1": {"voltage": 9.0, "current": 2.2,
                                           "power": 20.0, "protocol": "PD"}})
        now += 5
    session = tracker.session("dev", "C1")
    assert session.delivering is True
    assert session.active is True

    quiet = {"C1": {"voltage": 9.0, "current": 0.0, "power": 0.0, "protocol": "PD"}}
    # Measured from the last reading that carried current, which is one poll
    # before the quiet begins.
    last_draw = session.last_draw
    while now - last_draw < session_module.DRAW_SETTLE:
        tracker.update(now, "dev", quiet)
        now += 5
    # Still inside the settle: one missed poll must not toggle the sensor.
    assert session.delivering is True

    tracker.update(now, "dev", quiet)
    assert session.delivering is False
    # And the bout is still open, which is the whole point of the separation.
    assert session.active is True


def _configurable_bout_minimums() -> list[float]:
    """Every idle window the dialogs will accept, smallest first, in seconds.

    Both schemas, matched on CONF_IDLE_END rather than on a minute-valued
    selector: there are two of those -- setup and options -- and taking the
    first walked would have let the other one be lowered past the settle with
    this still green. The gate on `delivering` is only safe while a bout
    outlives it, so both have to be checked, not whichever comes first.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "custom_components" / "ugreen_connect" / "config_flow.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
    found: list[float] = []
    # The selector is the dict *value* beside the `vol.Required(...)` key, not
    # nested inside it, so the two have to be paired rather than walked.
    for mapping in ast.walk(tree):
        if not isinstance(mapping, ast.Dict):
            continue
        for key, value in zip(mapping.keys, mapping.values):
            if not isinstance(key, ast.Call) or not key.args:
                continue
            if getattr(key.args[0], "id", None) != "CONF_IDLE_END":
                continue
            for selector in ast.walk(value):
                if not isinstance(selector, ast.Call):
                    continue
                if getattr(selector.func, "id", None) != "NumberSelectorConfig":
                    continue
                smallest = {kw.arg: kw.value for kw in selector.keywords}.get("min")
                if isinstance(smallest, ast.Constant):
                    found.append(float(smallest.value) * 60)
    assert len(found) == 2, f"expected the setup and options schemas, found {found}"
    return sorted(found)


def test_the_two_windows_cannot_meet():
    """The separation the split rests on, asserted rather than assumed.

    `delivering` answers "is charge flowing" and `active` answers "is this bout
    over". They are the same predicate with different patience, and the whole
    reason for two of them is that the patience differs by orders of magnitude.
    Bring them together and the battery sensor silently becomes the thing it
    replaced -- true for as long as the bout, which was the bug.

    The bound is the smallest window the options dialog allows, not a number
    picked here: the settle has to be shorter than any bout anybody can
    configure, or the two would collide on somebody's real settings.
    """
    # Read out of the dialogs rather than copied from them: a number typed here
    # would keep agreeing with a docstring that had stopped being true the
    # moment somebody lowered a minimum. Both dialogs, because `delivering` is
    # now gated on the bout being live -- an idle window shorter than the settle
    # would have the gate suppressing delivery that is genuinely happening.
    for smallest in _configurable_bout_minimums():
        assert smallest > session_module.DRAW_SETTLE
    assert session_module.IDLE_END > session_module.DRAW_SETTLE



@pytest.mark.parametrize("step", [5.0, 10.0, 29.0])
def test_a_renegotiation_blip_does_not_flap_the_charging_sensor(step):
    """The charger says `none` for one poll in the middle of an unbroken charge.

    That blip is why UNPLUG_DEBOUNCE exists, and the bout is guarded against it.
    `delivering` was not: it was cleared on the single frame and set again on the
    next, so a battery_charging sensor went off and back on mid-charge and any
    automation on `to: "off"` -- a "finished" notification, a lamp, a speaker --
    fired while the laptop was still charging.

    Only below DRAW_SETTLE. Above it the blip is a vanishing share of the
    samples and the guard would cost real staleness instead; that half is
    `test_a_slow_poll_is_not_made_stale_by_the_blip_guard`.
    """
    tracker = SessionTracker()
    now, seen = 1000.0, []
    for poll in range(20):
        tracker.update(now, KEY, EMPTY if poll == 10 else reading(20.0))
        now += step
        seen.append(tracker.session(KEY, PORT).delivering)

    assert seen[9] is True
    assert seen[10] is True, "one `none` frame must not say charge stopped"
    assert seen[11] is True
    assert sum(1 for a, b in itertools.pairwise(seen) if a != b) == 0


@pytest.mark.parametrize("step", [30.0, 60.0, 900.0])
def test_a_slow_poll_is_not_made_stale_by_the_blip_guard(step):
    """At or above the settle the first empty reading still stops delivery.

    The first empty reading arrives one whole poll period after the last
    drawing one. Holding out for a second on a fifteen-minute interval would
    leave `battery_charging` on for three quarters of an hour after the cable
    was pulled -- worse than the flap it was meant to prevent, and certain
    rather than occasional.
    """
    tracker = SessionTracker()
    now = feed(tracker, 1000.0, 10 * step, reading(20.0), step=step) + step

    tracker.update(now, KEY, EMPTY)
    assert tracker.session(KEY, PORT).delivering is False


@pytest.mark.parametrize("step", [5.0, 10.0])
def test_a_port_that_stays_empty_does_stop_delivering(step):
    """The other half of the fast-poll guard: a device gone is reported as gone.

    Counting empty readings was the earlier answer and this asserted two. The
    rule is the settle instead -- one clock for "no current lately", asked once
    after the dispatch, so an empty port and a live one that stopped drawing
    cannot disagree about the same physical fact. Two empty readings five
    seconds apart is ten seconds without current, which is not yet a claim
    worth making.
    """
    tracker = SessionTracker()
    now = feed(tracker, 1000.0, 10 * step, reading(20.0), step=step) + step

    tracker.update(now, KEY, EMPTY)
    assert tracker.session(KEY, PORT).delivering is True, "the first is the blip"

    last_draw = tracker.session(KEY, PORT).last_draw
    # The reading that separates the two answers. Counting empty readings makes
    # this False; one clock keeps it True until the settle has actually passed.
    # Without this line the suite is green against either, which is how the
    # change it is here to hold could be reverted unnoticed.
    now += step
    tracker.update(now, KEY, EMPTY)
    assert tracker.session(KEY, PORT).delivering is True
    while now - last_draw < session_module.DRAW_SETTLE:
        now += step
        tracker.update(now, KEY, EMPTY)
    assert tracker.session(KEY, PORT).delivering is False


def test_a_restored_bout_on_an_empty_port_never_reads_as_charging():
    """last_draw outlives the bout it belonged to, and it must not speak for it.

    Reloading the entry -- the options dialog, seconds rather than minutes --
    restores a session and finishes it on the first poll, because the port is
    empty. The timestamp of the last current stays, though, and read on its own
    it says charge flowed moments ago. That turned `battery_charging` on for
    four polls on a port with nothing in it, alongside session sensors saying
    the bout was over, and fired a spurious `to: "on"` at whatever was
    listening.
    """
    tracker = SessionTracker()
    last_draw = 1000.0
    tracker.restore(
        KEY, PORT,
        {"energy_wh": 12.0, "active": True, "protocol": "PD",
         "started_at": 100.0, "last_draw": last_draw},
    )

    seen = []
    for poll in range(1, 8):
        tracker.update(last_draw + poll * 5.0, KEY, EMPTY)
        seen.append(tracker.session(KEY, PORT).delivering)

    assert seen == [False] * 7, seen

