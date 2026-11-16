#!/usr/bin/env python3
"""Check every installed package's license expression against the accepted set."""
import argparse
from pathlib import Path
import sys
import unittest


def check(manifest: str, accepted: str) -> list[str]:
    allowed = {line.strip() for line in accepted.splitlines()
               if line.strip() and not line.lstrip().startswith('#')}
    errors = []
    licenses = []
    packages = 0
    for line in manifest.splitlines():
        if line.startswith('PACKAGE NAME:'):
            packages += 1
        if line.startswith('LICENSE:'):
            expression = line.partition(':')[2].strip()
            licenses.append(expression)
            if not expression or expression not in allowed:
                errors.append(f'Unaccepted license: {expression or "<empty>"}')
    if not allowed:
        errors.append('Accepted license list is empty')
    if not packages or packages != len(licenses):
        errors.append('Manifest must contain one LICENSE entry per package')
    return errors


class Tests(unittest.TestCase):
    def test_accepted_expressions(self):
        self.assertEqual(check('PACKAGE NAME: a\nLICENSE: MIT & BSD-3-Clause\n',
                               'MIT & BSD-3-Clause\n'), [])

    def test_unknown_and_empty_license(self):
        self.assertEqual(len(check('PACKAGE NAME: a\nLICENSE: CLOSED\nPACKAGE NAME: b\nLICENSE: \n', 'MIT\n')), 2)

    def test_missing_entries_and_empty_list(self):
        self.assertTrue(check('PACKAGE NAME: a\n', 'MIT\n'))
        self.assertTrue(check('PACKAGE NAME: a\nLICENSE: MIT\n', '# none\n'))

    def test_expressions_are_not_partially_accepted(self):
        self.assertTrue(check('PACKAGE NAME: a\nLICENSE: MIT & unknown\n', 'MIT\n'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true')
    parser.add_argument('manifest', nargs='?', type=Path)
    parser.add_argument('accepted', nargs='?', type=Path)
    args = parser.parse_args()
    if args.test:
        return 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    if not args.manifest or not args.accepted:
        parser.error('manifest and accepted license list are required')
    try:
        errors = check(args.manifest.read_text(encoding='utf-8'),
                       args.accepted.read_text(encoding='utf-8'))
    except (OSError, UnicodeError) as exc:
        print(f'License check: {exc}', file=sys.stderr)
        return 1
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('OK: all image license expressions are accepted')
    return 0


if __name__ == '__main__':
    sys.exit(main())
