#!/usr/bin/env python3

import argparse
import math
import sys
from pathlib import Path

from dbparse import DBParser, dfs_regions, flag_definitions


IMPLEMENTED_BANDS = ('2.4', '5')
RESERVED_BANDS = ('6', '60', 's1g', 'lc')
ALL_BANDS = IMPLEMENTED_BANDS + RESERVED_BANDS
CHANNEL_LIMITS = {
    '2.4': (1, 14),
    '5': (32, 181),
}
REQUIREMENT_KEYS = (
    'band', 'channels', 'min-bw', 'min-eirp', 'flag-inc', 'flag-exc'
)
REQUIRED_REQUIREMENT_KEYS = ('band', 'channels')
SINGULAR_REQUIREMENT_KEYS = ('band', 'channels', 'min-bw', 'min-eirp')
DFS_REGION_NAMES = dict((value, key) for key, value in dfs_regions.items())
WMMRULE_ETSI_FLAG = 'wmmrule=ETSI'


class Requirement(object):
    def __init__(
        self,
        band,
        channels,
        min_bw,
        min_eirp,
        flag_inc,
        flag_exc,
        query_start_mhz,
        query_end_mhz,
    ):
        self.band = band
        self.channels = channels
        self.min_bw = min_bw
        self.min_eirp = min_eirp
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


