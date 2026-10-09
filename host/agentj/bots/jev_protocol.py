"""Key-free TypeSafe decisions contract shared by public bots and the official F18 tenant.

No transport, credentials, state directory, visitor record or cloud queue is owned here.
The official adapter keeps its existing dedicated account/queue and policy questions.
"""
import math
MODEL='~typesafe/jev-latest'
def request(state,questions):
    return {'model':MODEL,'state':state,'questions':questions}
def probability(answers,name):
    value=answers[name]['noul']
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:raise ValueError('invalid_jev_probability')
    return value
