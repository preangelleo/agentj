"""Lossless packaged lexicon container: independent frames and hostile-input controls."""
import _hermetic
import gzip
import hashlib
import lzma
from pathlib import Path
import struct
import unittest
from agentj import wake

HEADER = struct.Struct('>4sIIB')

def frame(words=b'\0A\0\x01B\0', sounds=b'\x01\0\x01\x02\0', *, count=2, table=b'AA\0B\0', nphones=2, word_size=None, magic=b'AJP1'):
    return HEADER.pack(magic, count, len(words) if word_size is None else word_size, nphones)+table+words+sounds

class DictionaryCodec(unittest.TestCase):
    def decode(self, raw):
        return wake.decode_dictionary(lzma.compress(raw))

    def test_independent_valid_frame_preserves_prefix_and_every_space_newline(self):
        self.assertEqual(self.decode(frame()), b'A AA\nAB AA B\n')

    def test_pronunciation_prefixes_preserve_full_rows_and_reject_out_of_range(self):
        self.assertEqual(self.decode(frame(sounds=b'\x01\x01\0\x02\x02\0', magic=b'AJP2')), b'A AA\nAB AA B\n')
        for sounds in (b'\x02\x01\0\x02\x02\0', b'\x01\x01\0\x03\x02\0', b'\0\x02\x02\0'):
            with self.assertRaisesRegex(ValueError, 'pronunciation prefix'):
                self.decode(frame(sounds=sounds, magic=b'AJP2'))

    def test_marked_prefix_records_preserve_rows_and_refuse_ambiguous_data(self):
        words, sounds = b'\x80A\x81B', b'\x81\x01\x82\x02'
        self.assertEqual(self.decode(frame(words, sounds, magic=b'AJP3')), b'A AA\nAB AA B\n')
        self.assertEqual(self.decode(frame(words, b'\x81\x01\x82', magic=b'AJP3')), b'A AA\nAB AA\n')
        invalid = [
            frame(b'A' + words, sounds, magic=b'AJP3'),
            frame(words, b'\x01' + sounds, magic=b'AJP3'),
            frame(words, sounds + b'\x81', magic=b'AJP3'),
            frame(words, b'\x81\0\x82\x02', magic=b'AJP3'),
            frame(words, b'\x81\x01\x83\x02', magic=b'AJP3'),
            frame(words, sounds, nphones=128, magic=b'AJP3'),
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.decode(raw)

    def test_pronunciation_lookback_reuses_only_available_records(self):
        words = b'\x80A\x81B\x81C\x81D\x81E'
        sounds = b'\x84\x01\x84\x02\x89\x02\x84\x02\x8b'
        self.assertEqual(self.decode(frame(words, sounds, magic=b'AJP4', count=5)),
                         b'A AA\nAB B\nAC AA B\nAD B\nAE AA\n')
        for invalid in (b'\x85\x01\x84\x02', b'\x84\x01\x8b',
                        b'\x84\x01\x8c', b'\x84\x01\x80\x02'):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, 'pronunciation (?:reference|prefix)'):
                    self.decode(frame(words[:4], invalid, magic=b'AJP4'))

    def test_original_hash_and_all_rows_are_unchanged(self):
        raw = wake.dictionary_bytes()
        self.assertEqual(len(raw), 3330061)
        self.assertEqual(len(raw.splitlines()), 126052)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), 'f7000ec3a90544c0c7c16090d8951779c2b322e14dad5006290f498567d439ea')
        expected = {}
        for line in raw.decode().splitlines():
            row=line.split()
            if len(row)>1: expected.setdefault(row[0],row[1:])
        self.assertEqual(wake.lexicon(), expected)

    def test_invalid_frames_fail_with_value_error(self):
        valid=frame()
        invalid = [b'', valid[:8], b'BAD!'+valid[4:], frame(count=0), frame(count=200001),
                   frame(nphones=0), frame(table=b'\0B\0'), frame(table=b'AA\0AA\0'),
                   frame(table=b'\xff\0B\0'), frame(word_size=9999999), frame(words=b'\x01A\0\x01B\0'),
                   frame(words=b'\0\xff\0\x01B\0'), frame(words=b'\0\0\x00B\0'),
                   frame(sounds=b'\x03\0\x01\0'), frame(sounds=b'\0\x01\0'),
                   frame(sounds=b'\x01\0\x01'), frame(words=b'\0A\0\x01B\0extra'), valid+b'extra']
        for raw in invalid:
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(ValueError): self.decode(raw)

    def test_xz_truncated_corrupt_concatenated_or_trailing_data_fails(self):
        blob=lzma.compress(frame())
        for data in (blob[:-1], b'invalid', blob+b'extra', blob+blob,
                     blob[:20]+bytes([blob[20]^1])+blob[21:]):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError): wake.decode_dictionary(data)

    def test_decompression_and_output_limits_are_enforced(self):
        with self.assertRaises(ValueError): wake.decode_dictionary(lzma.compress(b'x' * (wake.DICTIONARY_FRAME_MAX+1)))
        with self.assertRaises(ValueError): self.decode(frame(words=b'\0'+b'A'*256+b'\0\0B\0'))
        with self.assertRaises(ValueError): self.decode(frame(sounds=b'\x01'*257+b'\0\x01\0'))
        many=2000
        bomb=frame(words=b'\0A\0'+b'\x01\0'*(many-1), sounds=(b'\x01'*256+b'\0')*many, count=many, table=b'P'*32+b'\0', nphones=1)
        with self.assertRaisesRegex(ValueError, 'output limit'): self.decode(bomb)

if __name__ == '__main__': unittest.main()
