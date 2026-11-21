"""Tests for the IANA time zone to POSIX TZ string table (FR-userprefs-correctness-10)."""

import pytest

from mtfwbuilder.time_zones import POSIX_TZ_BY_ZONE, POSIX_TZ_PATTERN, to_posix_tz


def test_table_maps_america_new_york_to_its_posix_tz_string():
    assert POSIX_TZ_BY_ZONE["America/New_York"] == "EST5EDT,M3.2.0,M11.1.0"


def test_table_holds_the_whole_tz_database():
    # The tz database has about 600 names, so a smaller table means the script read the wrong files.
    assert len(POSIX_TZ_BY_ZONE) > 500


def test_every_table_value_matches_the_posix_tz_pattern():
    # The pass-through check uses the same pattern, so it must accept every real POSIX TZ string.
    rejected = {zone: tz for zone, tz in POSIX_TZ_BY_ZONE.items() if not POSIX_TZ_PATTERN.fullmatch(tz)}
    assert rejected == {}


@pytest.mark.parametrize("zone", ["Europe/Berlin", "UTC", "Asia/Kathmandu"])
def test_iana_time_zone_gives_the_table_value(zone):
    assert to_posix_tz(zone) == POSIX_TZ_BY_ZONE[zone]


@pytest.mark.parametrize("value", ["Mars/Olympus_Mons", "ESTX", "5", "EST5EDT,M3", "America/New_York "])
def test_value_that_is_not_a_time_zone_raises_value_error(value):
    with pytest.raises(ValueError, match="time zone"):
        to_posix_tz(value)