def parse_power_value(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('expected a numeric power value')
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
        key for key in REQUIRED_REQUIREMENT_KEYS
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
    min_bw = parse_requirement_min_bw(fields.get('min-bw'))
    min_eirp = parse_requirement_min_eirp(fields.get('min-eirp'))
    validate_requirement_flags(flag_inc, flag_exc)

    start_channel, end_channel = channels
    return Requirement(
        band,
        channels,
        min_bw,
        min_eirp,
        flag_inc,
        flag_exc,
        channel_to_mhz(band, start_channel),
        channel_to_mhz(band, end_channel),
    )


def parse_flag(value, key):
    if value in flag_definitions or value == WMMRULE_ETSI_FLAG:
        return value

    valid_flags = sorted(list(flag_definitions) + [WMMRULE_ETSI_FLAG])
    raise argparse.ArgumentTypeError(
        "invalid %s flag %r (choose from %s)" % (
            key,
            value,
            ', '.join(valid_flags),
        )
    )


def parse_requirement_channels(value):
    try:
        return parse_channel_selection(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError('invalid channels: %s' % exc)


def parse_requirement_min_bw(value):
    if value is None:
        return None
    try:
        min_bw = parse_non_negative_float(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError('invalid min-bw: %s' % exc)
    if min_bw == 0:
        return None
    return min_bw


def parse_requirement_min_eirp(value):
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized.endswith('dbm'):
        return parse_min_eirp_dbm(value.strip()[:-3].strip())

    if normalized.endswith('mw'):
        return parse_min_eirp_mw(value.strip()[:-2].strip())

    raise argparse.ArgumentTypeError(
        'invalid min-eirp: expected VALUEdBm or VALUEmW'
    )


def parse_min_eirp_dbm(value):
    try:
        min_eirp = parse_power_value(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError('invalid min-eirp: %s' % exc)

    if math.isnan(min_eirp):
        raise argparse.ArgumentTypeError('invalid min-eirp: NaN is not valid')
    if math.isinf(min_eirp) and min_eirp > 0:
        raise argparse.ArgumentTypeError(
            'invalid min-eirp: +inf dBm is not valid'
        )
    return min_eirp


def parse_min_eirp_mw(value):
    try:
        min_mw = parse_power_value(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError('invalid min-eirp: %s' % exc)

    if math.isnan(min_mw):
        raise argparse.ArgumentTypeError('invalid min-eirp: NaN is not valid')
    if math.isinf(min_mw):
        raise argparse.ArgumentTypeError(
            'invalid min-eirp: infinite mW is not valid'
        )
    if min_mw < 0:
        raise argparse.ArgumentTypeError(
            'invalid min-eirp: mW value must be greater than or equal to 0'
        )
    if min_mw == 0:
        return float('-inf')
    return 10.0 * math.log10(min_mw)


def parse_country_code(value):
    country_code = value.strip().upper()
    if len(country_code) != 2:
        raise argparse.ArgumentTypeError(
            'country code must be exactly two characters'
        )
    try:
        country_code.encode('ascii')
    except UnicodeEncodeError:
        raise argparse.ArgumentTypeError('country code must be ASCII')
    return country_code


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
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument(
        '--require',
        action='append',
        type=parse_requirement,
        metavar='KEY=VALUE,...',
        help=(
            'required condition group; repeat for AND. Required keys: '
            'band, channels. Optional keys: min-bw, min-eirp. '
            'Optional repeatable keys: flag-inc, flag-exc'
        ),
    )
    query_group.add_argument(
        '--country',
        type=parse_country_code,
        metavar='COUNTRY',
        help='print all rules for one country code, for example US',
    )
    return parser.parse_args(argv)


def load_countries(db_path):
    with open(db_path, 'r', encoding='utf-8') as db_file:
        return DBParser(include_no_indoor=True).parse(db_file)


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
        if requirement.min_bw is not None and band.maxbw < requirement.min_bw:
            continue
        if (
            requirement.min_eirp is not None
            and permission.power.max_eirp < requirement.min_eirp
        ):
            continue
        if not fully_covers(
            band.start,
            band.end,
            requirement.query_start_mhz,
            requirement.query_end_mhz,
        ):
            continue
        if not permission_has_all_flags(permission, included_flags):
            continue
        if permission_has_any_flag(permission, excluded_flags):
            continue
        matches.append(permission)
    return matches


def permission_has_all_flags(permission, flags):
    return all(permission_has_flag(permission, flag) for flag in flags)


def permission_has_any_flag(permission, flags):
    return any(permission_has_flag(permission, flag) for flag in flags)


def permission_has_flag(permission, flag):
    if flag == WMMRULE_ETSI_FLAG:
        return permission.wmmrule is not None
    return flag in permission.textflags


def format_mhz(value):
    return '%g' % value


def format_band(band):
    if band == 's1g':
        return 'S1G'
    if band == 'unknown':
        return 'unknown'
    return '%s GHz' % band


def format_channel_ranges(channels):
    if channels is None:
        return 'unsupported'
    if not channels:
        return 'none'

    ranges = []
    start = channels[0]
    end = channels[0]
    for channel in channels[1:]:
        if channel == end + 1:
            end = channel
            continue
        ranges.append(format_channel_range(start, end))
        start = channel
        end = channel
    ranges.append(format_channel_range(start, end))
    return ','.join(ranges)


def format_channel_range(start, end):
    if start == end:
        return '%d' % start
    return '%d-%d' % (start, end)


def rule_channels(band, freqband):
    if band not in CHANNEL_LIMITS:
        return None

    min_channel, max_channel = CHANNEL_LIMITS[band]
    channels = []
    for channel in range(min_channel, max_channel + 1):
        mhz = channel_to_mhz(band, channel)
        if freqband.start <= mhz <= freqband.end:
            channels.append(channel)
    return channels


def source_eirp_is_na(permission):
    source_eirp = permission.source_eirp
    return source_eirp is not None and source_eirp.upper() == 'N/A'


def format_eirp_dbm(permission):
    if source_eirp_is_na(permission):
        return 'N/A'
    if permission.power.max_eirp is not None:
        return '%.2f dBm' % permission.power.max_eirp
    return 'N/A'


def format_power_mw(permission):
    if source_eirp_is_na(permission):
        return 'N/A'
    source_eirp = permission.source_eirp
    if source_eirp and source_eirp.lower().endswith('mw'):
        return '%s mW' % source_eirp[:-2]
    if permission.power.max_eirp is not None:
        return '%.2f mW' % (10.0 ** (permission.power.max_eirp / 10.0))
    return 'N/A'


def permission_flags(permission):
    flags = list(permission.textflags)
    if permission.wmmrule is not None:
        flags.append(WMMRULE_ETSI_FLAG)
    return flags


def format_flags(flags):
    if flags:
        return ','.join(flags)
    return 'none'


def format_dfs_region(dfs_region):
    return DFS_REGION_NAMES.get(dfs_region, 'DFS-UNSET')


def infer_rule_band(freqband):
    if freqband.end <= 1000:
        return 's1g'
    if freqband.start < 2500 and freqband.end <= 2500:
        return '2.4'
    if freqband.start < 5925:
        return '5'
    if freqband.start < 10000:
        return '6'
    if freqband.start >= 57000:
        return '60'
    return 'unknown'


def print_matches(matches):
    for country_code, dfs_region, entries in matches:
        print('%s, %s' % (country_code, format_dfs_region(dfs_region)))
        for requirement_band, permission in entries:
            freqband = permission.freqband
            print(
                '  band %s, channels %s, %s-%s MHz @ %s MHz, '
                'EIRP %s, power %s, flags %s' % (
                    format_band(requirement_band),
                    format_channel_ranges(
                        rule_channels(requirement_band, freqband)
                    ),
                    format_mhz(freqband.start),
                    format_mhz(freqband.end),
                    format_mhz(freqband.maxbw),
                    format_eirp_dbm(permission),
                    format_power_mw(permission),
                    format_flags(permission_flags(permission)),
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
    if args.country:
        raw_code = args.country.encode('ascii')
        country = countries.get(raw_code)
        if country is None:
            print('error: country %s not found' % args.country, file=sys.stderr)
            return 1
        matches.append((
            args.country,
            country.dfs_region,
            country_permissions(country),
        ))
    else:
        for raw_code, country in sorted(countries.items()):
            permissions = matching_country_permissions(country, args.require)
            if not permissions:
                continue
            matches.append((
                raw_code.decode('ascii'),
                country.dfs_region,
                permissions,
            ))

    if not matches:
        print('No matching countries.')
        return 0

    print_matches(matches)
    return 0


def country_permissions(country):
    return [
        (infer_rule_band(permission.freqband), permission)
        for permission in country.permissions
    ]


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
            country_matches.append((requirement.band, permission))
    return country_matches


if __name__ == '__main__':
    sys.exit(main())
