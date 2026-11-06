"""Offline text→keyword phonemes; no per-user model training or provider request.
Chinese uses pypinyin, English uses the model's public CMU-style lexicon.
Unknown proper names fail with a pronunciation request; never silently use Jarvis.
"""
from functools import lru_cache
import re
import hashlib
import lzma
import struct
from .preferences import DATA, ConfigError

# AJP1 is a lossless container for the complete public CMU source, not a reduced lexicon.
DICTIONARY_FRAME_MAX = 4 * 1024 * 1024
DICTIONARY_RAW_MAX = 4 * 1024 * 1024
_DICTIONARY_HEADER = struct.Struct('>4sIIB')
_DICTIONARY_SHA = 'f7000ec3a90544c0c7c16090d8951779c2b322e14dad5006290f498567d439ea'


def decode_dictionary(blob):
    """Decode one bounded XZ/AJP1 frame. No concatenation, trailing bytes or implicit recovery."""
    if not isinstance(blob, bytes) or not 0 < len(blob) <= 2 * 1024 * 1024:
        raise ValueError('invalid dictionary container size')
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=32 * 1024 * 1024)
        frame = decoder.decompress(blob, max_length=DICTIONARY_FRAME_MAX + 1)
    except (lzma.LZMAError, EOFError) as error:
        raise ValueError('invalid dictionary XZ stream') from error
    if len(frame) > DICTIONARY_FRAME_MAX or not decoder.eof or decoder.unused_data:
        raise ValueError('unbounded, truncated or trailing dictionary stream')
    if len(frame) < _DICTIONARY_HEADER.size:
        raise ValueError('truncated dictionary header')
    magic, count, word_size, nphones = _DICTIONARY_HEADER.unpack_from(frame)
    if magic != b'AJP1' or not 0 < count <= 200000 or not 0 < nphones < 256:
        raise ValueError('invalid dictionary header')
    position, table = _DICTIONARY_HEADER.size, []
    for _ in range(nphones):
        end = frame.find(b'\0', position)
        phone = frame[position:end] if end >= 0 else b''
        if not 0 < len(phone) <= 32 or any(c <= 32 or c == 127 for c in phone):
            raise ValueError('invalid dictionary phoneme table')
        try:
            phone.decode('utf-8')
        except UnicodeDecodeError as error:
            raise ValueError('invalid dictionary phoneme encoding') from error
        if phone in table:
            raise ValueError('duplicate dictionary phoneme')
        table.append(phone)
        position = end + 1
    if not 2 * count <= word_size <= len(frame) - position - 2 * count:
        raise ValueError('invalid dictionary word stream size')
    words, sounds = frame[position:position + word_size], frame[position + word_size:]
    wi = si = 0
    previous, output = b'', bytearray()
    for _ in range(count):
        if wi >= len(words):
            raise ValueError('truncated dictionary word')
        prefix = words[wi]
        wi += 1
        end = words.find(b'\0', wi)
        if prefix > len(previous) or end < 0:
            raise ValueError('invalid dictionary word prefix')
        word = previous[:prefix] + words[wi:end]
        if not 0 < len(word) < 256 or any(c <= 32 or c == 127 for c in word):
            raise ValueError('invalid dictionary word')
        try:
            word.decode('utf-8')
        except UnicodeDecodeError as error:
            raise ValueError('invalid dictionary word encoding') from error
        wi = end + 1
        end = sounds.find(b'\0', si)
        ids = sounds[si:end] if end >= 0 else b''
        if not 0 < len(ids) <= 256 or any(not 0 < code <= nphones for code in ids):
            raise ValueError('invalid dictionary pronunciation')
        si = end + 1
        output += word + b' ' + b' '.join(table[code - 1] for code in ids) + b'\n'
        if len(output) > DICTIONARY_RAW_MAX:
            raise ValueError('dictionary output limit exceeded')
        previous = word
    if wi != len(words) or si != len(sounds):
        raise ValueError('trailing dictionary records')
    return bytes(output)


def dictionary_bytes():
    raw = decode_dictionary((DATA / 'en.phone.xz').read_bytes())
    if len(raw) != 3330061 or hashlib.sha256(raw).hexdigest() != _DICTIONARY_SHA:
        raise ValueError('packaged dictionary content changed')
    return raw


@lru_cache(maxsize=1)
def lexicon():
    words = {}
    for line in dictionary_bytes().decode('utf-8').splitlines():
        row = line.split()
        if len(row) > 1:
            words.setdefault(row[0], row[1:])
    return words

@lru_cache(maxsize=1)
def tokens():return {line.split()[0] for line in (DATA/'wake-tokens.txt').read_text().splitlines() if line.strip()}

def keyword(phrase,pronunciation=''):
    if pronunciation:
        result=pronunciation.split()
    else:
        from pypinyin import pinyin,Style
        result=[]
        # 中文 mixed with English names is supported. 英文 must be separated into dictionary words.
        for part in re.findall(r'[\u3400-\u9fff]|[a-zA-Z][a-zA-Z\'.-]*|[^\s]',phrase):
            if re.fullmatch(r'[\u3400-\u9fff]',part):
                initial=pinyin(part,style=Style.INITIALS,strict=False)[0][0]
                final=pinyin(part,style=Style.FINALS_TONE,strict=False)[0][0]
                if initial:result.append(initial)
                if final:result.append(final)
            elif part.upper() in lexicon():result+=lexicon()[part.upper()]
            else:raise ConfigError('voice.wake_word','unknown pronunciation; set voice.wake_pronunciation phonemes for this name')
    if not result or len(result)>100 or any(x not in tokens() for x in result):raise ConfigError('voice.wake_pronunciation','phonemes must exist in bundled wake-tokens.txt')
    return ' '.join(result)+' @agentj'
