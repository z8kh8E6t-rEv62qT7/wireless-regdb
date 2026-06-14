#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

from dbparse import DBParser, flag_definitions


IMPLEMENTED_BANDS = ('2.4', '5')
RESERVED_BANDS = ('6', '60', 's1g', 'lc')
ALL_BANDS = IMPLEMENTED_BANDS + RESERVED_BANDS
CHANNEL_LIMITS = {
    '2.4': (1, 14),
    '5': (32, 181),
}
REQUIREMENT_KEYS = ('band', 'channels', 'min-bw', 'flag-inc', 'flag-exc')
SINGULAR_REQUIREMENT_KEYS = ('band', 'channels', 'min-bw')


class Requirement(object):
    def __init__(
        self,
        band,
        channels,
        min_bw,
        flag_inc,
        flag_exc,
        query_start_mhz,
        query_end_mhz,
    ):
        self.band = band
        self.channels = channels
        self.min_bw = min_bw
        self.flag_inc = flag_inc
        self.flag_exc = flag_exc
        self.query_start_mhz = query_start_mhz
        self.query_end_mhz = query_end_mhz


def channel_to_5ghz_mhz(channel):
    return 5000 + channel * 5


def channel_to_24ghz_mhz(channel):
    if channel == 14:
        return 2484
    return 2407 + channel * 5


def channel_to_mhz(band, channel):
    if band == '2.4':
        return channel_to_24ghz_mhz(channel)
    if band == '5':
        return channel_to_5ghz_mhz(channel)
    raise ValueError('unsupported band: %s' % band)


def parse_channel_selection(value):
    if '-' not in value:
        try:
            channel = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(
                'expected CHANNEL or START-END, for example 32 or 100-144'
            )
        return channel, channel

    try:
        start_text, end_text = value.split('-', 1)
        start = int(start_text)
        end = int(end_text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            'expected CHANNEL or START-END, for example 32 or 100-144'
        )

    if start > end:
        raise argparse.ArgumentTypeError(
            'channel range start must be <= end'
        )
    return start, end


