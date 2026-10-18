"""Copyable commands and clickable links in host-owned remote messages only."""
import re

# Leave existing fenced code, inline code and Markdown links intact.
_PART = re.compile(r"(```[\s\S]*?```|`[^`\n]+`|\[[^]\n]*\]\([^\n)]*\))")
_COMMAND = re.compile(r"^(?:agentj(?:-\w+)?|aj|claude|codex|opencode|loginctl|systemctl|launchctl)(?:\s|$)|^/(?:start|resume)(?:\s|$)")
_URL = re.compile(r"https?://[^\s<>\[\]`\u3000-\u9fff\uff00-\uffef]+")


def remote(text: str) -> str:
    def plain(s):
        def link(m):
            url = m[0].rstrip('.,;:!?，。；：！？）')
            while url.endswith(')') and url.count(')') > url.count('('):
                url = url[:-1]
            tail = m[0][len(url):]
            # Parentheses in URLs must not terminate the Markdown destination.
            return '[' + url + '](' + url.replace('(', '%28').replace(')', '%29') + ')' + tail
        return _URL.sub(link, s)
    out = []
    for i, part in enumerate(_PART.split(text)):
        if i % 2:
            if part.startswith('`') and not part.startswith('```'):
                if _COMMAND.match(part[1:-1]):
                    part = '\n```sh\n' + part[1:-1] + '\n```\n'
                elif _URL.fullmatch(part[1:-1]):
                    part = plain(part[1:-1])
            out.append(part)
        else:
            out.append(plain(part))
    return ''.join(out)
