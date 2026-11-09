"""CLI credential literals: actual Noise card/signatures; no command is executed."""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from agentj import approvals, approval_review, privacy, action_policy, owner_route
from _p541_device import Phone, _owner_host
from test_l1 import _state, _ready
from test_p513b_summary import real_outbound, outbound_card


# Finite literal-token classes; no claim to authorize or execute arbitrary shell.
LITERAL_GRAMMAR = [('closed_quote_newlines', "'literal-grammar-\nprivate-X'", False), ('closed_quote_newlines', '"literal-grammar-\nprivate-X"', False), ('closed_quote_newlines', "'literal-grammar-\rprivate-X'", False), ('closed_quote_newlines', '"literal-grammar-\rprivate-X"', False), ('escaped_newlines', 'literal-grammar-\\\nprivate-X', False), ('escaped_newlines', '"literal-grammar-\\\nprivate-X"', False), ('dollar_literal_quotes', "$'literal-grammar-private-X'", False), ('dollar_literal_quotes', "$'literal-grammar-\\'private-X'", False), ('dollar_literal_quotes', "$'literal-grammar-\\x24HOME-private-X'", False), ('dollar_literal_quotes', '$"literal-grammar-private-X"', False), ('dollar_literal_quotes', "'$HOME-literal-grammar-private-X'", False), ('dollar_literal_quotes', "'${HOME}-literal-grammar-private-X'", False), ('attached_dash_values', '-q7Z', True), ('attached_dash_values', '-q7Z5x', True)]
DYNAMIC_OR_MISSING = ['$PASSWORD', '${PASSWORD}', '$((1+2))', '$(printf literal-grammar-action)', '`printf literal-grammar-action`', '<(printf literal-grammar-action)', '"$(printf literal-grammar-action)"', '"prefix$(printf literal-grammar-action)"', '$"prefix$(printf literal-grammar-action)"', 'pre"$PASSWORD"', '--verbose', '']


