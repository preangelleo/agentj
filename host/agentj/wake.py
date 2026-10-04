"""Offline text→keyword phonemes; no per-user model training or provider request.
Chinese uses pypinyin, English uses the model's public CMU-style lexicon.
Unknown proper names fail with a pronunciation request; never silently use Jarvis.
"""
from functools import lru_cache
import re
import gzip
from .preferences import DATA, ConfigError

@lru_cache(maxsize=1)
def lexicon():
    words={}
    for line in gzip.decompress((DATA/'en.phone.gz').read_bytes()).decode('utf-8').splitlines():
        row=line.split()
        if len(row)>1:words.setdefault(row[0],row[1:])
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
