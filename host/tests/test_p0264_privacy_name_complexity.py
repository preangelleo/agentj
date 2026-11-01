"""P0264: preserve the name exemption language without exponential backtracking."""
import _hermetic  # noqa: F401,I001
import itertools
import pathlib
import random
import re
import subprocess
import sys
import unittest

from agentj import privacy

HOST = pathlib.Path(__file__).resolve().parents[1]
# Frozen pre-fix recognizer: use only bounded synthetic strings in this oracle.
LEGACY = re.compile(r"(?:[A-Z][a-z]{2,}|[a-z]{3,}|[A-Z]{2,}(?![a-z])|\d+|[a-z]\d+|[A-Z]\d+)+|[A-Za-z]{1,2}")


class NameLanguage(unittest.TestCase):
    def test_real_namelike_seam_has_bounded_adversarial_runtime(self):
        code = """from agentj import privacy
for n in (80, 1000, 100000):
    assert not privacy._namelike('ordinary-' + 'a' * n + 'B')
assert privacy._namelike('ordinary-letters')
print('bounded-name-seam PASS')
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=HOST,
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stdout.strip(), 'bounded-name-seam PASS')

    def test_exhaustive_short_language_equivalence(self):
        for length in range(7):
            for chars in itertools.product('aAzZ0١²-', repeat=length):
                part = ''.join(chars)
                self.assertEqual(privacy._name_part(part), bool(LEGACY.fullmatch(part)), part)

    def test_seeded_short_language_equivalence(self):
        rng = random.Random(264)
        for _ in range(5000):
            part = ''.join(rng.choice('abcABCxyzXYZ019١１２𝟙²é_-') for _ in range(rng.randrange(24)))
            self.assertEqual(privacy._name_part(part), bool(LEGACY.fullmatch(part)), part)

    def test_chunk_boundaries_unicode_decimal_and_normal_names(self):
        chunks = ('abc', 'Abc', 'ABC', 'ab', 'A', '0', '١', '１２', '𝟙', 'a0', 'Z١',
                  '²', 'é', '汉', '-', '', 'ABa', 'ABabc', 'Ab', 'aA')
        for count in (1, 2, 3):
            for parts in itertools.product(chunks, repeat=count):
                part = ''.join(parts)
                self.assertEqual(privacy._name_part(part), bool(LEGACY.fullmatch(part)), part)
        for codepoint in range(sys.maxunicode + 1):
            char = chr(codepoint)
            if char.isdecimal():
                self.assertTrue(privacy._name_part(char))
                self.assertIsNotNone(LEGACY.fullmatch(char))
        for part in ('OpenRouter', 'API', 'ordinary', 'SuperTool123', 'ABCabc', 'ABc',
                     'a１２B', 'Abc١٢', 'XYabc１２ZZ', 'αβγ', 'Àbc'):
            self.assertEqual(privacy._name_part(part), bool(LEGACY.fullmatch(part)), part)


if __name__ == '__main__':
    unittest.main()