class CliValues(unittest.TestCase):
    def test_literal_password_forms_and_adjacent_effects(self):
        for spelling in ('--password synthetic-short-X', '--password=synthetic-short-X',
                         '--password \"synthetic short X\"', "--password='synthetic short X'",
                         "--password 'synthetic$literal-X'", "--password 'synthetic`literal-Y'", "--password 'synthetic'\\''quote-X'", '--password "synthetic\\$literal-X"', '--password "synthetic\\`literal-Y"',
                         '--password z', '--passwd=123', '--passphrase \"two words\"'):
            with self.subTest(spelling=spelling):
                raw='curl '+spelling+' --output /fixture/result; rm /fixture/adjacent-target'
                shown=privacy.owner_text(raw)
                self.assertIn('<redacted>',shown)
                self.assertNotIn(spelling,shown)
                flag=spelling.split('=',1)[0]+'=' if '=' in spelling.split(' ',1)[0] else spelling.split(' ',1)[0]+' '
                self.assertEqual(shown,'curl '+flag+'<redacted> --output /fixture/result; rm /fixture/adjacent-target')
                self.assertIn('--output /fixture/result; rm /fixture/adjacent-target',shown)
                self.assertEqual(privacy.owner_text(shown),shown)

    def test_no_credential_flags_and_dynamic_references_are_preserved(self):
        for text in ('password policy documentation', '--password-file /fixture/password.txt',
                     '--passwordless true', '--password $PASSWORD', '--password=${PASSWORD}',
                     '--password \"$PASSWORD\"', '--password \"$(cat /fixture/password.txt)\"',
                     '--password=\"$PASSWORD\"', '--password=\"$(cat /fixture/password.txt)\"',
                     '--password --help', '--password x', 'curl --output /fixture/password --data ordinary'):
            with self.subTest(text=text):
                self.assertEqual(privacy.owner_text(text),text)


    def test_literal_shell_grammar_inventory(self):
        self.assertEqual({row[0] for row in LITERAL_GRAMMAR},
                         {'closed_quote_newlines','escaped_newlines','dollar_literal_quotes','attached_dash_values'})
        for flagname in ('--password','--passwd','--passphrase'):
            for kind,word,attached in LITERAL_GRAMMAR:
                with self.subTest(flagname=flagname,kind=kind,word=word):
                    flag=flagname+('=' if attached else ' ')
                    tail=' --output /fixture/result; rm /fixture/adjacent-target'
                    shown=privacy.owner_text('curl '+flag+word+tail)
                    self.assertEqual(shown,'curl '+flag+'<redacted>'+tail)
                    self.assertEqual(privacy.owner_text(shown),shown)
            for word in DYNAMIC_OR_MISSING:
                for flag in (flagname+' ',flagname+'=') if word!='--verbose' else (flagname+' ',):
                    with self.subTest(dynamic=word,flag=flag):
                        raw='curl '+flag+word+' --output /fixture/result; rm /fixture/adjacent-target'
                        self.assertEqual(privacy.owner_text(raw),raw)
        raw="curl --password 'literal-grammar-private-X'\nprintf actual-neighbour"
        self.assertEqual(privacy.owner_text(raw),'curl --password <redacted>\nprintf actual-neighbour')

    def test_cli_projection_keeps_genuine_form_and_json_credentials_private(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            project=action_policy.display_normalizer(workdir=root)
        value='literal-grammar-private-X'
        for payload in ({'passwd':value},{'--passwd':value},json.dumps({'passwd':value}),
                        'passwd='+value+'&mode=write',
                        'https://example.invalid/?passwd='+value+'&mode=write',
                        'https://example.invalid/?--passwd='+value+'&mode=write'):
            with self.subTest(payload=payload):
                shown=project(payload)
                self.assertNotIn(value,json.dumps(shown))
                if isinstance(shown,str) and 'mode=write' in str(payload):
                    self.assertIn('mode=write',shown)


    def test_shared_json_form_colon_and_cli_context_inventory(self):
        value='synthetic-privacy-X'
        for flag in ('--password','--passwd','--passphrase'):
            contexts=(json.dumps({flag:value,'mode':'write'}),
                      flag+'='+value+'&mode=write',
                      flag+': '+value+'\nmode: write',
                      '"'+flag+'": "'+value+'"\nmode: write')
            for raw in contexts:
                with self.subTest(flag=flag,raw=raw):
                    # Reproduce the actual shared mail body first; no card
                    # projector can hide a regression in this independent path.
                    self.assertNotIn(value,owner_route.plan(raw,'mail','en',False)[0])
                    found=privacy.credentials(raw)
                    self.assertEqual(len(found),1)
                    self.assertEqual(found[0][1],value)
                    self.assertNotIn(value,privacy.owner_text(raw))
                    for via in ('mail','telegram'):
                        for cards in (False,True):
                            body,values=owner_route.plan(raw,via,'en',cards)
                            self.assertNotIn(value,body)
                            self.assertIn('mode',body);self.assertIn('write',body)
                            self.assertEqual([v for _,v in values],[value] if cards else [])
            raw='curl '+flag+'="prefix$(printf actual-grammar-effect)" --output /fixture/result'
            self.assertEqual(privacy.credentials(raw),[])
            self.assertEqual(privacy.owner_text(raw),raw)
            self.assertEqual(owner_route.plan(raw,'mail','en',False),(raw,[]))
        # Existing ordinary credential keys remain recognized directly, without
        # the approval-card projector doing their masking first.
        raw=json.dumps({'passwd':value,'mode':'write'})
        self.assertNotIn(value,privacy.owner_text(raw))
        self.assertEqual(len(privacy.credentials(raw)),1)


class NoiseCards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP);self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        env=mock.patch.dict(os.environ,{'HOME':str(root/'home'),'XDG_CONFIG_HOME':str(root/'config')})
        env.start();self.addCleanup(env.stop)
        self.st=_state(str(root));self.sent=[];self.h=_owner_host(self.st,self.sent)
        self.h.ask_ttl=3
        self.ph=Phone(self.st,with_passkey=True);self.ph.policy_host=self.h
        self.s=_ready(self.h,self.ph);real_outbound(self.h,self.s,self.sent)

    async def open(self,tool,inp):
        task=asyncio.create_task(self.h.ask(tool,inp,_harness_permission=(tool=='Bash'),grant_info=True))
        async def close():
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        self.addAsyncCleanup(close)
        card=await outbound_card(self,self.h,self.sent,task)
        return task,self.h.asks[card['id']],card

    async def test_actual_noise_plain_and_uv_cards_bind_safe_display_and_original_input(self):
        with mock.patch.object(self.h,'hist_emit'):
            self.h.desktop_input('Synthetic owner instruction --password synthetic-source-X; retain actual effects.')
        for tool in ('Bash','agentj fn run'):
            with self.subTest(tool=tool):
                command="curl --password synthetic-short-X --passwd 'synthetic$literal-X' --passphrase 'synthetic`literal-Y' -H 'Authorization: Bearer synthetic-bearer-Y' 'https://example.invalid/?token=synthetic-query-Z' --output /fixture/result; rm /fixture/adjacent-target"
                inp={'command':command,'name':'synthetic-only'}
                untouched=copy.deepcopy(inp);raw_digest=approvals.input_digest(inp)
                self.sent.clear();task,ask,card=await self.open(tool,inp)
                self.assertEqual(inp,untouched);self.assertEqual(ask.tool_input,untouched)
                self.assertEqual(ask.input_sha,raw_digest)
                self.assertEqual(card['envelope']['input_sha256'],raw_digest)
                self.assertEqual(card['summary'],card['envelope']['details'])
                self.assertEqual(card['envelope_sha256'],approval_review.digest(card['envelope']))
                self.assertEqual(ask._list_display_snapshot,card['summary'])
                for value in ('synthetic-short-X','synthetic-bearer-Y','synthetic-query-Z','synthetic-source-X','synthetic$literal-X','synthetic`literal-Y'):
                    self.assertNotIn(value,json.dumps(card))
                self.assertIn('/fixture/result',card['summary'])
                self.assertIn('rm /fixture/adjacent-target',card['summary'])
                self.assertEqual(ask.require_passkey,tool!='Bash')
                # A signature over altered visible details does not authorize the
                # original card. The actual displayed snapshot does, with fresh
                # request-bound UV for the listed action.
                forged=self.ph.answer(card,True,summary=card['summary']+' changed')
                await self.h._answer(self.s,forged);self.assertFalse(ask.fut.done())
                answer=self.ph.answer(card,True)
                if ask.require_passkey:
                    no_uv=dict(answer);no_uv.pop('fa')
                    await self.h._answer(self.s,no_uv);self.assertFalse(ask.fut.done())
                await self.h._answer(self.s,answer)
                result=await task
                self.assertEqual(result['behavior'],'allow');self.assertEqual(result['updatedInput'],untouched)
                record=approvals.read_log(self.st)[-1]
                self.assertEqual(approvals.check_record(self.st,record),'ok')
                await self.h._answer(self.s,answer)
                self.assertEqual(len([r for r in approvals.read_log(self.st) if r.get('id')==ask.rid]),1)


    async def test_actual_noise_literal_inventory_and_dynamic_effects(self):
        for flagname in ('--password','--passwd','--passphrase'):
            for kind,word,attached in LITERAL_GRAMMAR:
                with self.subTest(flagname=flagname,kind=kind,word=word):
                    flag=flagname+('=' if attached else ' ')
                    command='curl '+flag+word+' --output /fixture/result; rm /fixture/adjacent-target'
                    inp={'command':command,'name':'synthetic-only'}
                    digest=approvals.input_digest(inp);untouched=copy.deepcopy(inp)
                    self.sent.clear();task,ask,card=await self.open('agentj fn run',inp)
                    self.assertEqual(json.loads(card['summary'].split('\n',1)[1])['command'],
                                     'curl '+flag+'<redacted> --output /fixture/result; rm /fixture/adjacent-target')
                    self.assertEqual(card['summary'],card['envelope']['details'])
                    self.assertEqual(card['envelope']['input_sha256'],digest)
                    self.assertEqual(ask.tool_input,untouched);self.assertEqual(inp,untouched)
                    self.assertEqual(card['envelope_sha256'],approval_review.digest(card['envelope']))
                    answer=self.ph.answer(card,True);no_uv=dict(answer);no_uv.pop('fa')
                    await self.h._answer(self.s,no_uv);self.assertFalse(ask.fut.done())
                    await self.h._answer(self.s,answer)
                    self.assertEqual((await task)['updatedInput'],untouched)
                    self.assertEqual(approvals.check_record(self.st,approvals.read_log(self.st)[-1]),'ok')
            for word in DYNAMIC_OR_MISSING:
                for sep in (' ','=') if word!='--verbose' else (' ',):
                    with self.subTest(flagname=flagname,dynamic=word,sep=sep):
                        command='curl '+flagname+sep+word+' --output /fixture/result; rm /fixture/adjacent-target'
                        inp={'command':command};self.sent.clear();task,ask,card=await self.open('Bash',inp)
                        self.assertEqual(json.loads(card['summary'].split('\n',1)[1])['command'],command)
                        self.assertEqual(card['summary'],card['envelope']['details'])
                        self.assertEqual(card['envelope']['input_sha256'],approvals.input_digest(inp))
                        await self.h._answer(self.s,self.ph.answer(card,False))
                        self.assertEqual((await task)['behavior'],'deny')


    async def test_real_host_owner_prepare_never_puts_json_credentials_in_mail(self):
        value='synthetic-privacy-X'
        before=len(self.h.elevate.out.cards)
        for flag in ('--password','--passwd','--passphrase'):
            for raw in (json.dumps({flag:value,'mode':'write'}),
                        flag+'='+value+'&mode=write',flag+': '+value+'\nmode: write'):
                with self.subTest(flag=flag,raw=raw):
                    body,pending=owner_route.prepare(self.h,raw,'mail','en')
                    self.assertNotIn(value,body)
                    self.assertEqual(pending.found,1)
                    self.assertEqual([v for _,v in pending],[value])
                    self.assertEqual(len(self.h.elevate.out.cards),before)
                    self.assertIn('write',body)


if __name__=='__main__':unittest.main()
