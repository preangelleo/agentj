"""Owner-funded OpenRouter Jev. Key is a host secret-card file, never model state."""
import json
from ..provider_profiles import secure_read
from .store import BotError
from .provider import Provider,invoke_jev

ENDPOINT='https://openrouter.ai/api'
NAME='OPENROUTER_API_KEY'

def resolve(store,bid):
    try:key=secure_read(store.secrets(bid)/NAME,8192).decode().strip()
    except (OSError,ValueError,UnicodeError):raise BotError('openrouter_required') from None
    if not key or any(c.isspace() for c in key):raise BotError('openrouter_required')
    return Provider(ENDPOINT,'~typesafe/jev-latest','jev',key,'openrouter')

def probe(p,transport=None):
    kw={'transport':transport} if transport else {}
    result=invoke_jev(p,[{'role':'user','content':json.dumps({'stage':'setup','scope':'Answer product questions','candidate':'Hello, how can I help?'})}],**kw)
    if json.loads(result.text)!={'allow':True}:raise BotError('openrouter_probe_refused')
    return result
