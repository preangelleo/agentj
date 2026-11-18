"""R-0056 items 1–2 (chairman 11-18 「同主人设备只读诊断封闭清单直接放行；UV 级仍当场批」).

A host-verified paired same-owner device's own forwarded task (provenance.HostDelegated kind same_owner, an ACTIVE
same-owner relation at decision time) runs a closed diagnostic list without a card: journalctl / systemctl status for the
agentj units, `agentj status` / `doctor --read-only --json` / the AGENTJ_READ listings, ls / find with positive grammars,
cat / head / tail of working-folder files and exactly host.log. Every other source keeps closed_read unchanged; anything
outside the grammars is refused (security r0 Claude + Codex must-refuse lists); UV-list actions still take the on-site card.

Real Host + real Host.ask → list_approval → approval_offlist; nothing here executes a listed command (approvals only).
Synthetic state, HOME, device and same-owner relation; PATH pinned to the system bin dirs for the executable check.
"""
import _hermetic  # noqa: F401
import asyncio
import os
import shlex
import shutil
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_offlist, compose, peer_guard, provenance, uv_list
from agentj.same_owner import ForwardSend, SameOwner
import test_heartbeat_readonly_cards_0306 as hb

FID = 'e' * 16
SYSTEM_PATH = '/usr/bin:/bin'


def _system(name):
    path = shutil.which(name, path=SYSTEM_PATH)
    try:
        return bool(path) and os.stat(os.path.realpath(path)).st_uid == 0
    except OSError:
        return False


HAS_JOURNAL = _system('journalctl') and _system('systemctl')


