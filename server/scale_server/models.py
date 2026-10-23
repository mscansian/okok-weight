"""What the gateway sends and what the API answers with."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The gateway derives the address from the advertisement it decoded, and it only
# ever posts the one it was provisioned to listen for. The same shape names the
# gateway itself, whose own radio has an address of exactly this kind.
BLUETOOTH_ADDRESS = r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$"

# What a gateway may be told to listen for. Wider than an address by five
# octets, because the advertisement carries only the tail of the scale's address
# and a gateway provisioned with that tail alone recognises the scale perfectly
# well. A ping reports the setting as it was given, so this is what it may hold.
SCALE_FILTER = r"^(?:[0-9A-Fa-f]{2}[:-]){4,5}[0-9A-Fa-f]{2}$"


def canonical_address(value: str) -> str:
    """One device spells itself the same way however its address reached us, so
    everything it has ever sent groups under the one name."""
    return value.replace("-", ":").lower()


class WeighIn(BaseModel):
    """A locked reading decoded from a scale advertisement.

    The weight, when it happened and which scale it came from are kept. The rest
    describes how well the gateway heard the scale, which belongs in the logs
    and not in the weigh-in history.
    """

    model_config = ConfigDict(extra="forbid")

    # Names the weigh-in rather than this delivery of it: the gateway mints it
    # when the scale first locks and repeats it if it repeats the delivery.
    event_id: UUID

    # The weight is the measurement, so an implausible one is worth refusing:
    # there is no such weigh-in, and storing it would put a lie in the history.
    weight_kg: float = Field(gt=0, lt=500)

    # As wide as the payload fields these are decoded from, and no wider.
    field_b: int = Field(ge=0, le=0xFFFF)
    sequence: int = Field(ge=0, le=0xFF)

    # Deliberately unbounded. The gateway forwards whatever the controller
    # reported, and 127 — the Bluetooth spec's "RSSI not available" — is a value
    # this field has to be able to carry. Refusing it would cost a weigh-in over
    # a number that is only ever logged, and a weigh-in the gateway failed to
    # deliver is gone.
    rssi: int

    address: str = Field(pattern=BLUETOOTH_ADDRESS)

    # How long before this delivery the weigh-in was taken, which is what the
    # server times it by. The gateway reports a gap rather than a clock reading
    # because it has no clock worth reading: the ESP32's is not battery-backed,
    # so a weigh-in taken after a power cut and before the network came back
    # would carry no time at all, while the gap is answerable from the moment
    # the board boots.
    #
    # Zero is the honest default. A gateway that posts as it reads sends that
    # anyway, and one too old to send the field at all was doing exactly that.
    #
    # The ceiling is looser than the day a gateway holds a weigh-in for, so
    # raising that limit stays a firmware change on its own.
    age_ms: int = Field(default=0, ge=0, le=7 * 24 * 60 * 60 * 1000)

    # How many locked advertisements the gateway heard before it decided this
    # weigh-in had settled. The weight it reports is the last of them, so a
    # count near one is a gateway that nearly missed the end of the weigh-in and
    # may be reporting someone half-way onto the scale.
    #
    # Zero means the gateway did not say, which no gateway that counts them can
    # report: a weigh-in exists because at least one lock was heard.
    locks: int = Field(default=0, ge=0)

    @field_validator("address")
    @classmethod
    def one_spelling(cls, value: str) -> str:
        return canonical_address(value)


class DevicePing(BaseModel):
    """A gateway reporting that it is alive, and how it is getting on.

    Everything past the liveness is here because the serial console is otherwise
    the only place it can be read, and a board doing its job is a board nobody
    has a cable plugged into.
    """

    model_config = ConfigDict(extra="forbid")

    # Which scales this gateway was told to listen for, as it was told. A board
    # aimed at the wrong scale and a board nobody has stood on are the same
    # silence in the reading history, and this is what tells them apart.
    #
    # Empty is a board saying it was told none, which is the board this field
    # most exists to reveal. Null is a board that did not say at all, which only
    # a gateway too old to know it listens for more than one does.
    #
    # The ceiling is a bound on the body rather than the firmware's own limit,
    # which is a number this server has no business holding a copy of.
    scale_addresses: list[Annotated[str, Field(pattern=SCALE_FILTER)]] | None = Field(default=None, max_length=16)

    # What a gateway too old to listen for more than one scale sends instead.
    # Folded into the list above rather than refused, because a board in the
    # field pings on its own cadence and would otherwise go quiet here — reading
    # as the failure this field was added to make visible — until someone walked
    # over with a cable.
    scale_address: str | None = Field(default=None, pattern=SCALE_FILTER, exclude=True)

    # The network it joined and how well it is hearing the access point. As wide
    # as 802.11 allows an SSID to be, and no wider.
    ssid: str | None = Field(default=None, max_length=32)
    # Named for the link it measures: the `rssi` a weigh-in carries is the other
    # radio, describing how well this board heard the scale.
    wifi_rssi: int | None = None

    # Weigh-ins taken but not yet delivered. Non-zero says the history is behind
    # what the scale has actually seen, which nothing else here reveals.
    queued: int = Field(ge=0)

    # Measured from the boot rather than against a clock, for the reason `age_ms`
    # is. A board that answers every ping but whose uptime keeps returning to
    # zero is rebooting, and no amount of liveness would show it.
    uptime_ms: int = Field(ge=0)

    # How often this board intends to ping, which is what its silence is judged
    # against. Declared by the gateway rather than configured here so that the
    # two cannot drift: a board given a new cadence brings the threshold with it.
    interval_ms: int = Field(gt=0, le=24 * 60 * 60 * 1000)

    firmware_version: str = Field(max_length=32)

    # Why the board last came up. A brownout is a power supply to fix and a panic
    # is firmware to fix, and uptime alone cannot tell the two apart.
    reset_reason: str = Field(max_length=32)

    # Every boot this board has had, and how many of them the brownout detector
    # caused. `reset_reason` names the last one only, which for a supply that
    # sags now and then says that it happened but never how often; these two
    # carry across reboots so the rate is readable without trawling the logs.
    #
    # Defaulted because a gateway too old to count them still pings, and a board
    # that has never counted a boot is indistinguishable from one that has none.
    boots: int = Field(default=0, ge=0)
    brownouts: int = Field(default=0, ge=0)

    # What the board had left to allocate as it built this ping. Two numbers
    # because a heap can be roomy and still unusable: reaching the server means
    # verifying its certificate chain, which multiplies bignums that need
    # contiguous space, so a gateway whose free memory has fragmented into small
    # pieces loses the server entirely while every other field still reads well.
    #
    # Null rather than zero for a gateway too old to report them, because no
    # running board has no heap and a plausible-looking zero would be read as one.
    free_heap: int | None = Field(default=None, ge=0)
    largest_free_block: int | None = Field(default=None, ge=0)

    # The floor the heap reached since the board came up. `free_heap` is read as
    # the ping is built, so it says nothing about the pressure a handshake put on
    # the heap and then gave back; this is the number that remembers it.
    min_free_heap: int | None = Field(default=None, ge=0)

    # How long the run before this one lasted. Pings arrive on a cadence, so the
    # history alone times a reset only to the nearest interval; the board itself
    # knows to the second, and this is it saying so.
    #
    # Null where it cannot: what carries this across a reset is the RTC domain,
    # and a supply that collapsed far enough to clear it leaves the board with
    # nothing to report rather than something wrong.
    previous_uptime_ms: int | None = Field(default=None, ge=0)

    # How often the link has dropped since the board came up, and the SDK's own
    # number for why it last did. Every reconnection owes the server a ping, so
    # churn here is extra traffic and extra current as much as it is a worse
    # link — which makes this the field that says whether power saving on the
    # radio was paid for out of the link.
    wifi_disconnects: int = Field(default=0, ge=0)
    wifi_disconnect_reason: int = Field(default=0, ge=0, le=255)

    # What the board is actually clocked at, against what it was built to be.
    cpu_mhz: int | None = Field(default=None, gt=0)
    xtal_mhz: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def the_scales_as_one_list(self) -> "DevicePing":
        """The scales this board watches, however its firmware spelled the fact.

        A board sending only the older single address is read as watching that
        one scale, so the two generations of firmware answer the same question in
        the same shape from here on.
        """
        if self.scale_addresses is None and self.scale_address is not None:
            self.scale_addresses = [self.scale_address]
        if self.scale_addresses is not None:
            self.scale_addresses = [canonical_address(address) for address in self.scale_addresses]
        return self

    @model_validator(mode="after")
    def the_largest_block_fits_in_the_heap(self) -> "DevicePing":
        """A contiguous run of free memory is part of the free memory, so it
        cannot be bigger than it. The pair arriving the wrong way round means the
        board measured something other than what these names claim."""
        if (
            self.free_heap is not None
            and self.largest_free_block is not None
            and self.largest_free_block > self.free_heap
        ):
            raise ValueError("the largest free block cannot exceed the free heap")
        return self

    @model_validator(mode="after")
    def every_brownout_is_a_boot(self) -> "DevicePing":
        """A brownout is one of the boots, so it cannot outnumber them. Refusing
        the pair here makes a gateway that has miscounted say so as a bad request
        rather than as a failed insert."""
        if self.brownouts > self.boots:
            raise ValueError("brownouts cannot outnumber boots")
        return self


class DeviceName(BaseModel):
    """What a board is to be called from now on.

    The only part of a device the server owns, so the only part there is to
    edit: everything else in the row is what the board itself reported.
    """

    model_config = ConfigDict(extra="forbid")

    # Null takes the name away again, leaving the board known by its address.
    name: str | None = Field(max_length=64)

    @field_validator("name")
    @classmethod
    def blank_is_nameless(cls, value: str | None) -> str | None:
        """A board called nothing and a board called spaces are both unnamed, so
        both are stored as no name at all."""
        return None if value is None else value.strip() or None


class Device(BaseModel):
    """A gateway as the server last heard from it."""

    address: str
    # Whatever you called this board. The server owns it, because a board knows
    # its own address and nothing at all about where it was put.
    name: str | None
    # Derived on every read and never stored, so a server that was not running
    # leaves nothing behind still claiming to be up.
    online: bool
    first_seen_at: datetime
    last_seen_at: datetime
    # Where the ping arrived from, as the server saw it. The gateway cannot know
    # the address its traffic leaves the network with.
    client_ip: str
    scale_addresses: list[str] | None
    ssid: str | None
    wifi_rssi: int | None
    queued: int
    uptime_ms: int
    interval_ms: int
    firmware_version: str
    reset_reason: str
    boots: int
    brownouts: int
    free_heap: int | None
    largest_free_block: int | None
    min_free_heap: int | None
    previous_uptime_ms: int | None
    wifi_disconnects: int
    wifi_disconnect_reason: int
    cpu_mhz: int | None
    xtal_mhz: int | None


class DeviceList(BaseModel):
    items: list[Device]
    total: int


class DevicePingRecord(BaseModel):
    """One ping as it arrived, kept rather than overwritten.

    A device row says what a board is doing. This says what it was doing, which
    for a board that stops and comes back is the only place the stopping shows:
    the heap on the way down, the link dropping, the die warming, and how long
    the run before this one managed to last.
    """

    recorded_at: datetime
    client_ip: str
    scale_addresses: list[str] | None
    ssid: str | None
    wifi_rssi: int | None
    queued: int
    uptime_ms: int
    interval_ms: int
    firmware_version: str
    reset_reason: str
    boots: int
    brownouts: int
    free_heap: int | None
    largest_free_block: int | None
    min_free_heap: int | None
    previous_uptime_ms: int | None
    wifi_disconnects: int
    wifi_disconnect_reason: int
    cpu_mhz: int | None
    xtal_mhz: int | None


class DevicePingPage(BaseModel):
    items: list[DevicePingRecord]
    total: int
    limit: int
    offset: int


class Reading(BaseModel):
    id: UUID
    event_id: UUID
    # Which scale was stood on.
    address: str
    weight_kg: float
    # When the weigh-in happened, against when it reached the server. They agree
    # on a weigh-in delivered as it was taken, and differ by however long the
    # gateway had to hold one back over a dropped link.
    measured_at: datetime
    recorded_at: datetime


class ReadingPage(BaseModel):
    items: list[Reading]
    total: int
    limit: int
    offset: int


class Health(BaseModel):
    status: Literal["ok"]
    readings: int
