"""Deterministic 2D A*; occupancy comes from live Unreal capsule sweeps."""
import heapq, math

def plan(start, goal, clear, walkable, cell=50, limit=6000):
    start,goal=tuple(start),tuple(goal)
    if clear(start, goal): return [goal]
    origin=start
    def pos(n): return (origin[0]+n[0]*cell,origin[1]+n[1]*cell)
    initial=(0,0); queue=[(math.dist(start,goal),0,initial)]; costs={initial:0}; parents={}; visited=set(); checks={}
    def edge(a,b):
        k=(a,b)
        if k not in checks: checks[k]=clear(a,b)
        return checks[k]
    while queue and len(visited)<limit:
        _,g,n=heapq.heappop(queue)
        if n in visited:continue
        visited.add(n);p=pos(n)
        if math.dist(p,goal)<cell*1.5 and edge(p,goal):
            path=[goal,p]
            while n in parents:n=parents[n];path.append(pos(n))
            path.reverse(); simplified=[]; anchor=start; i=1
            while i<len(path):
                j=i
                while j+1<len(path) and edge(anchor,path[j+1]):j+=1
                simplified.append(path[j]);anchor=path[j];i=j+1
            return simplified
        for dx,dy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
            nn=(n[0]+dx,n[1]+dy);q=pos(nn)
            if nn in visited or not walkable(q) or not edge(p,q):continue
            ng=g+math.hypot(dx,dy)*cell
            if ng<costs.get(nn,float('inf')):
                costs[nn]=ng;parents[nn]=n;heapq.heappush(queue,(ng+math.dist(q,goal),ng,nn))
    return None
