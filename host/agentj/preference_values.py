"""Pure dotted preference values; no State, files, defaults or runtime imports.

The preferences facade re-exports these helpers for existing callers.
"""

def get(doc, key, default=None):
    for p in key.split('.'):
        if not isinstance(doc,dict) or p not in doc: return default
        doc=doc[p]
    return doc

def put(doc,key,value):
    parts=key.split('.'); at=doc
    for p in parts[:-1]: at=at.setdefault(p,{})
    at[parts[-1]]=value


class ConfigError(ValueError):
    def __init__(self, key, detail, line=1, code=1):
        self.key, self.detail, self.line, self.code = key, detail, line, code
        super().__init__(f'line {line}, {key}: {detail}')
    def result(self):
        return {'ok':False,'key':self.key,'line':self.line,'error':self.detail,'needs':['human'] if self.code==2 else []}

