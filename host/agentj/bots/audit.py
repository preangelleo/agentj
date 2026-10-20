"""Owner-funded Jev through the selected official TypeSafe or OpenRouter route. Key is a host secret-card file, never model state."""
import json
from ..provider_profiles import secure_read
from .store import BotError
from .provider import Provider,invoke_jev

ENDPOINT='https://openrouter.ai/api'
NAME='OPENROUTER_API_KEY'

def _route():
    # P82 guarantee: no ambient-environment fallback. The host-wide route (P409) reads only the owner's secret-card
    # key file (Models & API keys), never a key inherited from the process environment.
    from .. import jev
    from ..provider_runtime import fresh_environment
    return jev.resolve(env=fresh_environment({},extra=[r[0] for r in jev.ROUTES.values()]))

def settings(c):
    vendor=_route().mode or c['safety']['provider']
    return ('https://api.typesafe.ai','TYPESAFE_API_KEY','typesafe') if vendor=='typesafe' else (ENDPOINT,NAME,'openrouter')

def resolve(store,bid):
    from .. import jev
    route=_route()
    if route.mode=='off':return None
    # Global route wins; old bot-local keys remain usable when no host key exists.
    endpoint,name,vendor=settings(store.get(bid))
    if route.mode in jev.ROUTES:
        vendor=route.mode
        name=jev.ROUTES[vendor][0]
        endpoint='https://api.typesafe.ai' if vendor=='typesafe' else ENDPOINT
    key=route.key
    if not key:
        try:key=secure_read(store.secrets(bid)/name,8192).decode().strip()
        except (OSError,ValueError,UnicodeError):raise BotError('openrouter_required' if vendor=='openrouter' else 'typesafe_required') from None
    if not key or any(c.isspace() for c in key):raise BotError('openrouter_required' if vendor=='openrouter' else 'typesafe_required')
    return Provider(endpoint,'jev-1.13.0' if vendor=='typesafe' else '~typesafe/jev-latest','jev',key,vendor)

def probe(p,transport=None):
    if p is None:return None  # explicitly signed off: no upstream call
    kw={'transport':transport} if transport else {}
    result=invoke_jev(p,[{'role':'user','content':json.dumps({'stage':'setup','scope':'Answer product questions','candidate':'Hello, how can I help?'})}],**kw)
    if json.loads(result.text)!={'allow':True}:raise BotError('openrouter_probe_refused')
    return result
