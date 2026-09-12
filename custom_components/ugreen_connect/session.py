"""Charging sessions: how much a port delivered to whatever was last charging on it.

A session is a *bout of charging*, not the span between plugging in and unplugging.
That distinction is forced by the hardware: this charger holds Vbus up and keeps the
negotiated USB-PD contract alive on a port that has only a cable in it, so "a device
is attached" is not something it can be asked. The frame carries an occupancy byte
per port and it reads "present" for a bare cable too -- checked against a charger
with every device removed.

So a bout begins when current actually flows and ends when it has stopped for long
enough to mean the device is done or gone. Its total then stays on show until the
next bout starts. A port that genuinely reports itself empty -- some do -- ends its
bout at once rather than waiting out the quiet.

Deliberately free of Home Assistant imports so the rules can be tested on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# How long a port must read empty before its session is called finished. The charger
# briefly reports "none" mid-renegotiation, and without this every such blip would
# reset the counter.
UNPLUG_DEBOUNCE = 30.0

# How long a port has to stop drawing before "charge is flowing" turns false.
#
# Deliberately short, and deliberately not IDLE_END. That one keeps a bout
# together for accounting -- a phone that sips at 100% belongs in the same
# session rather than fragmenting the milliamp-hour figure -- and two hours is
# the right answer to "is this bout over". It is the wrong answer to "is it
# charging now", which is what a battery_charging sensor promises. Long enough
# not to flap on one missed poll; short enough that somebody watching the
# charger and the sensor sees them agree.
DRAW_SETTLE = 30.0

# How long the current has to stay down before a bout counts as over.
#
# This is the one number the hardware cannot settle for us. A phone sitting at 100%
# keeps topping itself up every so often, and those sips have to land inside the same
# bout -- otherwise a 5 mAh trickle starts a "new session" and the 4000 mAh that
# actually went into the phone disappears off the card. Two hours clears that by a
# wide margin. The cost is at the other end: two devices swapped on the same cable
# less than two hours apart are counted as one. Which of those hurts more depends on
# what lives on the port, so it is an option.
IDLE_END = 7200.0

# Longest span between two readings that is still treated as continuous. The cloud
# drops out for minutes at a time, and carrying the last known power across such a
# hole would invent energy that was never delivered. Under-counting a real gap is
# the safer error, so the interval is simply dropped.
MAX_GAP = 60.0

# What counts as charge actually going somewhere. Both have to hold: a full device
# still reports the 0.1 A measurement quantum, which at 9 V would look like 0.9 W and
# invent close to a whole battery over a night, while a bare cable produces the
# mirror image -- a stray 0.3 A that the charger itself reports as 0.0 W.
IDLE_CURRENT = 0.15
IDLE_POWER = 0.5


def _plugged(values: dict) -> bool:
    """Whether the port shows anything at all -- a device, or just a cable."""
    return values.get("protocol", "none") != "none" or (values.get("voltage") or 0.0) > 0.5


def drawing(values: dict) -> bool:
    """Whether charge is actually flowing, as opposed to the port merely being live.

    Public, and deliberately so: the coordinator asks this too, to decide how
    often to poll. Changing what it means now changes more than sessions.
    """
    return (values.get("current") or 0.0) >= IDLE_CURRENT and (
        values.get("power") or 0.0
    ) >= IDLE_POWER


def _swapped(before: str, now: str) -> bool:
    """Whether a different device answered, rather than the same one re-negotiating."""
    return now != "none" and before not in ("none", now)


@dataclass
class Session:
    """One bout of charging on one port."""

    energy_wh: float = 0.0
    # False until current actually flows, so a port with only a cable in it -- which
    # this charger reports exactly like an idle device -- never reads as a session.
    active: bool = False
    # Whether charge is flowing right now, as opposed to the bout still being
    # open. Separate from `active` on purpose: they answer different questions
    # and are minutes to hours apart at the end of every bout.
    delivering: bool = False
    protocol: str = "none"
    started_at: float | None = None
    ended_at: float | None = None
    peak_w: float = 0.0
    last_draw: float | None = None
    # Sampling state, not part of what a restart carries over.
    last_ts: float | None = None
    last_power: float = 0.0
    empty_since: float | None = None
    pending_restore: bool = field(default=False, repr=False)

    @property
    def duration(self) -> float:
        """How long charge was flowing -- the quiet afterwards is not part of it."""
        until = self.ended_at if self.ended_at is not None else self.last_draw
        if self.started_at is None or until is None:
            return 0.0
        return until - self.started_at

    @property
    def average_w(self) -> float:
        span = self.duration
        return self.energy_wh * 3600 / span if span > 0 else 0.0

    def as_dict(self) -> dict:
        """The part worth carrying across a restart -- live sampling state is not."""
        return {
            "energy_wh": self.energy_wh,
            "active": self.active,
            "protocol": self.protocol,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "peak_w": self.peak_w,
            "last_draw": self.last_draw,
        }


class SessionTracker:
    """Accumulates per-port charging sessions from successive readings."""

    def __init__(self, max_gap: float = MAX_GAP, idle_end: float = IDLE_END) -> None:
        self._max_gap = max_gap
        self._idle_end = idle_end
        self._sessions: dict[tuple[str, str], Session] = {}
        # Watt-hours per port since this tracker was made, which is not the
        # same as since the charger was bought -- the sensors add what came
        # before a restart from their own last state.
        self._delivered: dict[tuple[str, str], float] = {}

    def delivered(self, key: str, port: str) -> float:
        """Watt-hours this port has passed since the tracker was made."""
        return self._delivered.get((key, port), 0.0)

    def delivered_total(self, key: str) -> float:
        """The same for the whole charger, every port added up."""
        return sum(wh for (device, _), wh in self._delivered.items() if device == key)

    def session(self, key: str, port: str) -> Session | None:
        return self._sessions.get((key, port))

    def restore(self, key: str, port: str, saved: dict) -> None:
        """Take back a session saved before a restart.

        ``last_ts`` is deliberately left unset: nothing is known about what the port
        did while Home Assistant was down, so the first reading after this only
        re-establishes the baseline rather than integrating across the outage.
        ``last_draw`` is kept, because how long ago charge last flowed is exactly
        what decides whether the session survives the gap.
        """
        started = saved.get("started_at")
        # A session that never started cannot have ended; older builds wrote one anyway.
        ended = saved.get("ended_at") if started is not None else None
        last_draw = saved.get("last_draw")
        if last_draw is None:
            # Saved before last_draw was recorded. The last moment the session showed
            # any sign of life stands in for it, which is all the idle window needs to
            # retire a session an older build left running.
            last_draw = ended if ended is not None else started
        self._sessions[(key, port)] = Session(
            energy_wh=saved.get("energy_wh") or 0.0,
            active=bool(saved.get("active")),
            protocol=saved.get("protocol") or "none",
            started_at=started,
            ended_at=ended,
            peak_w=saved.get("peak_w") or 0.0,
            last_draw=last_draw,
            pending_restore=True,
        )

    def update(self, now: float, key: str, ports: dict[str, dict]) -> None:
        """Fold one poll's readings into each port's session.

        Call this only for polls that actually returned data: a failed poll must leave
        every session exactly as it was, or an outage would read as an unplug.
        """
        for port, values in ports.items():
            state = self._sessions.setdefault((key, port), Session())
            protocol = values.get("protocol", "none")

            if state.pending_restore:
                state.pending_restore = False
                if not _plugged(values):
                    self._finish(now, state)
                    continue
                if _swapped(state.protocol, protocol):
                    state = self._restart(key, port)

            before = state.energy_wh
            if not _plugged(values):
                self._empty(now, state)
            elif not drawing(values):
                self._quiet(now, state)
            else:
                # Charge is flowing. It belongs to the running bout unless that bout is
                # over -- finished by the quiet timer, or stale because no reading arrived
                # while it ran out, or ended by the port emptying and a different device
                # answering.
                stale = (
                    state.last_draw is not None
                    and now - state.last_draw >= self._idle_end
                )
                finished = state.started_at is not None and not state.active
                swapped = state.empty_since is not None and _swapped(state.protocol, protocol)
                if finished or stale or swapped:
                    state = self._restart(key, port)
                    before = state.energy_wh
                self._advance(now, state, protocol, values)
            # Derived here rather than set in each branch, because every branch
            # had to agree and one of them did not: clearing it the moment a
            # reading came back empty made a single `none` frame mid-charge --
            # which this charger emits while a device renegotiates, and which
            # UNPLUG_DEBOUNCE exists for -- flip the sensor off and on again.
            # Reading it off last_draw gives delivery the same patience the
            # bout already had, and carries it across a restart for free, since
            # last_draw is in as_dict and delivering never was.
            #
            # Gated on the bout as well as the clock, because last_draw outlives
            # the bout it belonged to: a restored session whose port is empty
            # is finished on the first poll and keeps its timestamp, and
            # without this the second poll would read that as current having
            # flowed recently and turn the sensor on for an empty port.
            #
            # It also keeps a slow poll honest without a second rule. A blip is
            # only worth protecting against when it is a small share of the
            # samples: at a five-second interval the reading after one is still
            # seconds from the last current, so this stays true; at fifteen
            # minutes the first empty reading is already a whole period past it
            # and this goes false at once, where a rule that counted readings
            # would wait for the second and hold the sensor on for half an hour
            # after the cable was pulled.
            state.delivering = (
                state.active
                and state.last_draw is not None
                and now - state.last_draw < DRAW_SETTLE
            )
            # Whatever the session just gained, the lifetime total gains too.
            # Taken around the whole dispatch rather than around _advance alone:
            # a bout does not stop delivering the moment it stops drawing, and
            # _quiet integrates the ramp down to zero. Crediting only the
            # drawing branch left that last trapezoid in the session and out of
            # the lifetime, every bout, always in the same direction -- two
            # numbers on one dashboard that could never agree.
            #
            # Re-read after a restart, and only there: the old session's energy
            # was credited on the polls that earned it, so a restart cannot
            # lose what it already counted.
            self._delivered[(key, port)] = (
                self._delivered.get((key, port), 0.0) + state.energy_wh - before
            )

    def _restart(self, key: str, port: str) -> Session:
        state = Session()
        self._sessions[(key, port)] = state
        return state

    def _advance(self, now: float, state: Session, protocol: str, values: dict) -> None:
        """Charge one more reading's worth of energy into a running bout."""
        state.protocol = protocol
        state.active = True
        state.empty_since = None
        state.ended_at = None
        if state.started_at is None:
            state.started_at = now
        power = values.get("power") or 0.0
        state.peak_w = max(state.peak_w, power)
        self._integrate(now, state, power)
        state.last_draw = now

    def _quiet(self, now: float, state: Session) -> None:
        """The port is live but nothing is flowing: time the pause, end the bout if it lasts."""
        state.empty_since = None
        if state.started_at is None:
            # Only a cable so far, as far as anyone can tell. Nothing to time.
            return
        self._integrate(now, state, 0.0)
        if (
            state.active
            and state.last_draw is not None
            and now - state.last_draw >= self._idle_end
        ):
            state.active = False
            state.ended_at = state.last_draw

    def _integrate(self, now: float, state: Session, power: float) -> None:
        if state.last_ts is not None:
            span = now - state.last_ts
            if 0 < span <= self._max_gap:
                state.energy_wh += (state.last_power + power) / 2 * span / 3600
        state.last_ts = now
        state.last_power = power

    def _empty(self, now: float, state: Session) -> None:
        """An empty reading: note when it started, and end the session if it holds."""
        if state.empty_since is None:
            state.empty_since = now
            if state.started_at is not None and state.ended_at is None:
                state.ended_at = state.last_draw if state.last_draw is not None else now
        elif now - state.empty_since >= UNPLUG_DEBOUNCE:
            state.active = False

    def _finish(self, now: float, state: Session) -> None:
        state.active = False
        # Redundant three ways -- the flag defaults False, `as_dict` omits it
        # and `restore()` never passes it -- and all three live in other places.
        # Should it ever join `as_dict`, a restored True would survive this and
        # the empty-port flap would come back by another road.
        state.delivering = False
        if state.ended_at is None and state.started_at is not None:
            state.ended_at = state.last_draw if state.last_draw is not None else now


def charge_mah(energy_wh: float, nominal_v: float, efficiency: float) -> float:
    """Watt-hours out of the port, as charge into a battery -- an estimate, not a reading.

    The charger measures at its own connector, where a PD device may be taking 5, 9 or
    28 V; the cell behind it sits near 3.85 V and the converter in between loses some of
    what arrives. Both of those are assumptions, which is why every label says "about".
    """
    if nominal_v <= 0:
        return 0.0
    return energy_wh / nominal_v * 1000 * efficiency
