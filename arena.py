"""Static collision queries and incremental grid navigation, standard library only."""
import array
import hashlib
import zlib
import heapq
import json
import math
import os
import sys


def inside(x, y, ring):
    odd = False
    j = len(ring) - 2
    for i in range(0, len(ring), 2):
        xi, yi, xj, yj = ring[i], ring[i+1], ring[j], ring[j+1]
        if (yi > y) != (yj > y) and x < (xj-xi)*(y-yi)/(yj-yi)+xi:
            odd = not odd
        j = i
    return odd


class Arena:
    def __init__(self, folder):
        with open(os.path.join(folder, 'v2/world.json')) as f:
            w = json.load(f)
        self.bounds = w['bounds']
        self.t = w['terrain']
        self.map_grade = math.tan(math.radians(w['rules']['maxSlopeDeg']))
        self.heights = array.array('h')
        with open(os.path.join(folder, 'v2/terrain.i16'), 'rb') as f:
            self.heights.frombytes(f.read())
        if sys.byteorder != 'little':
            self.heights.byteswap()
        self.buckets = {}
        for p in w['prisms']:
            r = p['ring']
            p['box'] = (min(r[::2]), min(r[1::2]), max(r[::2]), max(r[1::2]))
            a,b,c,d = p['box']
            for i in range(math.floor((a-4)/20), math.floor((c+4)/20)+1):
                for j in range(math.floor((b-4)/20), math.floor((d+4)/20)+1):
                    self.buckets.setdefault((i,j), []).append(p)
        self.step = 5
        self.x0, self.y0 = self.bounds['minX']+5, self.bounds['minY']+5
        self.nx = int((self.bounds['maxX']-self.x0-5)/self.step)
        self.ny = int((self.bounds['maxY']-self.y0-5)/self.step)
        self.grid = bytearray(self.nx*self.ny)
        cache = os.path.join(os.path.dirname(__file__), 'navigation.json')
        checksum = hashlib.sha256(json.dumps(w, separators=(',', ':')).encode()).hexdigest()
        cached = None
        if os.path.exists(cache):
            with open(cache) as f:
                cached = json.load(f)
        if cached and cached['world_sha256'] == checksum:
            self.grid[:] = zlib.decompress(bytes.fromhex(cached['grid_zlib_hex']))
        else:
            for j in range(self.ny):
                for i in range(self.nx):
                    x,y = self.xy((i,j))
                    self.grid[j*self.nx+i] = self.walkable(x,y,self.height(x,y),3.0)
        self.checksum = checksum
        self.search = None
        self.path = []
        self.goal = None

    def height(self,x,y):
        t = self.t
        fx = max(0,min(t['nx']-1.001,(x-t['minX'])/t['step']))
        fy = max(0,min(t['ny']-1.001,(y-t['minY'])/t['step']))
        i,j = int(fx),int(fy)
        ax,ay = fx-i,fy-j
        k = j*t['nx']+i
        h = self.heights
        return .01*((1-ay)*((1-ax)*h[k]+ax*h[k+1])+ay*((1-ax)*h[k+t['nx']]+ax*h[k+t['nx']+1]))

    def solid(self,x,y,z,pad=0):
        for p in self.buckets.get((math.floor(x/20),math.floor(y/20)),()):
            a,b,c,d = p['box']
            if x<a-pad or x>c+pad or y<b-pad or y>d+pad or not p['base'] < z < p['top']:
                continue
            if inside(x,y,p['ring']) and not any(inside(x,y,h) for h in p['holes']):
                return True
            if pad:
                r=p['ring']
                for i in range(0,len(r),2):
                    xx,yy = r[i],r[i+1]
                    u,v = r[(i+2)%len(r)]-xx,r[(i+3)%len(r)]-yy
                    a1 = max(0,min(1,((x-xx)*u+(y-yy)*v)/(u*u+v*v+1e-9)))
                    if (xx+a1*u-x)**2+(yy+a1*v-y)**2 < pad*pad:
                        return True
        return False

    def walkable(self,x,y,z,pad=2.7,height=9):
        b=self.bounds
        return b['minX']+pad<x<b['maxX']-pad and b['minY']+pad<y<b['maxY']-pad and not any(self.solid(x,y,z+dz,pad) for dz in (1.21,height*.5,height)) and self.height(x,y)<z+1.2

    def traversable(self,pos,ux,uy,length,pad,height,max_grade=None):
        # Ground follows a slope. Rooftop and airborne movement retain current feet height.
        x,y,z=pos
        if max_grade is None:max_grade=self.map_grade
        ground=self.height(x,y)
        on_ground=abs(ground-z)<1.3
        prev=ground
        # Keep the original two-metre samples and check the actual endpoint.
        spacing=2.
        samples=math.ceil(length/spacing)
        previous_distance=0.
        for i in range(1,samples+1):
            distance=min(length,i*spacing)
            xx,yy=x+ux*distance,y+uy*distance
            h=self.height(xx,yy)
            if on_ground and h-prev>(distance-previous_distance)*max_grade:
                return False
            feet=h if on_ground else z
            if not self.walkable(xx,yy,feet,pad,height):return False
            prev=h
            previous_distance=distance
        return True

    def clear(self,a,b):
        n=max(1,math.ceil(math.dist(a,b)/2.5))
        for i in range(1,n):
            f=i/n
            x,y,z=(a[k]+f*(b[k]-a[k]) for k in range(3))
            if self.solid(x,y,z) or self.height(x,y)>z:
                return False
        return True

    def cell(self,p):
        return (max(0,min(self.nx-1,round((p[0]-self.x0)/self.step))),max(0,min(self.ny-1,round((p[1]-self.y0)/self.step))))

    def xy(self,c):
        return (self.x0+c[0]*self.step,self.y0+c[1]*self.step)

    def free(self,c):
        i,j=c
        return 0<=i<self.nx and 0<=j<self.ny and self.grid[j*self.nx+i]

    def nearest(self,p):
        c=self.cell(p)
        if self.free(c): return c
        for r in range(1,12):
            cs=[(c[0]+dx,c[1]+dy) for dx in range(-r,r+1) for dy in range(-r,r+1) if abs(dx)==r or abs(dy)==r]
            cs=[q for q in cs if self.free(q)]
            if cs: return min(cs,key=lambda q:math.dist(self.xy(q),p[:2]))
        return c

    def waypoint(self,pos,goal,exact_cell=False):
        target=self.nearest(goal)
        if self.goal is None or math.dist(self.goal,target)>(0 if exact_cell else 3):
            self.goal=target
            start=self.nearest(pos)
            self.search=([(math.dist(start,target),0,start)],{start:0},{},start)
            self.path=[]
        if self.search:
            heap,cost,parents,start=self.search
            for _ in range(300):
                if not heap:
                    self.search=None
                    break
                _,g,c=heapq.heappop(heap)
                if g>cost[c]:continue
                if c==self.goal:
                    path=[c]
                    while c in parents:
                        c=parents[c];path.append(c)
                    self.path=list(reversed(path));self.search=None;break
                for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                    q=(c[0]+dx,c[1]+dy)
                    if not self.free(q) or (dx and dy and (not self.free((c[0]+dx,c[1])) or not self.free((c[0],c[1]+dy)))):continue
                    ng=g+(1.414214 if dx and dy else 1)
                    if ng<cost.get(q,1e30):
                        cost[q]=ng;parents[q]=c
                        heapq.heappush(heap,(ng+math.dist(q,self.goal),ng,q))
        if self.path:
            nearest=min(range(len(self.path)),key=lambda i:math.dist(pos[:2],self.xy(self.path[i])))
            return self.xy(self.path[min(nearest+2,len(self.path)-1)])
        return goal[:2]