def parse_non_negative_float(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('expected a numeric MHz value')
    if result < 0:
        raise argparse.ArgumentTypeError(
            'value must be greater than or equal to 0'
        )
    return result


def parse_requirement(value):
    fields = {}
    flag_inc = []
    flag_exc = []

    for item in value.split(','):
        item = item.strip()
        if not item:
            raise argparse.ArgumentTypeError('empty requirement item')
        if '=' not in item:
            raise argparse.ArgumentTypeError(
                'expected key=value in requirement item %r' % item
            )

        key, item_value = item.split('=', 1)
        key = key.strip()
        item_value = item_value.strip()
        if not key or not item_value:
            raise argparse.ArgumentTypeError(
                'expected non-empty key=value in requirement item %r' % item
            )
        if key not in REQUIREMENT_KEYS:
            raise argparse.ArgumentTypeError(
                'unknown requirement key %r' % key
            )
        if key in SINGULAR_REQUIREMENT_KEYS:
            if key in fields:
                raise argparse.ArgumentTypeError(
                    'duplicate requirement key %r' % key
                )
            fields[key] = item_value
        elif key == 'flag-inc':
            flag_inc.append(parse_flag(item_value, key))
        elif key == 'flag-exc':
            flag_exc.append(parse_flag(item_value, key))

    missing_keys = [
        key for key in SINGULAR_REQUIREMENT_KEYS
        if key not in fields
    ]
    if missing_keys:
        raise argparse.ArgumentTypeError(
            'missing required requirement key(s): %s' % ', '.join(missing_keys)
        )

    band = fields['band']
    if band not in ALL_BANDS:
        raise argparse.ArgumentTypeError(
            "invalid band %r (choose from %s)" % (band, ', '.join(ALL_BANDS))
        )
    if band in RESERVED_BANDS:
        raise argparse.ArgumentTypeError(
            'band %s is reserved but not implemented yet' % band
        )

    channels = parse_requirement_channels(fields['channels'])
    validate_requirement_channels(band, channels)
    min_bw = parse_requirement_min_bw(fields['min-bw'])
    validate_requirement_flags(flag_inc, flag_exc)

    start_channel, end_channel = channels
    return Requirement(
        band,
        channels,
        min_bw,
        flag_inc,
        flag_exc,
        channel_to_mhz(band, start_channel),
        channel_to_mhz(band, end_channel),
    )


def parse_flag(value, key):
    if value not in flag_definitions:
        raise argparse.ArgumentTypeError(
            "invalid %s flag %r (choose from %s)" % (
                key,
                value,
                ', '.join(sorted(flag_definitions)),
            )
        )
    return value


def parse_requirement_channels(value):
    try:
        return parse_channel_selection(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError('invalid channels: %s' % exc)


def parse_requirement_min_bw(value):
    try:
        return parse_non_negative_float(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError('invalid min-bw: %s' % exc)


def validate_requirement_channels(band, channels):
    start_channel, end_channel = channels
    min_channel, max_channel = CHANNEL_LIMITS[band]
    if start_channel < min_channel or end_channel > max_channel:
        raise argparse.ArgumentTypeError(
            'channels for band %s must be in the range %d-%d' % (
                band,
                min_channel,
                max_channel,
            )
        )


def validate_requirement_flags(flag_inc, flag_exc):
    conflicting_flags = sorted(set(flag_inc).intersection(flag_exc))
    if conflicting_flags:
        raise argparse.ArgumentTypeError(
            'flags cannot be both included and excluded: %s' % (
                ', '.join(conflicting_flags)
            )
        )


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Search wireless-regdb countries by required band conditions.'
    )
    parser.add_argument(
        '--db',
        default=str(Path(__file__).resolve().with_name('db.txt')),
        help='path to db.txt (default: db.txt next to this script)',
    )
    parser.add_argument(
        '--require',
        required=True,
        action='append',
        type=parse_requirement,
        metavar='KEY=VALUE,...',
        help=(
            'required condition group; repeat for AND. Required keys: '
            'band, channels, min-bw. Optional repeatable keys: '
            'flag-inc, flag-exc'
        ),
    )
    return parser.parse_args(argv)


def load_countries(db_path):
    with open(db_path, 'r', encoding='utf-8') as db_file:
        return DBParser().parse(db_file)


def fully_covers(rule_start, rule_end, query_start, query_end):
    return rule_start <= query_start and rule_end >= query_end


def matching_permissions(
    country,
    requirement,
):
    included_flags = set(requirement.flag_inc)
    excluded_flags = set(requirement.flag_exc)
    matches = []
    for permission in country.permissions:
        band = permission.freqband
        if band.maxbw < requirement.min_bw:
            continue
        if not fully_covers(
            band.start,
            band.end,
            requirement.query_start_mhz,
            requirement.query_end_mhz,
        ):
            continue
        if not included_flags.issubset(permission.textflags):
            continue
        if excluded_flags.intersection(permission.textflags):
            continue
        matches.append(permission)
    return matches


def format_mhz(value):
    return '%g' % value


def format_eirp(power):
    if power.max_eirp:
        return '%.2f dBm' % power.max_eirp
    return 'N/A'


def format_flags(flags):
    if flags:
        return ','.join(flags)
    return 'none'


def print_matches(matches):
    for country_code, permissions in matches:
        print(country_code)
        for permission in permissions:
            band = permission.freqband
            print(
                '  %s-%s MHz @ %s MHz, EIRP %s, flags %s' % (
                    format_mhz(band.start),
                    format_mhz(band.end),
                    format_mhz(band.maxbw),
                    format_eirp(permission.power),
                    format_flags(permission.textflags),
                )
            )


def main(argv=None):
    args = parse_args(argv)

    try:
        countries = load_countries(args.db)
    except OSError as exc:
        print('error: failed to read %s: %s' % (args.db, exc), file=sys.stderr)
        return 1

    matches = []
    for raw_code, country in sorted(countries.items()):
        permissions = matching_country_permissions(country, args.require)
        if permissions:
            matches.append((raw_code.decode('ascii'), permissions))

    if not matches:
        print('No matching countries.')
        return 0

    print_matches(matches)
    return 0


def matching_country_permissions(country, requirements):
    country_matches = []
    seen_permissions = set()
    for requirement in requirements:
        requirement_matches = matching_permissions(country, requirement)
        if not requirement_matches:
            return []
        for permission in requirement_matches:
            if permission in seen_permissions:
                continue
            seen_permissions.add(permission)
            country_matches.append(permission)
    return country_matches


if __name__ == '__main__':
    sys.exit(main())
