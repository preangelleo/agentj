"""Feedback #515b: retired skill decisions stay absent; ordinary fixed commands remain signed."""
import _hermetic
import json
import os
import pathlib
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import cmdrun, personalize
from test_p361_cmdrun import Base, Done, sign
from test_l1 import Phone

class Decisions(Base):
    def test_exact_bundled_name_only(self):
        for name in ('skill_replace_agentj-config','skill_keep_agentj-config','skill_replace_../../tmp/x','skill_replace_agentj-config;id','skill_replace_agentj-unknown','skill_install_agentj-config','skill_keep_--help'):
            self.assertIsNone(cmdrun.skill_command(name),name)
            with self.assertRaises(KeyError):cmdrun.argv(name)
        self.assertNotIn('skill_replace_agentj-config',cmdrun.RUN_CMDS)

    def test_metadata_is_only_name_and_capability(self):
        rows=[None, {'skill': ['agentj-config'], 'conflict': True}, {'skill': 'agentj-config', 'conflict': 1}, {'skill':'agentj-config','conflict':True,'can_keep':True,'path':'/private/path'},
              {'skill':'agentj-config','conflict':True,'can_keep':False},
              {'skill':'not-bundled','conflict':True,'can_keep':True},
              {'skill':'agentj-update','conflict':False,'can_keep':True}]
        with mock.patch.object(personalize,'status',return_value=rows):
            self.assertEqual(cmdrun.skill_conflicts(),[])

    def test_signed_retired_decisions_refused_and_ordinary_doctor_runs(self):
        for action in ('replace','keep'):
            res=self.send(sign(self.phone,'skill_'+action+'_agentj-config'))
            self.assertEqual((res[-1]['ok'],res[-1]['why']),(False,'shape'))
            self.assertEqual(self.calls,[])
        res=self.send(sign(self.phone,'doctor'))
        self.assertTrue(res[-1]['ok'])
        self.assertEqual(self.calls[-1][0][-2:],['doctor','--read-only'])
        self.assertEqual(res[-1]['skill_conflicts'],[])

    def test_unsigned_forged_unknown_and_replay_are_refused(self):
        other=Phone(self.st,'other')
        name='doctor'
        for obj in (sign(self.phone,name,key=other.sk),{**sign(self.phone,name),'sig':''},sign(self.phone,'skill_replace_agentj-unknown'),sign(self.phone,'skill_keep_agentj-config')):
            res=self.send(obj)
            self.assertFalse(res[-1]['ok'])
        self.assertEqual(self.calls,[])
        obj=sign(self.phone,name);self.assertTrue(self.send(obj)[-1]['ok']);n=len(self.calls)
        self.assertEqual(self.send(obj)[-1]['why'],'replay');self.assertEqual(len(self.calls),n)

    def test_no_registered_approval_key_refused(self):
        self.st.remove_device(self.phone.did)
        res=self.send(sign(self.phone,'doctor'))
        self.assertFalse(res[-1]['ok']);self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
