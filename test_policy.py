"""Focused policy regressions. These are not a combat simulator."""
import copy
import gzip
import json
import math
import sys
import time
from bot import Bot


def run(kit):
    with gzip.open(kit+'/check/stream.jsonl.gz','rt') as f:
        messages=[json.loads(line) for line in f]
    hello=messages[0]
    hello['map']['dir']=kit+'/arena'
    begin=time.perf_counter()
    bot=Bot(hello)
    initialization=(time.perf_counter()-begin)*1000
    observations=[o for o in messages if o['type']=='obs']
    timings=[]
    for obs in observations:
        begin=time.perf_counter()
        action=bot.act(obs)
        timings.append((time.perf_counter()-begin)*1000)
        assert all(math.isfinite(x) for x in action.get('aim',[])+action.get('move',[]))
        assert action['tick']==obs['tick']
    # Slopes must use local terrain rather than fixed start altitude.
    arena=bot.arena
    original_height,original_walkable=arena.height,arena.walkable
    arena.height=lambda x,y:.3*x
    arena.walkable=lambda x,y,z,pad,height: abs(z-.3*x)<1e-9
    assert arena.traversable((0,0,0),1,0,8,2.8,9)
    arena.height,arena.walkable=original_height,original_walkable
    # In the absence of any traversable heading, halt instead of driving into a wall.
    obs=copy.deepcopy(observations[0]);obs['mechs']=[];obs['crates']=[];obs['pickups']=[];obs['cubes']=[]
    obs['self']['pos']=[100,100,arena.height(100,100)]
    obs['zone']['next_center']=[0,0]
    bot.arena.traversable=lambda *args:False
    assert bot.act(obs)['move']==[0.,0.]
    # A low-health retreat persists until repaired, and does not stop at an arbitrary timer.
    obs['self']['hp']=obs['self']['max_hp']*.3
    bot.act(obs)
    obs['t']+=30;obs['self']['hp']=obs['self']['max_hp']*.6
    bot.act(obs);assert bot.recovering
    obs['self']['hp']=obs['self']['max_hp']*.9
    bot.act(obs);assert not bot.recovering
    print(json.dumps({'initialization_ms':initialization,'observations':len(timings),'p50_ms':sorted(timings)[len(timings)//2],'p99_ms':sorted(timings)[int(len(timings)*.99)],'max_ms':max(timings),'regressions':'PASS'},indent=2))


if __name__=='__main__':run(sys.argv[1])
