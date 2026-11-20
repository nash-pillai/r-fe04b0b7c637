"""Map-aware skirmisher with independent movement and weapon-specific fire control."""
import math
import random
import mechfight as mf
from arena import Arena


def angle(a): return (a+math.pi)%(2*math.pi)-math.pi

def unit(x,y):
    d=math.hypot(x,y)
    return (x/d,y/d) if d>1e-8 else (0.,0.)


def projected_zone(zone, now, seconds):
    start = max(now, zone["shrink_start"])
    end = zone["shrink_end"]
    fraction = min(1.0, max(0.0, (now + seconds - start) / (end - start))) if end > start else 1.0
    center = tuple(a + fraction * (b - a) for a, b in zip(zone["center"], zone["next_center"]))
    radius = zone["radius"] + fraction * (zone["next_radius"] - zone["radius"])
    return center, radius


class Bot:
    name='Martin Brooke / Skirmisher'

    def __init__(self,hello):
        self.config=hello['config']
        self.rng=random.Random(hello['seed'])
        self.phase=self.rng.random()*6.28
        self.arena=Arena(hello['map']['dir'])
        self.previous=None
        self.stuck=0
        self.goal=None
        self.last_goal=-100
        self.recovering=False
        self.direction=1 if hello['slot']%2 else -1
        self.focus_id = None
        self.pursuit = None

    def aim_at(self,me,target,weapon):
        kind=weapon['kind'];s=weapon['stats'];p=me['pos']
        muzzle=me['stats'].get('muzzle_height',7)
        if kind=='mortar': muzzle=me['stats'].get('height',9)+.5
        q=list(target['pos']);v=target.get('vel',[0,0,0])
        scale=1+self.config['cubes'].get('size_per_cube',.05)*target.get('cubes',0)
        height=self.config['npc']['height'] if target.get('kind')=='npc' else self.config['crates']['height'] if target.get('kind')=='crate' else self.config['mech']['height']*scale
        q[2]+=height*.52
        speed=s.get('speed',1e9)
        t=0
        if kind in ('gun','mortar'):
            rx,ry,rz=q[0]-p[0],q[1]-p[1],q[2]-p[2]-muzzle
            a=sum(w*w for w in v)-speed*speed;b=2*(rx*v[0]+ry*v[1]+rz*v[2]);c=rx*rx+ry*ry+rz*rz
            disc=b*b-4*a*c
            if disc>=0 and abs(a)>1e-9:
                roots=[u for u in ((-b+math.sqrt(disc))/(2*a),(-b-math.sqrt(disc))/(2*a)) if u>0]
                if roots:t=min(roots)
            t=min(t,3)
            for i in range(3):q[i]+=v[i]*t
        dx,dy=q[0]-p[0],q[1]-p[1];d=math.hypot(dx,dy)
        yaw=math.atan2(dy,dx);dz=q[2]-p[2]-muzzle
        if kind=='mortar':
            g=s.get('gravity',22)
            # Fixed-point interception includes the shell's actual ballistic flight time.
            for _ in range(5):
                disc=speed**4-g*(g*d*d+2*dz*speed*speed)
                if disc<0:return None
                pitch=math.atan2(speed*speed-math.sqrt(disc),g*max(d,.01))
                flight=d/max(1,speed*math.cos(pitch))
                q=[target['pos'][i]+v[i]*flight for i in range(3)]
                q[2]+=height*.52
                dx,dy=q[0]-p[0],q[1]-p[1];d=math.hypot(dx,dy);dz=q[2]-p[2]-muzzle
            yaw=math.atan2(dy,dx)
        else:pitch=math.atan2(dz,max(d,.01))
        source=(p[0],p[1],p[2]+muzzle)
        if kind=='mortar':
            clear=True
            steps=max(2,math.ceil(flight/.08))
            for n in range(1,steps):
                tt=flight*n/steps
                xx=source[0]+speed*math.cos(pitch)*math.cos(yaw)*tt
                yy=source[1]+speed*math.cos(pitch)*math.sin(yaw)*tt
                zz=source[2]+speed*math.sin(pitch)*tt-.5*g*tt*tt
                if self.arena.solid(xx,yy,zz) or self.arena.height(xx,yy)>zz:
                    clear=False;break
        else:clear=self.arena.clear(source,q)
        return yaw,pitch,d,clear

    def shot_option(self, me, target, primary):
        if not primary:
            return None
        sol = self.aim_at(me, target, primary)
        if not sol or not sol[3] or sol[2] > primary['stats'].get('range', 230):
            return None
        if primary['kind'] == 'mortar' and sol[2] < 25:
            return None
        hp = target.get('hp', self.config['mech']['hp'])
        if self.recovering and (target['kind'] != 'mech' or sol[2] > 30 or hp > 200):
            return None
        score = sol[2] + .05 * hp
        if target['kind'] != 'mech':
            score += 80
        elif target['id'] == self.focus_id:
            score -= 20
        return score, target, sol

    def assess_opponents(self, o, enemies, primary):
        me = o['self']
        x, y, z = me['pos']
        stats = me['stats']
        rules = self.config['weapons']
        vision = self.config['vision']
        near = vision['near_range'] + stats.get('near_range_bonus', 0)
        seen_range = vision['range'] + stats.get('vision_bonus', 0)
        center = (x, y, z + .5 * stats['height'])
        attackers = {e.get('by') for e in o['events'] if e['type'] == 'damaged'}
        threats, shots = [], []
        for enemy in enemies:
            ex, ey, ez = enemy['pos']
            distance = math.hypot(ex - x, ey - y)
            bearing = math.atan2(ey - y, ex - x)
            scale = 1 + self.config['cubes']['size_per_cube'] * enemy.get('cubes', 0)
            facing = abs(angle(enemy.get('aim', (bearing + math.pi,))[0] - bearing - math.pi))
            if distance < 1.5 * vision['near_range'] or facing < .6 or enemy['id'] in attackers:
                reach = 0
                for kind in enemy.get('weapons', ('gun',)):
                    weapon = rules[kind]
                    reach = max(reach, weapon['speed']**2 / weapon['gravity'] if kind == 'mortar' else weapon['range'])
                muzzle = (ex, ey, ez + self.config['mech']['muzzle_height'] * scale)
                if math.dist(muzzle, center) <= reach and self.arena.clear(muzzle, center):
                    threats.append(enemy)
            # The server can list a mech behind a wall or outside our sensors for five seconds.
            if distance > near and (distance > seen_range or
                    abs(angle(bearing - me['aim'][0])) > math.radians(vision['fov_deg']) / 2):
                continue
            option = self.shot_option(me, enemy, primary)
            if not option:
                continue
            # A clear lead or lob can pass a corner while the tracked body is still hidden.
            if enemy not in threats and (any(enemy.get('vel', ())) or primary['kind'] == 'mortar'):
                eye = (x, y, z + stats['eye_height'])
                body = (ex, ey, ez + .52 * self.config['mech']['height'] * scale)
                if not self.arena.clear(eye, body):
                    continue
            shots.append(option)
        threat_ids = {e['id'] for e in threats}
        shot = min(shots, key=lambda s: (s[1]['id'] not in threat_ids, s[0], s[1]['id']), default=None)
        return threats, shot

    def act(self,o):
        me=o['self'];p=me['pos'];x,y,z=p;t=o['t'];zone=o['zone'];a={'tick':o['tick']}
        hp=me['hp']/me['max_hp'];energy=me['energy'];stats=me['stats'];radius=stats.get('radius',2.5)
        if hp<.42:self.recovering=True
        if hp>.86:self.recovering=False
        recovering=self.recovering
        if o.get('offer'):
            ranks={'nanites':95 if hp<.8 else 75,'servos':92,'armor':88,'plating':84,'gun_mag':81,'gun_damage':80,'gun_rate':79,'laser_damage':83,'laser_charge':82,'reactor':70,'gun_velocity':68,'jets':65,'lightning_efficiency':75,'lightning_range':78,'capacitor':58,'sensors':45,'gyros':40}
            a['pick']=max(range(len(o['offer']['choices'])),key=lambda i:ranks.get(o['offer']['choices'][i]['id'],50))
        enemies=[e for e in o['mechs'] if e['kind']=='mech']
        nearest=min((math.dist(e['pos'][:2],p[:2]) for e in enemies),default=999)
        held=[w['kind'] for w in me['weapons'] if w]
        weapons=me['weapons'];primary=next((w for w in weapons if w and w['kind']=='laser' and w['cooldown']<=.1),None) or next((w for w in weapons if w and w['kind']=='gun'),None)
        gun_for_mortar=next((w for w in weapons if w and w['kind']=='gun'),None)
        mortar=next((w for w in weapons if w and w['kind']=='mortar' and w['cooldown']<=.05),None)
        if mortar and gun_for_mortar and (gun_for_mortar.get('reload',0)>0 or gun_for_mortar.get('ammo',0)==0):primary=mortar
        threats, shot = self.assess_opponents(o, enemies, primary)
        nearest_threat = min(threats, key=lambda e: math.dist(e['pos'], p), default=None)
        target = shot[1] if shot else None
        threat = target if target is not None and (not threats or target in threats) else nearest_threat
        value={'gun':0,'mortar':1,'lightning':2,'laser':3}
        pickups=[q for q in o['pickups'] if q['kind'] not in held]
        for q in sorted(pickups,key=lambda q:-value[q['kind']]):
            if math.dist(q['pos'][:2],p[:2])<self.config['loot']['pickup_radius'] and abs(q['pos'][2]-z)<4:
                if None in me['weapons']:a['take']=q['id'];break
                other=next((i for i,w in enumerate(me['weapons']) if w['kind']!='gun'),None)
                if other is not None and value[q['kind']]>value[me['weapons'][other]['kind']] and not me['weapons'][other].get('charge',0):
                    a.update(take=q['id'],drop=other);break
        # Reserve time to reach the upcoming circle while still using its safe interior.
        center = zone['next_center']
        next_radius = zone['next_radius']
        safe_r = next_radius - min(12, next_radius * .3)
        dnext=math.dist(p[:2],center)
        travel = max(0, dnext - safe_r) / max(1, .8 * stats['walk_speed'])
        safe_center, safe_radius = projected_zone(zone, t, 5)
        safe_margin = min(8, safe_radius * .3)
        urgent = (
            not me['in_zone']
            or math.dist(p[:2], safe_center) > safe_radius - safe_margin
            or (travel > 0 and t + travel + 8 >= zone['shrink_end'])
        )
        goal=center
        arrival=min(4., zone['radius']*.5)
        priorities=[]
        def candidate(q,reward,arrival_radius=4.):
            d=math.dist(p[:2],q['pos'][:2])
            if abs(q['pos'][2]-z)>8:return
            if math.dist(q['pos'][:2], safe_center) > safe_radius - safe_margin:return
            danger=sum(max(0,95-math.dist(q['pos'][:2],e['pos'][:2])) for e in enemies)
            priorities.append((d-reward+danger*(1.2 if recovering else .45),q['pos'],arrival_radius))
        if not urgent:
            for q in o['cubes']:candidate(q,70,self.config['cubes']['pickup_radius']*.95)
            for q in pickups:
                if None in me['weapons'] or (q['kind']=='laser' and 'laser' not in held):candidate(q,55,self.config['loot']['pickup_radius']*.95)
            if not recovering:
                for q in o['crates']:candidate(q,15)
            for q in o.get('drops',[]):
                if q['lands_in']<18 and (q['holder'] in (None,me['id'])):candidate(q,90 if hp<.65 else 50)
            if priorities:_,goal,arrival=min(priorities)
            elif dnext<safe_r*.65:goal=p[:2]
        # Only remember the last open, favorable approach. A tracked position behind
        # cover, a kill, or a second firing lane must never extend the chase.
        if (urgent or recovering or threats or target or priorities or
                any(e['type'] == 'kill' and e['target'] == self.focus_id for e in o['events'])):
            self.pursuit = None
        if self.pursuit and t >= self.pursuit[1]:
            self.pursuit = None
        if target:
            self.focus_id = target['id']
        elif not self.pursuit:
            self.focus_id = None
        combat_goal = None
        crossfire = len(threats) >= 2
        if crossfire and not urgent:
            # Maximize separation from the worst bearing; opposite shooters favor a lateral exit.
            bearings = [unit(x-e['pos'][0], y-e['pos'][1]) for e in threats]
            headings = [(math.cos(i*math.pi/8), math.sin(i*math.pi/8)) for i in range(16)]
            ux, uy = max(headings, key=lambda h: min(h[0]*u + h[1]*v for u,v in bearings))
            combat_goal = (x + 35*ux, y + 35*uy)
        elif threat and not urgent:
            ex,ey=threat['pos'][:2];ux,uy=unit(x-ex,y-ey)
            distance = math.hypot(x-ex, y-ey)
            reach = primary['stats'].get('range', 230) if primary else self.config['weapons']['gun']['range']
            lightning = next((w['stats'] for w in weapons if w and w['kind'] == 'lightning'), None)
            arc_ready = lightning and energy > lightning['energy'] + 10
            can_attack = target is threat and not recovering and (arc_ready or
                not primary.get('reload', 0) and primary.get('ammo', 1) > 0)
            weak = can_attack and threat.get('hp', me['max_hp']) < .4 * min(me['hp'], threat.get('max_hp', self.config['mech']['hp']))
            if threats or (can_attack and distance < .7*reach and
                           (not priorities or weak and distance < math.dist(p[:2], goal[:2]))):
                desired = .5 * reach
                if not can_attack:
                    desired = max(distance + 20, .6 * reach)
                elif arc_ready:
                    desired = .78 * lightning['range']
                elif weak:
                    desired = max(30, .2 * reach)
                radial=max(-1,min(1,(desired-distance)/35))
                tangent=.8*self.direction
                combat_goal=(x+35*(ux*radial-uy*tangent),y+35*(uy*radial+ux*tangent))
                if weak and distance > desired:
                    self.pursuit = (tuple(threat['pos'][:2]), t + 1.)
        elif self.pursuit:
            combat_goal = self.pursuit[0]
        if combat_goal is not None:
            goal = combat_goal
            arrival=4.
            cg = math.dist(goal, safe_center)
            if cg > safe_radius - safe_margin:
                fraction = (safe_radius - safe_margin) / cg
                goal = tuple(c + (g - c) * fraction for c, g in zip(safe_center, goal))
        if recovering and nearest_threat and not crossfire and not urgent:
            hidden=[]
            muzzle=stats.get('eye_height',8)
            eye=(threat['pos'][0],threat['pos'][1],threat['pos'][2]+8)
            for i in range(12):
                theta=i*math.pi/6
                xx,yy=x+35*math.cos(theta),y+35*math.sin(theta)
                zz=self.arena.height(xx,yy)
                if math.dist((xx, yy), safe_center) > safe_radius - safe_margin:continue
                if not self.arena.walkable(xx,yy,zz,radius+.5,stats.get('height',9)):continue
                if not self.arena.clear(eye,(xx,yy,zz+muzzle)):
                    hidden.append((math.dist((xx,yy),goal[:2]),(xx,yy)))
            if hidden:goal=min(hidden)[1];arrival=4.
        if self.goal is None or math.dist(goal[:2],self.goal[:2])>18 or t-self.last_goal>3:
            self.goal=goal;self.last_goal=t
        distance=math.dist(goal[:2],p[:2])
        look=min(8.,distance)
        height=stats.get('height',9)
        max_grade=math.tan(math.radians(stats.get('max_slope_deg',self.config['mech']['max_slope_deg'])))
        direct=unit(goal[0]-x,goal[1]-y)
        if distance<=arrival:
            wp=p
        elif distance<=8 and self.arena.traversable(p,*direct,distance,radius+.3,height,max_grade):
            wp=goal
        else:
            wp=self.arena.waypoint(p,self.goal)
        gx,gy=unit(wp[0]-x,wp[1]-y)
        # Evaluate local trajectories against walls, zone, and imminent projectiles.
        best=(-1e30,(0.,0.))
        for k in range(16 if gx or gy else 0):
            theta=k*math.pi/8;ux,uy=math.cos(theta),math.sin(theta)
            xx,yy=x+ux*look,y+uy*look
            if not self.arena.traversable(p,ux,uy,look,radius+.3,height,max_grade):continue
            score=8*(gx*ux+gy*uy)
            score-=max(0,math.dist((xx,yy),zone['center'])-zone['radius']+7)*2
            if threat:
                # Periodic lateral variation frustrates constant-velocity projectile leading.
                ex,ey=unit(threat['pos'][0]-x,threat['pos'][1]-y)
                score+=.7*math.sin(t*2.2+self.phase)*(ux*(-ey)+uy*ex)
            for b in o.get('projectiles',[]):
                bp=b['pos'];bv=b['vel'];rx=bp[0]-x;ry=bp[1]-y
                vx=bv[0]-ux*stats.get('walk_speed',11.7);vy=bv[1]-uy*stats.get('walk_speed',11.7)
                tau=max(0,min(.6,-(rx*vx+ry*vy)/(vx*vx+vy*vy+1e-9)))
                miss=math.hypot(rx+vx*tau,ry+vy*tau)
                bz=bp[2]+bv[2]*tau-(.5*self.config['weapons']['mortar']['gravity']*tau*tau if b['kind']=='mortar' else 0)
                if z-3<bz<z+stats.get('height',9)+4:score-=max(0,radius+5-miss)*3
            if score>best[0]:best=(score,(ux,uy))
        move=best[1] if gx or gy else (0.,0.)
        self.stuck=self.stuck+1 if self.previous and math.dist(p[:2],self.previous[:2])<.15 and (gx or gy) else 0
        if self.stuck>8 and best[0]<-1e20:move=(gx,gy)
        self.previous=p
        a['jump']=bool(me['grounded'] and self.stuck>8)
        escape = (crossfire and not urgent) or (recovering and nearest_threat and math.dist(nearest_threat['pos'][:2],p[:2])<120)
        a['boost']=bool(energy>35 and (not me['in_zone'] or escape or self.stuck>8))
        speed=stats['boost_speed'] if a['boost'] else stats['walk_speed']
        match=self.config['match']
        interval=match['decide_every']/match['tick_hz']
        fraction=min(1.,distance/max(1e-8,speed*interval))
        a['move']=[component*fraction for component in move]
        # Shared torso aim favors laser hitscan, then gun, then ballistics.
        options = [shot] if shot else []
        if not recovering and not threats:
            targets = [dict(q,kind='crate') for q in o['crates']]
            targets += [e for e in o['mechs'] if e['kind']=='npc' and math.dist(e['pos'][:2],p[:2])<110]
            for target in targets:
                option = self.shot_option(me, target, primary)
                if option:
                    options.append(option)
        a['fire']=[False,False]
        if options:
            _,target,sol=min(options,key=lambda q:q[0]);a['aim']=list(sol[:2])
            solutions=[]
            for weapon in weapons:
                aim=sol if weapon is primary else None
                if weapon and weapon is not primary:
                    aim=self.aim_at(me,target,weapon)
                solutions.append(aim)
            use_mortar=False
            if mortar and nearest>35:
                arc=solutions[weapons.index(mortar)]
                gun_ready=any(v and v['kind'] in ('gun','laser') and v['cooldown']<=.05
                              and not v.get('reload',0) and (v['kind']!='gun' or v.get('ammo',0)>0)
                              for v in weapons)
                if arc and 22<arc[2]<200 and (primary is mortar or not gun_ready):
                    use_mortar=True
                    a['aim']=list(arc[:2])
            for i,(w,aim) in enumerate(zip(weapons,solutions)):
                if not w:continue
                if not aim:continue
                yaw,pitch,d,clear=aim;kind=w['kind'];s=w['stats']
                yaw_err=abs(angle(yaw-me['aim'][0]));pitch_err=abs(pitch-me['aim'][1])
                tol=math.atan2(1.7*(1+.05*target.get('cubes',0)),max(1,d))
                aligned=yaw_err<max(.015,tol) and pitch_err<max(.015,math.atan2(2,max(1,d)))
                if kind=='gun':
                    # A held trigger can become ready after the torso has turned toward another slot.
                    shared_yaw,shared_pitch=a['aim']
                    agrees=(abs(angle(yaw-shared_yaw))<max(.015,tol)
                            and abs(pitch-shared_pitch)<max(.015,math.atan2(2,max(1,d))))
                    a['fire'][i]=bool(clear and d<s.get('range',230) and aligned and agrees)
                elif kind=='laser':
                    # Keep charged until aim settles, then release at full charge.
                    a['fire'][i]=bool(w['cooldown']<=.05 and (w.get('charge',0)<s.get('charge',.75) or not aligned))
                elif kind=='lightning':a['fire'][i]=bool(clear and d<s['range'] and yaw_err<math.radians(s['cone_deg'])*.45 and energy>s.get('energy',3)+10)
                elif kind=='mortar':
                    a['fire'][i]=bool(use_mortar and aligned and clear)
        else:
            bearing=next((e['bearing'] for e in o['events'] if e['type']=='damaged' and e.get('bearing') is not None),None)
            if bearing is None and threat:
                bearing=math.atan2(threat['pos'][1]-y,threat['pos'][0]-x)
            a['aim']=[bearing if bearing is not None else math.atan2(gy,gx)+math.sin(t*.9)*1.6,0]
            # A released laser still fires, so point it toward any remembered threat.
            if threat and any(w and w.get('charge',0)>0 for w in weapons):
                a['aim']=[math.atan2(threat['pos'][1]-y,threat['pos'][0]-x),math.atan2(threat['pos'][2]-z-2.5,max(1,nearest))]
        gun=next((w for w in weapons if w and w['kind']=='gun'),None)
        if gun:a['reload']=bool(gun.get('ammo',0)==0 or (not options and gun.get('ammo',0)<gun['stats'].get('mag',12)*.7))
        return a


if __name__=='__main__':mf.run(Bot)
