"""Independent PYPOWER AC-OPF from the original MATPOWER numeric tables."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from distributed_opf.matpower_case import internal_case
from distributed_opf.config import load_topology_config
from distributed_opf.ac_validation import validate_ac
from pandapower.pypower.opf import opf
from pandapower.pypower.ppoption import ppoption
from pandapower.pypower.makeYbus import makeYbus

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    case=internal_case('data/matpower/case118.m')
    # pandapower 2.14's PIPS derivative code fails for an empty set of rated
    # branches. Add only mathematically redundant bounds, derived from Yf/Yt
    # and the original voltage upper bounds. The source remains unchanged.
    _, yf, yt = makeYbus(case['baseMVA'], case['bus'], case['branch'])
    vmax = case['bus'][:,11]
    f = case['branch'][:,0].astype(int)
    t = case['branch'][:,1].astype(int)
    upper = np.maximum(vmax[f]*(abs(yf)@vmax), vmax[t]*(abs(yt)@vmax))*case['baseMVA']
    unrated = case['branch'][:,5] == 0
    case['branch'][unrated,5] = upper[unrated]*(1+1e-6)
    options=ppoption(VERBOSE=0,OUT_ALL=0,INIT='flat',OPF_ALG=560,
        PDIPM_FEASTOL=1e-9,PDIPM_GRADTOL=1e-9,PDIPM_COMPTOL=1e-9,PDIPM_COSTTOL=1e-9)
    start=time.perf_counter()
    result=opf(case,options)
    elapsed=time.perf_counter()-start
    states={int(b[0]):dict(v=float(b[7]),va=float(np.deg2rad(b[8])),pg=0.,qg=0.) for b in result['bus']}
    for g in result['gen']:
        states[int(g[0])]['pg'] += float(g[1]/case['baseMVA'])
        states[int(g[0])]['qg'] += float(g[2]/case['baseMVA'])
    validation=validate_ac(load_topology_config('topology_ieee118_full.yaml'),states)
    report=dict(success=bool(result['success'] and validation['feasible']), solver='PYPOWER PIPS AC OPF',
        objective=float(result['f']),solver_wall_s=elapsed,states=states,validation=validation,
        redundant_rating_note='Unrated branches receive bounds proven redundant over the original voltage box to avoid an empty-derivative bug in pandapower 2.14 PIPS.',
        redundant_rating_count=int(unrated.sum()))
    (args.out/'reference.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('states','validation')},indent=2))
    raise SystemExit(0 if report['success'] else 2)