class _Fixture(hb._Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        env = mock.patch.dict(os.environ, {'PATH': SYSTEM_PATH}); env.start(); self.addCleanup(env.stop)
        os.environ.pop('AGENTJ_SERVICE_NAME', None)
        (self.cwd / 'notes.md').write_text('synthetic line\n')
        (self.cwd / 'sub').mkdir()
        (self.cwd / 'sub' / 'a.md').write_text('synthetic\n')
        self.st.log('synthetic', note='host log line')          # host.log exists (host-written, redacted)
        self.log = str(self.st.log_path)
        self.assertTrue(os.path.isfile(self.log))
        # A live same-owner relation (SameOwner.active / row are the host's own reads of same-owner.json).
        self.relation = {'on': True}
        self.so = SameOwner(SimpleNamespace())
        self.so.active = lambda fid: self.relation['on'] and fid == FID
        self.so.row = lambda fid: {'token': 'tok'} if fid == FID else {}
        old = self.h.peers
        self.h.peers = SimpleNamespace(same_owner=self.so, xfer=SimpleNamespace(broker=None))
        self.addCleanup(setattr, self.h, 'peers', old)
        self.h.cur_turn = 1
        # A stand-in agentj program the executable check accepts (owner-uid, not group/other-writable, outside cwd).
        self.bindir = self.root / 'bin'; self.bindir.mkdir()
        exe = self.bindir / 'agentj'; exe.write_text('#!/bin/sh\nexit 0\n'); exe.chmod(0o755)
        os.environ['PATH'] = SYSTEM_PATH + ':' + str(self.bindir)

    # ---- turn sources, built exactly as their injectors build them
    def same_owner(self, **kw):
        return ForwardSend(self.so, FID, 'tok', 1, 'synthetic', **kw)

    def sources(self):
        phone = compose.Send(self.ph.did, 'sid', 1, text='owner', by=None); phone.source = provenance.phone()
        same_host = compose.Send('dev', 'sid', 1, text='x', by=None)
        same_host.source = provenance.SameHost(kind='same_host', role='peer', unit='synthetic.service')
        plain = compose.Send('dev', 'sid', 1, text='x', by=None); plain.source = {'kind': 'same_owner', 'role': 'task'}
        forged = compose.Send('dev', 'sid', 1, text='x', by=None)
        forged.source = type('HostDelegated', (dict,), {})({'kind': 'same_owner', 'role': 'task'})   # look-alike class
        return {'same_owner': (self.same_owner(), 'diag'),
                'friend_task': (self.same_owner(approved=True), 'external'),
                'irt_reply': (self.same_owner(data={'irt': 'm1', 'mid': 'm1'}), 'external'),
                'end_message': (self.same_owner(data={'end': True}), 'external'),
                'same_host': (same_host, 'external'),
                'plain_dict': (plain, 'external'),
                'forged_class': (forged, 'external'),
                'host_read': (self.completion(), 'host_read'),
                'unknown': (self.unknown(), 'unknown'),
                'owner': (phone, 'owner')}

    async def ask(self, send, tool, inp):
        self.h.agent.cur_send = send
        task = asyncio.create_task(self.h.ask(tool, inp, batch=False, _harness_permission=True))
        kind, result = await self.outcome(task)
        return kind, (result or {}).get('behavior') if kind == 'none' else result, (result or {}).get('reason') if kind == 'none' else None

    async def bash(self, send, command):
        return await self.ask(send, 'Bash', {'command': command})


DIAG_SHELL = (
    'tail -n 50 {log}', 'cat {log}', 'head -n 5 {log}', 'wc -l {log}',
    'ls -lt', 'ls -latr sub', 'ls --all',
    'find . -maxdepth 2 -name "*.md" -type f -print', 'find sub -mtime -1 -size +0c', 'find sub -iname A.MD -path "*sub*"',
    'agentj status', 'agentj doctor --read-only --json', 'agentj card list', 'agentj uv list',
)
JOURNAL_SHELL = (
    'journalctl --user -u agentj -n 50 --no-pager',
    'journalctl --user -u agentj.service --since "2026-11-17 10:00" --no-pager -o short-iso',
    'journalctl --user -u agentj -u agentj-rescue --since 2026-11-17 -o short --no-pager -n 500',
    'systemctl --user is-active agentj', 'systemctl --user status agentj --no-pager -n 0',
    'systemctl --user is-active agentj-rescue.service',
)


class Matrix(_Fixture):
    async def test_diag_list_only_for_verified_same_owner(self):
        commands = DIAG_SHELL + (JOURNAL_SHELL if HAS_JOURNAL else ())
        for name, (send, tier) in self.sources().items():
            for template in commands:
                command = template.format(log=shlex.quote(self.log))
                with self.subTest(source=name, command=command):
                    kind, behavior, reason = await self.bash(send, 'cd ' + str(self.cwd) + ' && ' + command)
                    self.assertEqual('none', kind)
                    if tier in ('diag', 'owner'):
                        want = 'allow'
                    else:     # closed_read unchanged: only the host_read agentj listing shapes pass for host_read
                        want = 'allow' if tier == 'host_read' and command in ('agentj card list', 'agentj uv list') else 'deny'
                    self.assertEqual(want, behavior, (name, command, reason))
                    if tier == 'diag':
                        self.assertEqual('off_list_diag', self.last_reason())
            with self.subTest(source=name, tool='Read host.log'):
                kind, behavior, _ = await self.ask(send, 'Read', {'file_path': self.log})
                self.assertEqual(('none', 'allow' if tier in ('diag', 'owner') else 'deny'), (kind, behavior))

    def last_reason(self):
        import json
        rows = [json.loads(line) for line in self.st.log_path.read_text().splitlines() if '"ask_done"' in line]
        return rows[-1].get('reason') if rows else None

    async def test_plain_reads_unchanged_for_same_owner(self):
        # closed_read still answers first (reason off_list_read), and an off-list write is still refused.
        kind, behavior, _ = await self.bash(self.same_owner(), 'cat notes.md')
        self.assertEqual(('none', 'allow', 'off_list_read'), (kind, behavior, self.last_reason()))
        for command in ('touch made.txt', 'git status', 'grep -n x notes.md', 'stat notes.md', 'rg x'):
            with self.subTest(command=command):
                kind, behavior, _ = await self.bash(self.same_owner(), command)
                self.assertEqual(('none', 'deny', 'off_list_write_external'), (kind, behavior, self.last_reason()))

    async def test_relation_revoked_mid_turn(self):
        send = self.same_owner()
        self.h.agent.cur_send = send
        src = approval_offlist.source(self.h)
        self.assertIs(src.get('verified_same_owner'), True)
        command = {'command': 'tail -n 50 ' + self.log}
        self.assertEqual('off_list_diag', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[1])
        self.relation['on'] = False              # revoked / legacy-ended between turn start and the decision
        self.assertEqual(('deny', 'off_list_write_external'),
                         approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[:2])
        kind, behavior, _ = await self.bash(send, 'tail -n 50 ' + self.log)
        self.assertEqual(('none', 'deny'), (kind, behavior))
        self.relation['on'] = True
        self.h.cur_turn = 2                      # the forwarded task is no longer the running turn
        self.assertEqual('deny', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[0])
        self.h.cur_turn = 1
        other = self.same_owner()                # the live turn moved to another Send
        self.h.agent.cur_send = other
        self.assertEqual('deny', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[0])
        with mock.patch.object(self.h, 'stopped', return_value=True):
            self.h.agent.cur_send = send
            self.assertEqual('deny', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[0])

    async def test_wire_or_label_dicts_never_get_the_list(self):
        command = {'command': 'tail -n 50 ' + self.log}
        for src in ({'type': 'same_owner', 'trust': 'external', 'verified_same_owner': True},
                    {'type': 'same_owner', 'trust': 'external', 'verified_same_owner': True, 'same_owner_seat': object()},
                    {'type': 'same_owner', 'trust': 'external', 'verified_same_owner': 'true',
                     'same_owner_seat': approval_offlist._SameOwnerSeat(self.h, self.same_owner())},
                    {'type': 'friend', 'trust': 'external', 'verified_same_owner': True}):
            with self.subTest(src=src):
                self.assertEqual('deny', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[0])
        # A request_source() / shared-child scope carrying a HostDelegated same_owner but no forwarded Send: no seat.
        self.h.agent.cur_send = self.unknown()
        with provenance.request_source(provenance.HostDelegated({'kind': 'same_owner', 'role': 'task'})):
            src = approval_offlist.source(self.h)
        self.assertEqual(('same_owner', None), (src['type'], src.get('verified_same_owner')))
        # A ForwardSend from another SameOwner object (not this host's peer service) is no seat either.
        foreign = ForwardSend(SameOwner(SimpleNamespace()), FID, 'tok', 1, 'synthetic')
        self.h.agent.cur_send = foreign
        self.assertIsNone(approval_offlist.source(self.h).get('verified_same_owner'))
        # Wrong token for the relation generation.
        self.h.agent.cur_send = ForwardSend(self.so, FID, 'old-token', 1, 'synthetic')
        self.assertIsNone(approval_offlist.source(self.h).get('verified_same_owner'))


class MustRefuse(_Fixture):
    async def refused(self, command, reason=None, cwd=None):
        kind, behavior, _ = await self.bash(self.same_owner(), 'cd ' + str(cwd or self.cwd) + ' && ' + command)
        self.assertEqual(('none', 'deny'), (kind, behavior), command)
        if reason:
            self.assertEqual(reason, self.last_reason(), command)

    last_reason = Matrix.last_reason

    @unittest.skipUnless(HAS_JOURNAL, 'needs root-owned journalctl / systemctl')
    async def test_journalctl_grammar(self):
        ok = 'journalctl --user -u agentj --no-pager'
        for extra in ('-f', '--follow', '--file=/x', '--file /x', '-D /outside', '--directory=/outside', '--root /',
                      '--image=/x.img', '-M other', '--machine=other', '-m', '--merge', '--system', '-k', '--dmesg',
                      '-o json', '-o export', '-o verbose', '-o cat', '-o json-pretty', '--output=short',
                      '--output-fields=MESSAGE', '--cursor-file=a', '--vacuum-time=1s', '--vacuum-size=1K', '--rotate',
                      '--flush', '--sync', '--setup-keys', '--update-catalog', '--relinquish-var', '-n 501', '-n 0',
                      '-n -5', '--since yesterday', '--since "-1h"', '-S 2026-11-17', '--lines=5', '-e', '-x', '-r',
                      '--grep=x', '-u agentj -u agentj -u agentj', '--no-pager', '-n \uff15', '-n \u0665'):
            with self.subTest(extra=extra):
                await self.refused(ok + ' ' + extra)
        for command in ('journalctl --user -u "*" --no-pager', 'journalctl --user -u ssh --no-pager',
                        'journalctl --user -u agentj-other --no-pager', 'journalctl --user --no-pager',
                        'journalctl -u agentj --no-pager', 'journalctl --user -u agentj -n 5',
                        'journalctl --use -u agentj --no-pager', 'journalctl --user -u agentj --no-pag',
                        'journalctl --user --unit=agentj --no-pager', 'journalctl --file=/x'):
            with self.subTest(command=command):
                await self.refused(command)

    @unittest.skipUnless(HAS_JOURNAL, 'needs root-owned journalctl / systemctl')
    async def test_systemctl_grammar(self):
        for command in ("systemctl --user status '*'", 'systemctl --user status "agentj*"', 'systemctl --user stop agentj',
                        'systemctl --user start agentj', 'systemctl --user restart agentj', 'systemctl --user status agentj',
                        'systemctl --user status agentj --no-pager', 'systemctl --user status agentj --no-pager -n 5',
                        'systemctl --user status agentj --no-pager -n 0 -H host', 'systemctl -H host --user is-active agentj',
                        'systemctl --user --root=/ is-active agentj', 'systemctl --user is-active --machine=x agentj',
                        'systemctl --user is-active -- -agentj', 'systemctl is-active agentj', 'systemctl --user is-active ssh',
                        'systemctl --user is-active agentj agentj-rescue', 'systemctl --user cat agentj',
                        'systemctl --user show agentj', 'systemctl --user list-units'):
            with self.subTest(command=command):
                await self.refused(command)

    async def test_find_grammar(self):
        outside = self.outside
        for command in ('find . -exec id \\;', 'find . -execdir id \\;', 'find . -delete', 'find . -ok id \\;',
                        'find . -okdir id \\;', 'find -L .', 'find -H .', 'find -P .', 'find . -follow',
                        'find . -files0-from ' + str(outside / 'job.log'), 'find -D tree .', 'find . -newer notes.md',
                        'find . -anewer notes.md', 'find . -fprint out', 'find . -fprintf out x', 'find . -fprint0 out',
                        'find . -fls out', 'find . -ls', 'find . -maxdepth 4', 'find . -maxdepth 10',
                        'find . ( -name x )', 'find . ! -name x', 'find . -name x -o -name y', 'find . -not -name x',
                        'find . -regex x', 'find . -name', 'find . -type x', 'find . -size 1Q', 'find . -mtime soon',
                        'find . -printf %p', 'find . -name -exec', 'find . -maxdepth \uff12', 'find ' + str(outside), 'find ~/other', 'find ..',
                        'find sub ' + str(outside), 'find "*"'):
            with self.subTest(command=command):
                await self.refused(command)
        await self.refused('find . -newer ' + str(self.secret), 'off_list_credential')
        # A start point that holds the home folder or Agent J's state dir lists their names: refused.
        src = self.diag_src()
        for cwd in (self.root, self.home):
            with self.subTest(cwd=cwd):
                self.assertEqual('deny', approval_offlist.decide(self.h, 'Bash', {'command': 'find . -maxdepth 1'}, str(cwd), src)[0])

    def diag_src(self):
        self.h.agent.cur_send = self.same_owner()
        src = approval_offlist.source(self.h)
        self.assertIs(src.get('verified_same_owner'), True)
        return src

    async def test_ls_grammar(self):
        os.symlink(self.st.root, self.cwd / 'state-link')
        os.symlink(self.home / '.ssh', self.cwd / 'ssh-link')
        for command in ('ls -R', 'ls -lR', 'ls -laR sub', 'ls --recursive', 'ls -L', 'ls -lL', 'ls -H',
                        'ls --dereference', 'ls -la state-link', 'ls -la ssh-link', 'ls -L state-link', 'ls ../other-logs',
                        'ls ' + str(self.outside), 'ls -I x', 'ls --hide=x', 'ls -w 5', 'ls *.md', 'ls --color=always'):
            with self.subTest(command=command):
                await self.refused(command)

    async def test_reader_operands(self):
        state = self.st.root
        (state / 'approvals.log').write_text('synthetic\n')
        (state / 'host.log.1').write_text('synthetic\n')
        (state / 'cloud.json').write_text('{}\n')
        await self.refused('tail -n 50 ' + str(state / 'approvals.log'), 'off_list_private_state')
        await self.refused('tail ' + str(state / 'host.log.1'), 'off_list_private_state')
        await self.refused('cat ' + self.log + '/../cloud.json', 'off_list_private_state')
        await self.refused('cat ' + str(state / 'devices.json'))
        (self.root / '.env').write_text('SYNTHETIC=1\n')
        await self.refused('cat ../.env', 'off_list_credential')
        for command in ('tail -f ' + self.log, 'tail -F ' + self.log, 'tail --follow=name ' + self.log,
                        'tail -n 5 -f ' + self.log, 'cat ' + str(state) + '/*.log', 'cat *.md',
                        'cat notes.md | head -5', 'cat notes.md 2>/dev/null', 'cat notes.md > copy.md',
                        'cat $(echo notes.md)', 'cat `echo notes.md`', 'cat ' + str(self.outside / 'job.log'),
                        'wc --files0-from=notes.md', 'head -c 5 ' + self.log + ' ' + str(self.outside / 'job.log')):
            with self.subTest(command=command):
                await self.refused(command)

    async def test_host_log_links_and_ancestors(self):
        os.symlink(self.log, self.cwd / 'link.log')
        await self.refused('cat link.log')
        alias = self.root / 'state-alias'
        os.symlink(self.st.root, alias)
        await self.refused('tail -n 5 ' + str(alias / 'host.log'))
        kind, behavior, _ = await self.ask(self.same_owner(), 'Read', {'file_path': str(alias / 'host.log')})
        self.assertEqual(('none', 'deny'), (kind, behavior))
        os.link(self.log, self.cwd / 'twin.log')         # host.log now has two links: neither name passes
        await self.refused('cat twin.log')
        await self.refused('tail -n 5 ' + self.log)

    async def test_host_log_swapped_or_ancestor_replaced(self):
        src = self.diag_src()
        command = {'command': 'tail -n 5 ' + self.log}
        self.assertEqual('allow', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[0])
        os.replace(self.log, self.log + '.moved')
        os.symlink(self.outside / 'job.log', self.log)    # host.log replaced by a symlink after an earlier check
        self.assertEqual('deny', approval_offlist.decide(self.h, 'Bash', command, str(self.cwd), src)[0])

    async def test_executable_resolution(self):
        fake = self.cwd / 'journalctl'; fake.write_text('#!/bin/sh\n'); fake.chmod(0o755)
        for path in (str(self.cwd) + ':' + SYSTEM_PATH, '.:' + SYSTEM_PATH, ':' + SYSTEM_PATH, 'bin:' + SYSTEM_PATH):
            with self.subTest(path=path), mock.patch.dict(os.environ, {'PATH': path}):
                await self.refused('journalctl --user -u agentj --no-pager')
        user_bin = self.root / 'ubin'; user_bin.mkdir()
        for name in ('journalctl', 'ls', 'cat'):
            exe = user_bin / name; exe.write_text('#!/bin/sh\n'); exe.chmod(0o755)
        with mock.patch.dict(os.environ, {'PATH': str(user_bin) + ':' + SYSTEM_PATH}):
            for command in ('journalctl --user -u agentj --no-pager', 'ls -lt', 'cat ' + self.log):
                with self.subTest(command=command):        # an owner-writable look-alike of a system tool
                    await self.refused(command)
        (self.bindir / 'agentj').chmod(0o777)
        await self.refused('agentj status')
        (self.bindir / 'agentj').chmod(0o755)
        for command in ('./journalctl --user -u agentj --no-pager', 'PATH=. journalctl --user -u agentj --no-pager',
                        'env PATH=. journalctl --user -u agentj --no-pager', 'SYSTEMD_PAGER=sh journalctl --user -u agentj --no-pager',
                        '/usr/bin/journalctl --user -u agentj --no-pager', 'command journalctl --user -u agentj --no-pager',
                        'sudo journalctl --user -u agentj --no-pager', 'timeout 5 journalctl --user -u agentj --no-pager',
                        'bash -c "journalctl --user -u agentj --no-pager"'):
            with self.subTest(command=command):
                await self.refused(command)

    async def test_agentj_shapes(self):
        for command in ('agentj doctor --json', 'agentj doctor', 'agentj doctor --reconnect', 'agentj doctor --offline --json',
                        'agentj doctor --read-only --json --reconnect', 'agentj doctor --json --read-only --post-upgrade',
                        'agentj status --json', 'agentj status x', 'agentj card todo add --text x', 'agentj friends list',
                        'agentj --instance 2 status', 'agentj service status', 'agentj busy run -- ls'):
            with self.subTest(command=command):
                await self.refused(command)

    async def test_uv_list_still_takes_the_on_site_card(self):
        target = str(self.st.root / 'devices.json')
        self.assertTrue(uv_list.required('Write', {'file_path': target, 'content': '{}'}, cwd=str(self.cwd),
                                         env=uv_list.host_environment(self.h)))
        kind, uv, _ = await self.ask(self.same_owner(), 'Write', {'file_path': target, 'content': '{}'})
        self.assertEqual(('card', True), (kind, uv))
        # `agentj revoke` is UV-gated inside the ctl op itself (uv_required); as a Bash request from the same-owner
        # turn it is not on the diagnostic list, so it is refused here before it could reach that op.
        command = {'command': 'agentj revoke synthetic-device'}
        self.assertFalse(uv_list.required('Bash', command, cwd=str(self.cwd), env=uv_list.host_environment(self.h)))
        kind, behavior, _ = await self.ask(self.same_owner(), 'Bash', command)
        self.assertEqual(('none', 'deny'), (kind, behavior))

    async def test_shell_expansion_is_refused(self):
        # security r1 F1 (Claude): danger.lex leaves these unexpanded; the harness shell turns them into find actions.
        log = self.log
        for command in ('find . -name {x,-delete}', 'find . -name {"*",-fprint,/tmp/o}', r'find . -name {x,-exec,id,\;}',
                        'find sub -path {x,-delete}', 'find . -iname {a,-fls,out}', 'find . -name x${IFS}-delete',
                        'find . -name x$IFS-delete', 'find . -name *', 'find . -name "x"{,} -print', r'find . -name x\ -delete',
                        'find . -name "$(id)"', 'find . -name "`id`"', 'find . -name "x', 'find . -name ~',
                        'cat notes.md${IFS}/etc/hostname', 'tail -n 5 ' + log + '{,.1}', 'tail -n 5 "' + log + '"{,.1}',
                        'journalctl --user -u agentj{,-x} --no-pager', 'journalctl --user -u "$U" --no-pager',
                        'systemctl --user is-active agentj{,}', 'agentj status{,}', 'ls -lt ~', 'ls -lt s*', 'ls "s*"',
                        'cat "notes.md"*', 'head -n 5 [n]otes.md', 'cat no?es.md'):
            with self.subTest(command=command):
                await self.refused(command)
        # The planted-file case: with files `a` and `-delete` here, an unquoted `*` would become `-name a -delete`.
        (self.cwd / 'a').write_text('x'); (self.cwd / '-delete').write_text('x')
        await self.refused('find . -name *')
        for command in ('find . -maxdepth 2 -name "*.md" -type f -print', "find . -name '*.md'", 'find sub -path "*sub*"'):
            with self.subTest(allowed=command):
                kind, behavior, _ = await self.bash(self.same_owner(), command)
                self.assertEqual(('none', 'allow', 'off_list_diag'), (kind, behavior, self.last_reason()))

    async def test_r2_comment_quote_desync_and_one_line_rule(self):
        # security r2 F1 (Claude + Codex): a quote inside a `#` comment hid the next line's expansions from the scan.
        for command in ("ls -lt # '\nfind . -name {x,-delete} # '", 'ls -lt # "\nfind . -name {x,-delete} # "',
                        "ls -lt # '\nfind . -name *  # '", "ls -lt # '\nfind . -name x${IFS}-delete # '",
                        "ls -lt # '\nfind . -name {x,-fprint,/tmp/o} # '", "ls -lt # '\nfind . -name {x,-exec,id,\;} # '",
                        "pwd # '\nfind . -name {x,-delete}\npwd # '",
                        "ls -lt # '\nfind . -name {x,-exec,stripe,charges,create,--amount=100,\;} # '",
                        "find . -name x # '\nfind . -name {x,-exec,stripe,charges,create,--amount,100,--currency,usd,\;}\n# '",
                        'find . -name "*.md" # note',          # a trailing comment is refused too (no `#` at all)
                        'ls -lt\nls -la', 'ls -lt\r', 'ls\t-lt', 'ls -lt;', 'ls -lt &', 'ls -lt | cat', 'find . -name x(ls)',
                        'find . -name x!', 'find . -name =ls', 'ls =ls', 'ls "a""b"x', "ls ''", 'find . -name "x;-delete"', 'find . -name "x&-delete"',
                        'cd sub && ls -lt', 'cd ' + str(self.cwd) + ' && cd sub && ls -lt',
                        'cd "' + str(self.cwd) + '" && ls -lt', "cd '" + str(self.cwd) + "' && ls -lt",
                        'cd ' + str(self.cwd) + '  && ls -lt', 'find . -name “x”', 'find . -name "x', "ls 'a\"b'"):
            with self.subTest(command=command):
                kind, behavior, _ = await self.bash(self.same_owner(), command)
                self.assertEqual(('none', 'deny'), (kind, behavior), command)
        for command in ('find . -maxdepth 2 -name "*.md" -type f -print', 'ls -lt', 'tail -n 5 ' + self.log,
                        'cd ' + str(self.cwd) + ' && ls -lt'):
            with self.subTest(allowed=command):
                kind, behavior, _ = await self.bash(self.same_owner(), command)
                self.assertEqual(('none', 'allow'), (kind, behavior))

    async def test_symlink_dotdot_cd_is_refused_for_same_owner_and_friends(self):
        # security r3 F1: `<root>/outside/lnk -> <cwd>/sub`; realpath(lnk/..) is the folder, bash's logical cd is outside.
        out = self.root / 'outside'; out.mkdir()
        (out / 'notes.md').write_text('OUTSIDE-NOT-IN-SCOPE\n'); os.symlink(self.cwd / 'sub', out / 'lnk')
        friend = ForwardSend(mock.Mock(), 'f' * 16, 'tok', 1, 'synthetic', approved=True)
        lnk = str(out / 'lnk')
        for send in (self.same_owner(), friend):
            for command in ('cd ' + lnk + '/.. && cat notes.md', 'cd ' + lnk + '/.. && ls -la',
                            'cd ' + lnk + '/.. && find . -maxdepth 1 -name "*" -print', 'cd ' + lnk + ' && cd .. && ls -la',
                            'cd ' + lnk + ' && ls -la'):
                with self.subTest(source=type(send).__name__, command=command):
                    kind, behavior, _ = await self.bash(send, command)
                    self.assertEqual(('none', 'deny'), (kind, behavior))
        # closed_diag also refuses a typed dir that is not already its own normal path (closed_read keeps `/./` and `//`,
        # which land in the same physical folder, for its own shapes).
        for command in ('cd ' + str(self.cwd) + '/./sub && ls -lt', 'cd ' + str(self.cwd) + '//sub && ls -lt',
                        'cd ' + str(self.cwd) + '/sub/.. && ls -lt'):
            with self.subTest(diag=command):
                kind, behavior, _ = await self.bash(self.same_owner(), command)
                self.assertEqual(('none', 'deny'), (kind, behavior))
        for send in (self.same_owner(), friend):          # the plain forms still work
            kind, behavior, _ = await self.bash(send, 'cd ' + str(self.cwd / 'sub') + ' && ls -la')
            self.assertEqual(('none', 'allow'), (kind, behavior))

    async def test_closed_read_is_not_open_to_the_same_expansion(self):
        # Pre-existing closed_read (friends / external / unknown): the same tricks are refused by its operand rules.
        friend = ForwardSend(mock.Mock(), 'f' * 16, 'tok', 1, 'synthetic', approved=True)
        for send in (friend, self.unknown()):
            for command in ('cat {notes.md,/etc/hostname}', 'ls ${HOME}', 'ls $HOME', 'cat notes.md${IFS}/etc/hostname',
                            'cat *', 'cat no?es.md', 'ls -{l,R}', 'cd {sub,/} && ls', 'head -n 5 notes.md{,}', 'ls ~',
                            'wc -l [n]otes.md', 'cat "notes.md"*'):
                with self.subTest(source=type(send).__name__, command=command):
                    kind, behavior, _ = await self.bash(send, command)
                    self.assertEqual(('none', 'deny'), (kind, behavior))

    async def test_seat_rechecked_after_the_audit_await(self):
        # security r1 F2 (Codex): the relation / turn / stop state changing while the audit row is written → refused.
        real = approval_offlist._record
        def revoke(): self.relation['on'] = False
        def move(): self.h.agent.cur_send = self.unknown()
        def other_turn(): self.h.cur_turn = 7
        estop = mock.patch.object(self.h, 'stopped', return_value=True)
        for name, change in (('revoke', revoke), ('turn', move), ('cur_turn', other_turn), ('estop', estop.start)):
            with self.subTest(change=name):
                def record(*a, **kw):
                    out = real(*a, **kw)
                    change()
                    return out
                with mock.patch.object(approval_offlist, '_record', record):
                    kind, behavior, _ = await self.bash(self.same_owner(), 'journalctl --user -u agentj --no-pager'
                                                         if HAS_JOURNAL else 'tail -n 5 ' + self.log)
                self.assertEqual(('none', 'deny'), (kind, behavior))
                if name == 'estop':
                    estop.stop()
                self.relation['on'], self.h.cur_turn = True, 1
        kind, behavior, _ = await self.bash(self.same_owner(), 'tail -n 5 ' + self.log)     # and back to normal
        self.assertEqual(('none', 'allow'), (kind, behavior))

    async def test_same_owner_money_and_irreversible_never_run(self):
        # A charge / IRREVERSIBLE call from the same-owner device: not on the diagnostic list → refused, no card,
        # never `fingerprint_headless` (same_owner is not a FINGERPRINT_SOURCES owner type).
        for command in ('stripe charges create --amount 100 --currency usd', 'git push --force origin main'):
            with self.subTest(command=command):
                kind, behavior, _ = await self.bash(self.same_owner(), command)
                self.assertEqual(('none', 'deny'), (kind, behavior))
                self.assertNotEqual('fingerprint_headless', self.last_reason())

    def test_peer_reply_still_passes_the_outbound_gate(self):
        # Diagnostic output reaches the other computer only through PeerService.tell → peer_guard.outbound (unchanged).
        leaked = 'journal: token ghp_' + ''.join(chr(65 + (i * 7) % 26) for i in range(36))   # built, not a literal
        self.assertFalse(peer_guard.outbound(leaked, None).ok)
        self.assertTrue(peer_guard.outbound('agentj.service: active (running)', None).ok)


class BashDifferential(_Fixture):
    """security r2 item 2: for every string closed_diag ALLOWS, real bash (PATH = argv-printing stubs, nothing else)
    must run exactly one program with exactly the words the approval judged. Thousands of mutations of the allowed
    shapes with every special character, comments, newlines, quotes, braces, $IFS and globs, over planted files."""
    PROGRAMS = ('find', 'ls', 'cat', 'tail', 'head', 'nl', 'wc', 'journalctl', 'systemctl', 'agentj')
    POOL = ('{', '}', '{x,-delete}', '{,}', '$IFS', '${IFS}', '$(id)', '`id`', '\\', '\\ ', '\n', '\r', '\t', '#', '# ',
            "# '", '# "', "'", '"', "''", '""', '*', '?', '[a]', ']', '~', '!', ';', '&', '&&', '|', '<', '>', '(', ')',
            ' -delete', '-delete', ' -exec id ;', '\x00', '$', "$'x'", '..', '=', ' ', '  ', 'cd /tmp && ', ' && ls',
            '"*"', "'*'", '"-delete"', ',', '%', '@', ':', '+', '"x y"', "'x'\"y\"", '“', ' ', '-')

    def stubs(self):
        d = self.root / 'stubs'; d.mkdir()
        for name in self.PROGRAMS:
            exe = d / name
            exe.write_text('#!/bin/sh\nprintf \'%s\\0\' "$(pwd -P)" "${0##*/}" "$@"\n'); exe.chmod(0o755)
        return str(d)

    def seeds(self):
        log = self.log
        return ['find . -maxdepth 2 -name "*.md" -type f -print', "find sub -iname 'A*' -mtime -1", 'find . -name x -print',
                'ls -lt', 'ls -latr sub', 'ls --all', 'cat notes.md', 'tail -n 50 ' + log, 'head -n 5 ' + log,
                'wc -l notes.md', 'journalctl --user -u agentj -n 50 --no-pager',
                'journalctl --user -u agentj --since "2026-11-17 10:00" --no-pager -o short-iso',
                'systemctl --user is-active agentj', 'systemctl --user status agentj --no-pager -n 0',
                'agentj status', 'agentj doctor --read-only --json', 'agentj card list',
                'cd ' + str(self.cwd) + ' && ls -lt', 'cd ' + str(self.cwd) + ' && find . -name "*.md"',
                'cd ' + str(self.cwd / 'sub') + ' && ls -la']

    def cd_attacks(self):
        # security r3 F1: a symlink component followed by `..`, `/./`, `//` — judged by realpath, moved by typed path.
        out = self.root / 'outside'
        if not out.exists():
            out.mkdir(); (out / 'notes.md').write_text('OUTSIDE-NOT-IN-SCOPE\n'); os.symlink(self.cwd / 'sub', out / 'lnk')
        lnk = str(out / 'lnk')
        rows = ['cd ' + lnk + '/.. && ' + c for c in ('cat notes.md', 'ls -la', 'ls -lt', 'find . -maxdepth 1 -name "*" -print')]
        rows += ['cd ' + str(self.cwd) + '/./sub && ls -lt', 'cd ' + str(self.cwd) + '//sub && ls -lt',
                 'cd /' + str(self.cwd) + ' && ls -lt', 'cd ' + str(self.cwd) + '/sub/.. && ls -lt', 'cd ' + lnk + ' && ls -lt']
        return rows

    def requote(self, rnd, text):
        # Balanced quoting of whole or part words: shapes the grammar accepts, so bash agreement is really tested.
        parts = text.split(' ')
        for _ in range(rnd.randint(1, 3)):
            i = rnd.randrange(len(parts)); w = parts[i]
            if not w or any(c in w for c in '\'"'):
                continue
            q = rnd.choice(('"', "'"))
            a = rnd.randint(0, len(w) - 1); b = rnd.randint(a + 1, len(w))
            parts[i] = w[:a] + q + w[a:b] + q + w[b:]
        return ' '.join(parts)

    def mutate(self, rnd, text):
        if rnd.random() < .35:
            text = self.requote(rnd, text)
            if rnd.random() < .5:
                return text
        for _ in range(rnd.randint(1, 3)):
            token, op = rnd.choice(self.POOL), rnd.random()
            if op < .4:
                i = rnd.randint(0, len(text)); text = text[:i] + token + text[i:]
            elif op < .7 and ' ' in text:
                spaces = [i for i, c in enumerate(text) if c == ' ']
                i = rnd.choice(spaces); text = text[:i] + token + text[i + 1:]
            elif op < .85:
                text = text + rnd.choice(('', ' ')) + token
            else:
                parts = text.split(' '); i = rnd.randrange(len(parts)); parts[i] = rnd.choice(('"', "'")) + parts[i] + rnd.choice(('"', "'", ''))
                text = ' '.join(parts)
        return text

    def bash_argv(self, stubs, text):
        import subprocess
        out = subprocess.run([shutil.which('bash', path=SYSTEM_PATH), '--norc', '--noprofile', '-c', text], cwd=str(self.cwd), capture_output=True, timeout=10,
                             env={'PATH': stubs, 'HOME': str(self.home), 'LC_ALL': 'C'})
        return out.stdout.decode('utf-8', 'surrogateescape')

    async def test_allowed_strings_run_exactly_the_judged_words(self):
        import random
        for name in ('-delete', 'a', 'x', '-exec', '-fprint', 'id'):
            (self.cwd / name).write_text('planted\n')
        stubs = self.stubs()
        rnd = random.Random(20261118)
        attacks = [
            "ls -lt # '\nfind . -name {x,-delete} # '", 'ls -lt # "\nfind . -name {x,-delete} # "',
            "ls -lt # '\nfind . -name *  # '", "ls -lt # '\nfind . -name x${IFS}-delete # '",
            "ls -lt # '\nfind . -name {x,-fprint,/tmp/o} # '", "ls -lt # '\nfind . -name {x,-exec,id,\;} # '",
            "pwd # '\nfind . -name {x,-delete}\npwd # '",
            "find . -name x # '\nfind . -name {x,-exec,stripe,charges,create,--amount,100,--currency,usd,\;}\n# '",
            'find . -name {x,-delete}', 'find . -name {"*",-fprint,/tmp/o}', 'find . -name {x,-exec,id,\;}',
            'find sub -path {x,-delete}', 'find . -iname {a,-fls,out}', 'find . -name x${IFS}-delete',
            'find . -name x$IFS-delete', 'find . -name *', 'find . -name "x"{,} -print', 'cat notes.md${IFS}/etc/hostname',
            'tail -n 5 ' + self.log + '{,.1}']
        attacks += self.cd_attacks()
        seeds = self.seeds()
        pool = seeds + self.cd_attacks()
        candidates = list(attacks) + list(seeds) + [self.mutate(rnd, rnd.choice(pool)) for _ in range(9000)]
        allowed = mismatches = 0
        for text in candidates:
            if not approval_offlist.closed_diag(self.h, 'Bash', {'command': text}, str(self.cwd)):
                continue
            allowed += 1
            here, _, judged = approval_offlist._diag_command(text, str(self.cwd))
            ran = self.bash_argv(stubs, text)
            # the directory bash really ran in (pwd -P) must be the one the approval judged, then the same words
            if ran != '\0'.join([os.path.realpath(here)] + judged) + '\0':
                mismatches += 1
                self.fail('bash ran %r for %r, approval judged %r' % (ran.split('\0'), text, judged))
        for text in attacks:
            self.assertFalse(approval_offlist.closed_diag(self.h, 'Bash', {'command': text}, str(self.cwd)), text)
        for text in seeds:
            self.assertTrue(approval_offlist.closed_diag(self.h, 'Bash', {'command': text}, str(self.cwd)), text)
        self.assertGreater(allowed, 1000, 'the fuzz must exercise the allowed side')
        if os.path.islink('/bin'):        # r3: cwd=/usr, `cd /bin/..` is judged /usr but bash lands in /
            for check in (lambda t: approval_offlist.closed_diag(self.h, 'Bash', {'command': t}, '/usr'),
                          lambda t: approval_offlist.closed_read(self.h, 'Bash', {'command': t}, '/usr', 'external')):
                self.assertFalse(check('cd /bin/.. && ls -lt'))
                self.assertFalse(check('cd /bin/.. && ls -la'))
        print('\nR-0056 bash differential: %d strings, %d allowed, %d mismatches' % (len(candidates), allowed, mismatches))


if __name__ == '__main__':
    unittest.main()
