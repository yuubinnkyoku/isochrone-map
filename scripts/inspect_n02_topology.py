#!/usr/bin/env python3
"""Temporary structural inspection of N02 Station/RailroadSection GeoJSON."""
from __future__ import annotations
import io, json, urllib.request, zipfile
from collections import Counter
from pathlib import Path
from audit_n02_station_groups import SOURCE_URL, USER_AGENT

req = urllib.request.Request(SOURCE_URL, headers={'User-Agent': USER_AGENT})
with urllib.request.urlopen(req, timeout=180) as response:
    payload = response.read()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    names = z.namelist()
    station_names = [n for n in names if n.casefold().endswith('station.geojson')]
    rail_names = [n for n in names if n.casefold().endswith('railroadsection.geojson')]
    out = {
        'archiveNames': names,
        'stationCandidates': station_names,
        'railroadCandidates': rail_names,
    }
    if not station_names or not rail_names:
        Path('data/n02-topology-inspection.json').write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        raise SystemExit('required GeoJSON missing')
    station = json.loads(z.read(sorted(station_names, key=len)[0]))
    rail = json.loads(z.read(sorted(rail_names, key=len)[0]))

out['stationFeatureCount'] = len(station.get('features', []))
out['railFeatureCount'] = len(rail.get('features', []))
out['stationGeometryTypes'] = dict(Counter((f.get('geometry') or {}).get('type') for f in station.get('features', [])))
out['railGeometryTypes'] = dict(Counter((f.get('geometry') or {}).get('type') for f in rail.get('features', [])))
out['stationPropertyKeys'] = sorted({k for f in station.get('features', [])[:100] for k in (f.get('properties') or {})})
out['railPropertyKeys'] = sorted({k for f in rail.get('features', [])[:100] for k in (f.get('properties') or {})})
out['stationExamples'] = station.get('features', [])[:3]
out['railExamples'] = rail.get('features', [])[:3]

# Structural coordinate overlap statistics.  Round to 7 decimals to absorb serialization noise.
def coords(geom):
    value = (geom or {}).get('coordinates', [])
    def walk(x):
        if isinstance(x, list) and len(x)>=2 and isinstance(x[0], (int,float)) and isinstance(x[1], (int,float)):
            yield (round(float(x[0]),7), round(float(x[1]),7))
        elif isinstance(x, list):
            for y in x: yield from walk(y)
    yield from walk(value)
rail_vertices = set()
for f in rail.get('features', []): rail_vertices.update(coords(f.get('geometry')))
station_vertex_counts=[]
for f in station.get('features', []):
    pts=list(coords(f.get('geometry')))
    station_vertex_counts.append(sum(p in rail_vertices for p in pts))
out['railUniqueVertices']=len(rail_vertices)
out['stationFeaturesWithAnyExactRailVertex']=sum(v>0 for v in station_vertex_counts)
out['stationFeaturesWithAllExactRailVertices']=sum(v>0 and v==len(list(coords(f.get('geometry')))) for v,f in zip(station_vertex_counts, station.get('features', [])))
Path('data/n02-topology-inspection.json').write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k not in {'archiveNames','stationExamples','railExamples'}}, ensure_ascii=False, indent=2))
