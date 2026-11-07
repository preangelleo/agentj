"""Offline contracts for the installation continuation; no real platform effects."""
import copy
import json
import unittest
from unittest.mock import patch
from agentj import initialization


def schema():
    return {'$schema':'http://json-schema.org/draft-07/schema#', 'type':'object',
            'x-capabilities':[{'id':'writing','stage_zh':'Writing', 'required':True,
             'providers':[{'id':'openai','env':[{'name':'OPENAI_API_KEY','secret':True}]}],
             'if_missing_zh':'Unavailable', 'degrade_zh':'Manual preparation', 'adapter':None}]}

class Initialization(unittest.TestCase):
    def test_presence_only_never_cloud_ready(self):
        d = initialization.parse(json.dumps(schema()).encode(), {})
        r = initialization.assess(d, env_names={'OPENAI_API_KEY'}, cli_names=set(), fonts=set())
        self.assertEqual(r['rows'][0]['status'], 'missing')
        self.assertEqual(r['rows'][0]['evidence'], 'unknown')
        self.assertNotIn('secret-value', str(r))

    def test_local_ready_optional_degraded_manual_unknown(self):
        s = schema(); c = s['x-capabilities'][0]
        c['providers'] = [{'id':'local','cli':[{'name':'ffmpeg'}]}]
        d = initialization.parse(json.dumps(s).encode(), {})
        self.assertEqual(initialization.assess(d, env_names=set(), cli_names={'ffmpeg'}, fonts=set())['rows'][0]['status'], 'ready')
        c['required'] = False
        c['providers'][0]['manual'] = True
        d = initialization.parse(json.dumps(s).encode(), {})
        self.assertEqual(initialization.assess(d, env_names=set(), cli_names={'ffmpeg'}, fonts=set())['rows'][0]['status'], 'degraded')

    def test_unknown_fields_paths_secrets_refs_and_size_refused(self):
        changes = [lambda s:s.update({'$ref':'#'}),
                   lambda s:s['x-capabilities'][0].update({'adapter':'../escape'}),
                   lambda s:s['x-capabilities'][0]['providers'][0].update({'dest':'/tmp/key'}),
                   lambda s:s['x-capabilities'][0]['providers'][0].update({'verifyURL':'https://evil.invalid'}),
                   lambda s:s['x-capabilities'][0].update({'degrade_zh':'sk-'+'a'*48}),
                   lambda s:s['x-capabilities'][0]['providers'][0]['env'][0].update({'name':'LD_PRELOAD'})]
        for change in changes:
            s = schema(); change(s)
            with self.subTest(s=s), self.assertRaises(ValueError): initialization.parse(json.dumps(s).encode(), {})
        with self.assertRaises(ValueError): initialization.parse(b' '*65537, {})

    def test_fixed_key_action_missing_only(self):
        d = initialization.parse(json.dumps(schema()).encode(), {})
        r = initialization.assess(d, env_names=set(), cli_names=set(), fonts=set())
        self.assertEqual(r['key_requests'], [{'provider':'openai','name':'OPENAI_API_KEY'}])
        self.assertNotIn('dest', str(r))
        self.assertNotIn('verify', str(r))

    def test_config_and_manual_are_explicit_unknown(self):
        s=schema(); s['properties']={'voice':{'type':'string','enum':['quiet','normal'],'default':'quiet'}}
        s['x-capabilities'][0]['providers']=[{'id':'local','manual':True}]
        r=initialization.assess(initialization.parse(json.dumps(s).encode(), {}), env_names=set(), cli_names=set(), fonts=set())
        self.assertEqual(r['config'], ['voice'])
        self.assertEqual(r['rows'][0]['evidence'], 'unknown')

class MissingSwitch(unittest.TestCase):
    def test_ark_missing_offers_native_owner_entry_without_npm(self):
        from agentj import ark
        with patch('agentj.opencode_install.offer', return_value={'op':'ai.install','params':{'cli':'opencode','source':'official'}}) as offer:
            r=ark.switch(object(), detector=lambda:{'opencode':None})
        offer.assert_called_once()
        self.assertEqual(r['install_action']['params']['cli'], 'opencode')
        self.assertNotIn('npm', ark.say_switch(r, 'en'))
