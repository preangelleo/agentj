    INSTALL_CODE=$(printf '%s' "$INSTALL_CODE" | python3 -c "$(cat <<'EXTRACT_CODE'
import re,sys,unicodedata
s=unicodedata.normalize('NFKC',sys.stdin.read()).translate({ord(c):'-' for c in '\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d\u30fc'})
for t in (s,re.sub(r'\s+','',s)):
    m=re.search(r'AJI-([0-9a-f]{32})(?![0-9a-f])',t,re.I)
    if m: print('AJI-'+m.group(1).lower()); sys.exit()
print('ajt' if re.search(r'aj[th]_[A-Za-z0-9_-]{43}',s) else '')
EXTRACT_CODE
)")
      [[ $INSTALL_CODE == ajt ]] && printf 'ajt_ / ajh_ 开头的是设置码，只给已经装好 Agent J 的 Agent J 主机绑定席位用。 / A code starting with ajt_ / ajh_ is a setup code; it only links an Agent J host that already has Agent J installed.\n'
