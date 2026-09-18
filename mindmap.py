"""Animated node-tree mindmap segments.

Rendered deterministically via headless Chromium (Playwright) -> PNG frames ->
ffmpeg, at the SAME 30fps / 1920x1080 / yuv420p (limited range) / libx264 as the
rest of the pipeline, so a mindmap mp4 concatenates cleanly with photo/pexels
segments (same reason the segment renderers force -color_range tv).

Modules kept separate so each can be tuned alone:
  - layout_tree()      : mindmap tree -> node x,y (right-tree)
  - MINDMAP_HTML       : the canvas renderer (draws a frame for a given time t)
  - render_mindmap()   : drive Chromium frame-by-frame -> ffmpeg -> mp4
The SRT-driven camera-timeline generation and pipeline wiring live elsewhere;
this file is only the DATA + RENDER core (MVP).
"""

import json
import math
import subprocess
import tempfile
import zlib
from pathlib import Path


# ════════════════════════════════════════════════════════════════════════════
# 1) LAYOUT  —  tree -> node positions (right-tree). Positions are auto-computed,
#    never hand-entered. Coordinates are "world" units centered on the root.
# ════════════════════════════════════════════════════════════════════════════
def layout_tree(root: dict, col_w: float = 620.0, row_h: float = 250.0) -> dict:
    """Annotate every node with _x,_y (center) using a tidy right-tree layout:
    x by depth, y by leaf order with parents centered on their children."""
    leaves = []

    def depth_pass(n, depth):
        n["_depth"] = depth
        kids = n.get("children") or []
        if kids:
            for c in kids:
                depth_pass(c, depth + 1)
        else:
            leaves.append(n)

    depth_pass(root, 0)
    for i, leaf in enumerate(leaves):
        leaf["_y"] = i * row_h

    def y_pass(n):
        kids = n.get("children") or []
        if kids:
            for c in kids:
                y_pass(c)
            n["_y"] = 0.5 * (kids[0]["_y"] + kids[-1]["_y"])

    y_pass(root)

    # center the whole map on (0,0): root at x=0, vertical midpoint at 0
    ys = [leaf["_y"] for leaf in leaves] or [0.0]
    y_mid = 0.5 * (min(ys) + max(ys))

    def place(n):
        n["_x"] = n["_depth"] * col_w
        n["_y"] = n["_y"] - y_mid
        for c in (n.get("children") or []):
            place(c)

    place(root)
    return root


def flatten(root: dict) -> list:
    out = []

    def walk(n, parent):
        out.append({"id": n["id"], "label": n.get("label", ""), "detail": n.get("detail", ""),
                    "x": n.get("_x", 0.0), "y": n.get("_y", 0.0), "parent": parent,
                    "depth": n.get("_depth", 0),
                    "w": n.get("_w", 380.0), "h": n.get("_h", 104.0),
                    "color": n.get("_color", ""), "shape": n.get("_shape", "box"),
                    "image": n.get("image", "")})
        for c in (n.get("children") or []):
            walk(c, n["id"])

    walk(root, None)
    return out


# ── Variety: accent themes (rotated per minute) + a PYRAMID layout ──────────────
THEMES = [
    {"activeOutline": "#3b82f6", "line": "#4a4a4a"},   # blue
    {"activeOutline": "#f59e0b", "line": "#5a4a2a"},   # amber
    {"activeOutline": "#10b981", "line": "#2a4a3a"},   # green
    {"activeOutline": "#ef4444", "line": "#5a2a2a"},   # red
    {"activeOutline": "#a855f7", "line": "#3a2a5a"},   # purple
    {"activeOutline": "#06b6d4", "line": "#2a4a52"},   # cyan
]
PYRAMID_COLORS = ["#14b8a6", "#84cc16", "#ef4444", "#f59e0b", "#8b5cf6", "#22c55e", "#3b82f6"]
# bright, harmonious node colors for the tree (dark text on top) — pretty + scannable
TREE_COLORS = ["#f472b6", "#60a5fa", "#34d399", "#fbbf24", "#a78bfa", "#22d3ee", "#fb923c", "#4ade80"]


def layout_pyramid(root: dict) -> dict:
    """Central label on top, sub-points as stacked, widening, colored bars below —
    a pyramid. Node ids/positions still work with the same camera."""
    pts = root.get("children") or []
    n = max(1, len(pts))
    base_w, step_w, bar_h, gap = 460.0, 150.0, 116.0, 26.0
    root["_w"], root["_h"], root["_shape"] = 380.0, 92.0, "pill"
    root["_x"], root["_y"] = -190.0, -(bar_h + gap + 92.0)   # centered on x=0, above the pyramid
    for i, p in enumerate(pts):
        w = base_w + i * step_w
        p["_w"], p["_h"], p["_shape"] = w, bar_h, "bar"
        p["_x"], p["_y"] = -w / 2.0, i * (bar_h + gap)
        p["_color"] = PYRAMID_COLORS[i % len(PYRAMID_COLORS)]
        p["children"] = []      # pyramid is one level deep
    return root


# ── 1-level layouts (root + its points) rotated per minute for visual variety ──
_BW, _BH, _COL, _ROW = 380.0, 104.0, 560.0, 250.0


def _pts(root):
    return root.get("children") or []


def layout_right(root):
    pts = _pts(root); n = max(1, len(pts))
    for i, p in enumerate(pts):
        p["_x"], p["_y"], p["_w"], p["_h"] = _COL, i * _ROW, _BW, _BH
    root["_x"], root["_y"], root["_w"], root["_h"] = 0.0, (n - 1) * _ROW / 2, _BW, _BH
    return root


def layout_left(root):
    pts = _pts(root); n = max(1, len(pts))
    for i, p in enumerate(pts):
        p["_x"], p["_y"], p["_w"], p["_h"] = -_COL, i * _ROW, _BW, _BH
    root["_x"], root["_y"], root["_w"], root["_h"] = 0.0, (n - 1) * _ROW / 2, _BW, _BH
    return root


def layout_split(root):
    pts = _pts(root); mid = (len(pts) + 1) // 2
    right, left = pts[:mid], pts[mid:]
    for i, p in enumerate(right):
        p["_x"], p["_y"], p["_w"], p["_h"] = _COL, i * _ROW, _BW, _BH
    for i, p in enumerate(left):
        p["_x"], p["_y"], p["_w"], p["_h"] = -_COL - _BW, i * _ROW, _BW, _BH
    h = (max(len(right), len(left)) - 1) * _ROW / 2 if (right or left) else 0.0
    root["_x"], root["_y"], root["_w"], root["_h"] = -_BW / 2, h, _BW, _BH
    return root


def layout_radial(root):
    pts = _pts(root); n = max(1, len(pts)); rr = 380.0 + n * 45.0
    for i, p in enumerate(pts):
        a = -math.pi / 2 + 2 * math.pi * i / n
        p["_w"], p["_h"] = _BW, _BH
        p["_x"] = math.cos(a) * rr - _BW / 2
        p["_y"] = math.sin(a) * rr - _BH / 2
    root["_x"], root["_y"], root["_w"], root["_h"] = -_BW / 2, -_BH / 2, _BW, _BH
    return root


def layout_columns(root):
    pts = _pts(root); n = max(1, len(pts)); gap = 44.0
    tot = n * _BW + (n - 1) * gap
    for i, p in enumerate(pts):
        p["_x"], p["_y"], p["_w"], p["_h"] = -tot / 2 + i * (_BW + gap), 0.0, _BW, _BH
    root["_x"], root["_y"], root["_w"], root["_h"] = -_BW / 2, -_ROW, _BW, _BH
    return root


_LAYOUTS_FN = {"tree": layout_right, "tree-right": layout_right, "tree-left": layout_left,
               "tree-split": layout_split, "pyramid": layout_pyramid,
               "radial": layout_radial, "columns": layout_columns}
LAYOUT_ROTATION = ["tree-right", "radial", "pyramid", "tree-split", "columns", "tree-left"]


def apply_layout(mindmap: dict) -> dict:
    """Position nodes per the mindmap's 'layout'. A nested map (mindmap-only big tree)
    always uses the recursive right-tree; a 1-level per-minute map uses the chosen layout."""
    root = mindmap["root"]
    nested = any((c.get("children")) for c in (root.get("children") or []))
    if nested:
        return layout_tree(root)
    return _LAYOUTS_FN.get(mindmap.get("layout", "tree-right"), layout_right)(root)


# ════════════════════════════════════════════════════════════════════════════
# 2) RENDERER  —  a self-contained canvas page. Given window.renderFrame(t) it
#    draws exactly that frame: eased camera pan/zoom, easing cursor with micro
#    jitter, detail-box fade (smoothstep), active-node blue outline.
# ════════════════════════════════════════════════════════════════════════════
MINDMAP_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;padding:0;background:#0a0a0a;overflow:hidden}
  canvas{display:block}
</style></head><body>
<canvas id="c"></canvas>
<script>
const W=__W__, H=__H__;
const NODES=__NODES__;      // [{id,label,detail,x,y,w,h,color,shape,parent,depth}]
let   CAM=__CAM__;
const TH=__THEME__;
const LAYOUT=__LAYOUT__;    // 'tree' | 'pyramid'
const cv=document.getElementById('c'); cv.width=W; cv.height=H;
const ctx=cv.getContext('2d');
const byId={}; NODES.forEach((n,i)=>{n._num=i+1; byId[n.id]=n;});
window.setCamera=function(c){CAM=c;};

const R=16, SS=2;
function radOf(n){return n.shape==='pill'?n.h/2:(n.shape==='bar'?16:R);}
function MOVE_DUR(){return (CAM.move_dur||1.15);}

function easeInOutPow(p,e){return p<0.5?Math.pow(2*p,e)/2:1-Math.pow(2-2*p,e)/2;}
function easeCam(p){return easeInOutPow(p,1.7);}
function easeOutCubic(p){return 1-Math.pow(1-p,3);}
function smoothstep(a,b,x){if(b<=a)return x<a?0:1;let t=Math.min(1,Math.max(0,(x-a)/(b-a)));return t*t*(3-2*t);}
function lerp(a,b,p){return a+(b-a)*p;}
function rr(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath();}
function wrapW(c,text,maxW){const words=(text||'').split(/\s+/);const lines=[];let cur='';for(const w of words){const t=(cur+' '+w).trim();if(c.measureText(t).width>maxW&&cur){lines.push(cur);cur=w;}else cur=t;}if(cur)lines.push(cur);return lines;}
const FONT='-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif';

// --- world bounds ---
let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9;
const rpad=(LAYOUT==='pyramid')?40:130;   // tree has a numbered circle to the right
NODES.forEach(n=>{minX=Math.min(minX,n.x);minY=Math.min(minY,n.y);maxX=Math.max(maxX,n.x+n.w+rpad);maxY=Math.max(maxY,n.y+n.h);});
const PAD=90; minX-=PAD;minY-=PAD;maxX+=PAD;maxY+=PAD;
const sceneW=Math.ceil(maxX-minX), sceneH=Math.ceil(maxY-minY);

// --- images preloaded into imgs{}; scene + detail cards built in init() ---
const imgs={};
function drawImg(sc,im,x,y,w,h){const ar=im.width/im.height,tr=w/h;let sw,sh,sx,sy;
  if(ar>tr){sh=im.height;sw=sh*tr;sx=(im.width-sw)/2;sy=0;}else{sw=im.width;sh=sw/tr;sx=0;sy=(im.height-sh)/2;}
  sc.save();rr(sc,x,y,w,h,10);sc.clip();sc.drawImage(im,sx,sy,sw,sh,x,y,w,h);sc.restore();}
const scene=document.createElement('canvas'); scene.width=sceneW*SS; scene.height=sceneH*SS;
function buildScene(){ const sc=scene.getContext('2d'); sc.scale(SS,SS); sc.translate(-minX,-minY);
  if(LAYOUT!=='pyramid'){
    // colored connectors (match the child node), direction-aware for every layout
    sc.lineJoin='round';
    NODES.forEach(n=>{ if(!n.parent)return; const p=byId[n.parent];
      const pcx=p.x+p.w/2,pcy=p.y+p.h/2,ccx=n.x+n.w/2,ccy=n.y+n.h/2;
      sc.strokeStyle=n.color||TH.line; sc.lineWidth=3;
      if(LAYOUT==='radial'){
        const a=Math.atan2(ccy-pcy,ccx-pcx);
        sc.beginPath();
        sc.moveTo(pcx+Math.cos(a)*p.w*0.42, pcy+Math.sin(a)*p.h*0.62);
        sc.lineTo(ccx-Math.cos(a)*n.w*0.42, ccy-Math.sin(a)*n.h*0.62); sc.stroke();
      } else { const dx=ccx-pcx, dy=ccy-pcy;
        if(Math.abs(dx)>=Math.abs(dy)){ const x1=pcx+(dx>0?p.w/2:-p.w/2), x2=ccx+(dx>0?-n.w/2:n.w/2), mx=(x1+x2)/2;
          sc.beginPath();sc.moveTo(x1,pcy);sc.lineTo(mx,pcy);sc.lineTo(mx,ccy);sc.lineTo(x2,ccy);sc.stroke();
        } else { const y1=pcy+(dy>0?p.h/2:-p.h/2), y2=ccy+(dy>0?-n.h/2:n.h/2), my=(y1+y2)/2;
          sc.beginPath();sc.moveTo(pcx,y1);sc.lineTo(pcx,my);sc.lineTo(ccx,my);sc.lineTo(ccx,y2);sc.stroke(); }
      }
    });
    NODES.forEach(n=>{
      const col=n.color;
      rr(sc,n.x,n.y,n.w,n.h,18);
      if(col){ sc.fillStyle=col; sc.fill(); }
      else { sc.fillStyle='#171717'; sc.fill(); sc.lineWidth=1.4; sc.strokeStyle=TH.nodeStroke; sc.stroke(); }
      sc.fillStyle=col?'#0b0b0b':TH.text; sc.textAlign='center'; sc.textBaseline='middle';
      const fs=col?29:31; sc.font=`700 ${fs}px ${FONT}`;
      const lines=wrapW(sc,n.label,n.w-40); const lh=fs*1.15; let ly=n.y+n.h/2-(lines.length-1)*lh/2;
      lines.forEach(L=>{sc.fillText(L,n.x+n.w/2,ly);ly+=lh;});
    });
  } else {  // PYRAMID: central pill on top, colored widening bars stacked below
    NODES.forEach(n=>{
      if(n.shape==='pill'){
        rr(sc,n.x,n.y,n.w,n.h,n.h/2); sc.fillStyle=TH.node; sc.fill();
        sc.lineWidth=1.6; sc.strokeStyle=TH.nodeStroke; sc.stroke();
        sc.fillStyle=TH.text; sc.textAlign='center'; sc.textBaseline='middle'; sc.font=`700 30px ${FONT}`;
        const lines=wrapW(sc,n.label,n.w-40); const lh=33; let ly=n.y+n.h/2-(lines.length-1)*lh/2;
        lines.forEach(L=>{sc.fillText(L,n.x+n.w/2,ly);ly+=lh;});
      } else {
        rr(sc,n.x,n.y,n.w,n.h,16); sc.fillStyle=n.color||'#334155'; sc.fill();
        sc.fillStyle='#0b0b0b'; sc.textAlign='center'; sc.textBaseline='middle'; sc.font=`800 32px ${FONT}`;
        const lines=wrapW(sc,n.label,n.w-56); const lh=36; let ly=n.y+n.h/2-(lines.length-1)*lh/2;
        lines.forEach(L=>{sc.fillText(L,n.x+n.w/2,ly);ly+=lh;});
      }
    });
  }
}

// --- pre-render each detail CARD (image on top + rich text below -> stable) ---
const detail={};
function buildDetails(){ NODES.forEach(n=>{ const im=imgs[n.id]; if(!n.detail && !im)return;
  const dw=Math.max(360,Math.min(n.w,560)), dfs=22, pad=16;
  const m=document.createElement('canvas'); const mc=m.getContext('2d');
  mc.font=`400 ${dfs}px ${FONT}`; const lines=n.detail?wrapW(mc,n.detail,dw-2*pad):[]; const lh=dfs*1.4;
  const imgH=im?Math.round((dw-2*pad)*0.5):0;                 // 2:1 image banner
  const dh=Math.ceil((im?imgH+14:0)+lines.length*lh+(lines.length?2*pad:pad));
  m.width=dw*SS; m.height=dh*SS; const d=m.getContext('2d'); d.scale(SS,SS);
  rr(d,0.5,0.5,dw-1,dh-1,12); d.fillStyle=TH.detailBg; d.fill(); d.strokeStyle=TH.nodeStroke; d.lineWidth=1; d.stroke();
  let yy=pad;
  if(im){ drawImg(d,im,pad,pad,dw-2*pad,imgH); yy=pad+imgH+14; }
  d.fillStyle=TH.text; d.font=`400 ${dfs}px ${FONT}`; d.textBaseline='top'; d.textAlign='left';
  lines.forEach(L=>{d.fillText(L,pad,yy);yy+=lh;});
  detail[n.id]={cv:m,w:dw,h:dh};
}); }

// --- camera position: DWELL on a node, then move to the next in the last MOVE_DUR seconds ---
function camState(t){
  const k=CAM.keys;
  if(t<=k[0].t) return {a:k[0],b:k[0],mp:0};
  for(let i=0;i<k.length-1;i++){
    if(t<k[i+1].t){ const D=k[i+1].t-k[i].t; const md=Math.min(MOVE_DUR(),D*0.85);
      const mp=Math.max(0,Math.min(1,(t-(k[i+1].t-md))/md)); return {a:k[i],b:k[i+1],mp}; }
  }
  return {a:k[k.length-1],b:k[k.length-1],mp:0};
}

function renderFrame(t){
  const s=camState(t); const a=s.a,b=s.b; const pc=easeCam(s.mp);
  const fa=byId[a.focus]||NODES[0], fb=byId[b.focus]||NODES[0];
  let camX=lerp(fa.x+fa.w/2,fb.x+fb.w/2,pc);
  let camY=lerp(fa.y+fa.h/2,fb.y+fb.h/2,pc);
  let scale=lerp(a.scale,b.scale,pc);
  // subtle organic handheld drift (3 incommensurate components -> never repeats/robotic)
  camX+=Math.sin(t*0.83)*7+Math.sin(t*1.9)*3+Math.sin(t*0.31+1.0)*5;
  camY+=Math.cos(t*0.71)*6+Math.sin(t*1.6)*2.5+Math.cos(t*0.27+0.5)*4;
  scale*=1+Math.sin(t*0.5)*0.006+Math.sin(t*0.19)*0.004;
  // ---- cursor: human curved travel between nodes, then an organic orbit AROUND the
  //      dwelt node (circles its edges, not just sits inside), + fine hand tremor ----
  const mp=s.mp;
  const ua=byId[a.cursor]||fa, ub=byId[b.cursor]||fb;
  const Ax=ua.x+ua.w/2, Ay=ua.y+ua.h/2, Bx=ub.x+ub.w/2, By=ub.y+ub.h/2;
  const et=easeCam(mp);                                  // accelerate/decelerate (human)
  const dX=Bx-Ax, dY=By-Ay, pl=Math.hypot(dX,dY)||1, nx=-dY/pl, ny=dX/pl;
  const arc=Math.min(95,pl*0.13)*Math.sin(Math.PI*mp);   // bowed path, not a straight line
  const travelX=Ax+dX*et+nx*arc, travelY=Ay+dY*et+ny*arc;
  const hump=smoothstep(0.0,0.22,mp)*(1-smoothstep(0.78,1.0,mp)); const settle=1-hump;  // 1 dwell, 0 mid-travel
  const cn=(mp<0.5?ua:ub); const cnx=cn.x+cn.w/2, cny=cn.y+cn.h/2;
  // irregular orbit (incommensurate freqs) so it wanders around the node's edges
  const orbx=cnx + Math.cos(t*0.78+0.6)*cn.w*0.40 + Math.sin(t*1.7)*18 + Math.sin(t*0.29)*26;
  const orby=cny + Math.sin(t*0.63)*cn.h*0.60 + Math.cos(t*1.5)*12 + Math.cos(t*0.24)*15;
  const trx=Math.sin(t*12.7)*1.7+Math.sin(t*21.1)*0.9, try_=Math.cos(t*11.3)*1.7+Math.sin(t*18.9)*0.8;
  const curx=travelX*hump+orbx*settle+trx, cury=travelY*hump+orby*settle+try_;

  function P(wx,wy){return [W/2+(wx-camX)*scale, H/2+(wy-camY)*scale];}
  ctx.fillStyle=TH.bg; ctx.fillRect(0,0,W,H);
  ctx.imageSmoothingEnabled=true; ctx.imageSmoothingQuality='high';
  const [dx,dy]=P(minX,minY); ctx.drawImage(scene,dx,dy,sceneW*scale,sceneH*scale);

  // active = node we're settled on; blue fades in on arrival (settle), off while traveling —
  // decoupled from exact cursor pos so orbiting the edges never makes it flicker.
  const activeNode=byId[(mp<0.5?a.cursor:b.cursor)];
  if(activeNode && settle>0.03){ const [ax,ay]=P(activeNode.x,activeNode.y);
    ctx.globalAlpha=settle; rr(ctx,ax-3,ay-3,activeNode.w*scale+6,activeNode.h*scale+6,(radOf(activeNode)+3)*scale);
    ctx.lineWidth=(activeNode.color?5:3.6)*scale; ctx.strokeStyle=activeNode.color?'#ffffff':TH.activeOutline;
    ctx.stroke(); ctx.globalAlpha=1; }
  if(activeNode && detail[activeNode.id] && settle>0.03){
    const d=detail[activeNode.id]; const [ex,ey]=P(activeNode.x,activeNode.y+activeNode.h+16);
    ctx.globalAlpha=settle; ctx.drawImage(d.cv,ex,ey,d.w*scale,d.h*scale); ctx.globalAlpha=1; }

  const [csx,csy]=P(curx,cury); drawCursor(csx,csy);
}
function drawCursor(x,y){ctx.save();ctx.translate(x,y);ctx.scale(1.2,1.2);
  ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(0,22);ctx.lineTo(6,17);ctx.lineTo(10,25);ctx.lineTo(13,23.5);ctx.lineTo(9,15.5);ctx.lineTo(16,15);ctx.closePath();
  ctx.fillStyle='#fff';ctx.strokeStyle='#000';ctx.lineWidth=1.3;ctx.fill();ctx.stroke();ctx.restore();}
window.renderFrame=renderFrame;
async function init(){
  await Promise.all(NODES.filter(n=>n.image).map(n=>new Promise(res=>{
    const im=new Image(); im.onload=()=>{imgs[n.id]=im;res();}; im.onerror=()=>res(); im.src=n.image; })));
  buildScene(); buildDetails(); window.__ready=true;
}
init();
</script></body></html>"""


DEFAULT_THEME = {
    "bg": "#0a0a0a", "node": "#141414", "nodeStroke": "#3a3a3a",
    "activeOutline": "#3b82f6", "text": "#eaeaea", "detailBg": "#111318", "line": "#4a4a4a",
}


# ════════════════════════════════════════════════════════════════════════════
# 3) TREE + CAMERA builders  (from a flat topic list; camera = overview -> focus)
# ════════════════════════════════════════════════════════════════════════════
def tree_from_topics(title: str, topics: list) -> dict:
    """topics = [{label, detail, ...}] -> a mindmap dict (root + one branch per topic).
    Node ids are 't0','t1',... so cameras can reference them."""
    children = [{"id": f"t{i}", "label": t.get("label", f"Topic {i+1}"),
                 "detail": t.get("detail", "")} for i, t in enumerate(topics)]
    return {"title": title, "theme": DEFAULT_THEME,
            "root": {"id": "root", "label": title, "children": children}}


def overview_to_focus_camera(focus_id: str, seg_dur: float = 12.0, move_in: float = 2.2) -> dict:
    """A short segment: start on the whole-map overview, glide to `focus_id`, dwell."""
    return {"fps": 30, "duration": float(seg_dur), "move_dur": 1.15,
            "keys": [
                {"t": 0.0,           "focus": "root",     "scale": 0.82, "cursor": "root"},
                {"t": move_in,       "focus": focus_id,   "scale": 1.42, "cursor": focus_id},
                {"t": float(seg_dur),"focus": focus_id,   "scale": 1.42, "cursor": focus_id},
            ]}


def tree_from_central(central: str, points: list) -> dict:
    """A small mindmap: one central idea + a few colored sub-points (ids p0,p1,...)."""
    children = [{"id": f"p{i}", "label": p.get("label", f"Point {i+1}"),
                 "detail": p.get("detail", ""), "_color": TREE_COLORS[i % len(TREE_COLORS)]}
                for i, p in enumerate(points)]
    return {"title": central, "theme": DEFAULT_THEME,
            "root": {"id": "root", "label": central, "children": children}}


def walkthrough_camera(n_points: int, seg_dur: float = 20.0) -> dict:
    """Overview, then walk the cursor/camera through each sub-point in turn (dwelling
    on each), then pull back to overview — reads the breakdown as the narrator speaks."""
    seg_dur = float(seg_dur)
    keys = [{"t": 0.0, "focus": "root", "scale": 0.80, "cursor": "root"}]
    if n_points > 0:
        intro, outro = 1.8, 2.2
        span = max(3.0, seg_dur - intro - outro)
        per = span / n_points
        t = intro
        for i in range(n_points):
            keys.append({"t": round(t, 2), "focus": f"p{i}", "scale": 1.42, "cursor": f"p{i}"})
            t += per
    keys.append({"t": seg_dur, "focus": "root", "scale": 0.84, "cursor": "root"})
    return {"fps": 30, "duration": seg_dur, "move_dur": 1.1, "keys": keys}


def render_varied(jobs: list, fps: int = 30, w: int = 1920, h: int = 1080,
                  workdir: Path = None, workers: int = 4) -> list:
    """Render MANY DIFFERENT mindmaps (each its own tree) -> mp4s, in parallel.
    jobs = [(mindmap, camera, out_path)]."""
    from concurrent.futures import ThreadPoolExecutor
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    prepared = []
    for i, (mm, cam, out) in enumerate(jobs):
        html = _build_html(mm, w, h, cam)
        prepared.append((html, cam, Path(out), tmp / f"frames_{i:03d}"))
    workers = max(1, min(workers, len(prepared)))
    chunks = [prepared[i::workers] for i in range(workers)]

    def do(chunk):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu"])
            clip = {"x": 0, "y": 0, "width": w, "height": h}
            for html, cam, out, fdir in chunk:
                fdir.mkdir(parents=True, exist_ok=True)
                # FRESH page per job — reusing a page + set_content leaves window.__ready
                # true from the previous clip, so the new scene renders empty (50KB clips).
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
                page.set_content(html, wait_until="load")
                page.wait_for_function("window.__ready===true", timeout=20000)
                total = max(1, int(round(cam["duration"] * fps)))
                for k in range(total):
                    page.evaluate("(t)=>window.renderFrame(t)", k / fps)
                    page.screenshot(path=str(fdir / f"f_{k:05d}.jpg"), type="jpeg", quality=92, clip=clip)
                _frames_to_mp4(fdir, out, fps)
                page.close()
            browser.close()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do, chunks))
    return [Path(out) for _, _, out in jobs]


# ── Mindmap-only: one BIG tree (topics + sub-points) animated across the whole video ──
def big_tree(title: str, topics: list) -> dict:
    """root -> topics (t0,t1,...) -> sub-points (t0p0,...). 3-level mindmap."""
    children = []
    for i, tp in enumerate(topics):
        col = TREE_COLORS[i % len(TREE_COLORS)]
        pts = [{"id": f"t{i}p{j}", "label": p.get("label", ""), "detail": p.get("detail", ""),
                "_color": col} for j, p in enumerate(tp.get("points") or [])]
        children.append({"id": f"t{i}", "label": tp.get("label", f"Topic {i+1}"),
                         "detail": "", "_color": col, "children": pts})
    return {"title": title, "theme": DEFAULT_THEME,
            "root": {"id": "root", "label": title, "children": children}}


def full_camera(topics: list, times: list, total: float) -> dict:
    """A full-length camera: open on the whole map, then walk topic-by-topic (and
    through each topic's sub-points) in sync with the SRT-derived `times`."""
    n = len(topics)
    n_leaves = max(1, sum(max(1, len(t.get("points") or [])) for t in topics))
    ov = max(0.11, min(0.7, 1100.0 / (n_leaves * 250.0)))   # overview scale that fits the map
    keys = [{"t": 0.0, "focus": "root", "scale": ov, "cursor": "root"}]
    for i in range(n):
        ti = times[i]
        tnext = times[i + 1] if i + 1 < n else total
        span = max(2.0, tnext - ti)
        keys.append({"t": round(ti, 2), "focus": f"t{i}", "scale": 1.02, "cursor": f"t{i}"})
        pts = topics[i].get("points") or []
        if pts:
            t0 = ti + span * 0.30
            per = (span * 0.70) / len(pts)
            for j in range(len(pts)):
                keys.append({"t": round(t0 + j * per, 2), "focus": f"t{i}p{j}",
                             "scale": 1.42, "cursor": f"t{i}p{j}"})
    keys.append({"t": float(total), "focus": keys[-1]["focus"], "scale": keys[-1]["scale"],
                 "cursor": keys[-1]["cursor"]})
    for k in range(1, len(keys)):                 # strictly increasing, capped at total
        if keys[k]["t"] <= keys[k - 1]["t"]:
            keys[k]["t"] = min(total, keys[k - 1]["t"] + 0.25)
    return {"fps": 30, "duration": float(total), "move_dur": 1.2, "keys": keys}


def render_full(mindmap: dict, camera: dict, out_mp4: Path, fps: int = 30, w: int = 1920,
                h: int = 1080, chunk_sec: float = 45.0, workers: int = 6, workdir: Path = None) -> Path:
    """Render ONE long continuous camera over a big mindmap by splitting the timeline
    into chunks rendered in parallel (each frame at its absolute t), then concat."""
    from concurrent.futures import ThreadPoolExecutor
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    html = _build_html(mindmap, w, h, camera)
    total_frames = max(1, int(round(camera["duration"] * fps)))
    cf = max(1, int(chunk_sec * fps))
    chunks = []
    a, ci = 0, 0
    while a < total_frames:
        b = min(total_frames, a + cf)
        chunks.append((ci, a, b)); a = b; ci += 1
    workers = max(1, min(workers, len(chunks)))
    groups = [chunks[i::workers] for i in range(workers)]

    def do(group):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu"])
            pg = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            pg.set_content(html, wait_until="load")
            pg.wait_for_function("window.__ready===true", timeout=20000)
            clip = {"x": 0, "y": 0, "width": w, "height": h}
            for (ci, a, b) in group:
                fdir = tmp / f"c_{ci:04d}"
                fdir.mkdir(exist_ok=True)
                for k in range(a, b):
                    pg.evaluate("(t)=>window.renderFrame(t)", k / fps)
                    pg.screenshot(path=str(fdir / f"f_{k-a:05d}.jpg"), type="jpeg", quality=92, clip=clip)
                _frames_to_mp4(fdir, tmp / f"chunk_{ci:04d}.mp4", fps)
            br.close()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do, groups))
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{(tmp / f'chunk_{i:04d}.mp4').resolve()}'\n" for i in range(len(chunks))), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_mp4)],
                   check=True, capture_output=True)
    return Path(out_mp4)


# ════════════════════════════════════════════════════════════════════════════
# 4) RENDER  —  Chromium frame-by-frame -> JPEG -> ffmpeg. The scene is built ONCE
#    per page; many camera timelines reuse it (setCamera), rendered in parallel.
# ════════════════════════════════════════════════════════════════════════════
def _img_data_uri(path: str) -> str:
    """Read a local image and return a base64 data URI (so the headless page can draw
    it without file:// access)."""
    import base64
    import mimetypes
    p = Path(path)
    if not p.exists():
        return ""
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _build_html(mindmap: dict, w: int, h: int, cam0: dict, theme: dict = None) -> str:
    root = apply_layout(mindmap)
    nodes = flatten(root)
    for n in nodes:                       # inline node images as data URIs
        if n.get("image"):
            n["image"] = _img_data_uri(n["image"])
    theme = {**DEFAULT_THEME, **(mindmap.get("theme") or {}), **(theme or {})}
    return (MINDMAP_HTML
            .replace("__W__", str(w)).replace("__H__", str(h))
            .replace("__NODES__", json.dumps(nodes))
            .replace("__CAM__", json.dumps(cam0))
            .replace("__THEME__", json.dumps(theme))
            .replace("__LAYOUT__", json.dumps(mindmap.get("layout", "tree"))))


def _frames_to_mp4(frames_dir: Path, out_mp4: Path, fps: int) -> None:
    subprocess.run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(frames_dir / "f_%05d.jpg"),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-color_range", "tv", "-r", str(fps), str(out_mp4)],
                   check=True, capture_output=True)


def _render_chunk(html: str, chunk: list, w: int, h: int, fps: int) -> None:
    """chunk = [(camera, out_mp4, frames_dir)] rendered by one browser (scene built once)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu"])
        page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        page.set_content(html, wait_until="load")
        page.wait_for_function("window.__ready===true", timeout=20000)
        clip = {"x": 0, "y": 0, "width": w, "height": h}
        for camera, out_mp4, fdir in chunk:
            fdir.mkdir(parents=True, exist_ok=True)
            page.evaluate("(c)=>window.setCamera(c)", camera)
            total = max(1, int(round(camera["duration"] * fps)))
            for i in range(total):
                page.evaluate("(t)=>window.renderFrame(t)", i / fps)
                page.screenshot(path=str(fdir / f"f_{i:05d}.jpg"), type="jpeg", quality=92, clip=clip)
            _frames_to_mp4(fdir, out_mp4, fps)
        browser.close()


def render_segments(mindmap: dict, cameras: list, out_paths: list,
                    fps: int = 30, w: int = 1920, h: int = 1080,
                    workdir: Path = None, workers: int = 3) -> list:
    """Render many camera timelines over the SAME mindmap into mp4s, in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    html = _build_html(mindmap, w, h, cameras[0] if cameras else {"fps": fps, "duration": 1, "keys": []})
    jobs = [(cameras[i], Path(out_paths[i]), tmp / f"frames_{i:02d}") for i in range(len(cameras))]
    workers = max(1, min(workers, len(jobs)))
    chunks = [jobs[i::workers] for i in range(workers)]           # round-robin split
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda c: _render_chunk(html, c, w, h, fps), chunks))
    return [Path(p) for p in out_paths]


def render_mindmap(mindmap: dict, camera: dict, out_mp4: Path,
                   fps: int = 30, w: int = 1920, h: int = 1080,
                   workdir: Path = None, theme: dict = None) -> Path:
    """Render a single mindmap animation (thin wrapper over render_segments)."""
    render_segments(mindmap, [camera], [out_mp4], fps=fps, w=w, h=h, workdir=workdir, workers=1)
    return Path(out_mp4)


# ════════════════════════════════════════════════════════════════════════════
# DEMO  —  hand-authored "Call Money by Name" example (MVP, ~16s)
# ════════════════════════════════════════════════════════════════════════════
def _demo():
    mindmap = {
        "title": "Call Money by Name",
        "theme": DEFAULT_THEME,
        "root": {
            "id": "root", "label": "Call Money by Name & It Comes to You — Florence Scovel Shinn",
            "children": [
                {"id": "premise", "label": "Premise",
                 "detail": "Money is a spiritual substance that responds to being addressed directly."},
                {"id": "affirm", "label": "Participation Affirmation",
                 "detail": "You affirm your share in the abundance already present."},
                {"id": "call", "label": "CALL MONEY BY NAME",
                 "detail": "Speak to it and it comes to you — name it, and it answers."},
            ],
        },
    }
    camera = {
        "fps": 30, "duration": 16.0,
        "keys": [
            {"t": 0.0,  "focus": "root",    "scale": 0.85, "cursor": "root",    "open": False},
            {"t": 2.0,  "focus": "premise", "scale": 1.40, "cursor": "premise", "open": True},
            {"t": 6.5,  "focus": "affirm",  "scale": 1.40, "cursor": "affirm",  "open": True},
            {"t": 11.0, "focus": "call",    "scale": 1.40, "cursor": "call",    "open": True},
            {"t": 16.0, "focus": "root",    "scale": 0.85, "cursor": "root",    "open": False},
        ],
    }
    out = Path(__file__).parent / "output" / "_mindmap_demo.mp4"
    out.parent.mkdir(exist_ok=True)
    print("rendering mindmap demo ->", out)
    render_mindmap(mindmap, camera, out)
    print("done")


# ════════════════════════════════════════════════════════════════════════════
# XMIND-STYLE renderer  —  looks like a real screen-recording of an XMind map:
#   dark theme, borderless text topics with hairline separators + elbow connectors,
#   full-sentence / numbered detail nodes, NO ZOOM (pan/scroll only), a moving
#   cursor that SELECTS text (blue highlight sweep) on the node it lands on.
# Input is a nested tree {label, children:[...]}. Layout is done in JS (measures
# the wrapped text) so long paragraphs size themselves.
# ════════════════════════════════════════════════════════════════════════════
XMIND_THEME = {
    "bg": "#070809", "text0": "#ffffff", "text1": "#eef1f6", "text2": "#c6ccd6",
    "line": "#5a616b", "sep": "#20242b", "sel": "#2f6bd8", "outline": "#3b82f6",
    "box": "#0d1013", "boxStroke": "#262b33",
}

XMIND_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#070809;overflow:hidden}canvas{display:block}
</style></head><body><canvas id="c"></canvas><script>
const W=__W__,H=__H__,DUR=__DURATION__,TH=__THEME__,TREE=__TREE__,SEED=__SEED__,CAM=__CAM__;
const cv=document.getElementById('c');cv.width=W;cv.height=H;const ctx=cv.getContext('2d');
const SS=2,FONT='-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif';
// seeded PRNG (mulberry32) — deterministic per render but different every segment
function mk(seed){let a=seed>>>0;return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
const RND=mk(SEED); function rr01(){return RND();} function rrange(a,b){return a+(b-a)*RND();} function ri(a,b){return Math.floor(rrange(a,b+1));}
function lerp(a,b,p){return a+(b-a)*p;}
function clamp(x,a,b){return Math.max(a,Math.min(b,x));}
function smoothstep(a,b,x){if(b<=a)return x<a?0:1;let t=clamp((x-a)/(b-a),0,1);return t*t*(3-2*t);}
function easeIO(p){return p<0.5?4*p*p*p:1-Math.pow(-2*p+2,3)/2;}
function rr(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath();}
const mc=document.createElement('canvas').getContext('2d');
function wrap(text,font,maxW){mc.font=font;const words=(text||'').split(/\s+/);const lines=[];let cur='';for(const w of words){const t=(cur+' '+w).trim();if(mc.measureText(t).width>maxW&&cur){lines.push(cur);cur=w;}else cur=t;}if(cur)lines.push(cur);return lines;}
function styleOf(d){
  if(d===0)return{fs:37,bold:1,maxW:440,col:TH.text0,lh:45};
  if(d===1)return{fs:31,bold:1,maxW:470,col:TH.text1,lh:39};
  return{fs:25,bold:0,maxW:520,col:TH.text2,lh:33};
}
const PADX=14,PADY=9,VGAP=24,COLGAP=110;
let nodes=[];
function build(node,depth,num){
  const st=styleOf(depth);const font=(st.bold?'700 ':'400 ')+st.fs+'px '+FONT;
  const prefix=(depth>=2&&num)?num+'. ':'';
  const lines=wrap(prefix+(node.label||''),font,st.maxW);
  mc.font=font;let ww=0;const lw=[];lines.forEach(L=>{const w=mc.measureText(L).width;lw.push(w);ww=Math.max(ww,w);});
  node._st=st;node._font=font;node._lines=lines;node._lw=lw;
  node.w=Math.ceil(ww+PADX*2);node.h=Math.ceil(lines.length*st.lh+PADY*2);node.depth=depth;
  nodes.push(node);const kids=node.children||[];node._kids=kids;
  kids.forEach((c,i)=>build(c,depth+1,i+1));
}
build(TREE,0,0);
let colMax={};nodes.forEach(n=>{colMax[n.depth]=Math.max(colMax[n.depth]||0,n.w);});
let colX={0:0};for(let d=1;d<=8;d++)colX[d]=(colX[d-1]||0)+(colMax[d-1]||0)+COLGAP;
nodes.forEach(n=>n.x=colX[n.depth]||0);
function place(node,top){const kids=node._kids;if(!kids||!kids.length){node.y=top;return top+node.h+VGAP;}
  let y=top;const t0=top;kids.forEach(c=>{y=place(c,y);});const cb=y-VGAP;node.y=(t0+cb)/2-node.h/2;
  return Math.max(y,node.y+node.h+VGAP);}
place(TREE,0);
let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9;
nodes.forEach(n=>{minX=Math.min(minX,n.x);minY=Math.min(minY,n.y);maxX=Math.max(maxX,n.x+n.w);maxY=Math.max(maxY,n.y+n.h);});
const PAD=150;minX-=PAD;minY-=PAD;maxX+=PAD;maxY+=PAD;
const sceneW=Math.ceil(maxX-minX),sceneH=Math.ceil(maxY-minY);
const N=nodes.length;
// ---- BIG-MAP moving camera (Rodriguez / XMind style): one large map, the camera
//      travels + zooms to each topic as the narrator reaches it (CAM keyframes). ----
function subBB(n){let x0=n.x,y0=n.y,x1=n.x+n.w,y1=n.y+n.h;(n._kids||[]).forEach(c=>{const b=subBB(c);
  x0=Math.min(x0,b[0]);y0=Math.min(y0,b[1]);x1=Math.max(x1,b[2]);y1=Math.max(y1,b[3]);});n._bb=[x0,y0,x1,y1];return n._bb;}
subBB(TREE);
function fitScale(bw,bh){return clamp(Math.min((W-200)/bw,(H-200)/bh),0.20,1.05);}
let CAMKEYS=null;
if(CAM&&CAM.keys&&CAM.keys.length){
  const topics=TREE._kids||[];
  CAMKEYS=CAM.keys.map(k=>{const n=(k.ni>=0&&topics[k.ni])?topics[k.ni]:TREE;const bb=n._bb;
    return{t:k.t,cx:(bb[0]+bb[2])/2,cy:(bb[1]+bb[3])/2,sc:fitScale(bb[2]-bb[0],bb[3]-bb[1]),node:n};});
}
function camAt(t){const ks=CAMKEYS;if(t<=ks[0].t)return{cx:ks[0].cx,cy:ks[0].cy,sc:ks[0].sc,node:ks[0].node,mp:null,a:ks[0],b:ks[0]};
  for(let i=0;i<ks.length-1;i++){if(t<ks[i+1].t){const D=ks[i+1].t-ks[i].t,md=Math.min((CAM.move_dur||1.6),D*0.8);
    const mp=clamp((t-(ks[i+1].t-md))/md,0,1),e=easeIO(mp);
    return{cx:lerp(ks[i].cx,ks[i+1].cx,e),cy:lerp(ks[i].cy,ks[i+1].cy,e),sc:lerp(ks[i].sc,ks[i+1].sc,e),
           node:(mp<0.5?ks[i].node:ks[i+1].node),mp,a:ks[i],b:ks[i+1]};}}
  const L=ks[ks.length-1];return{cx:L.cx,cy:L.cy,sc:L.sc,node:L.node,mp:null,a:L,b:L};}
// ---- STATIC camera: fit the whole little map on screen ONCE, then never move it
//      (only the cursor moves — reads far more like a real screen recording) ----
const MARGIN=96;
let SCALE=Math.min((W-2*MARGIN)/sceneW,(H-2*MARGIN)/sceneH,1.0);SCALE=Math.max(SCALE,0.32);
const PANx=(W-sceneW*SCALE)/2,PANy=(H-sceneH*SCALE)/2;
function S(wx,wy){return[(wx-minX)*SCALE+PANx,(wy-minY)*SCALE+PANy];}
// ---- cursor plan (Rodriguez / Twelve-Labs analysis): STRAIGHT lines, CONSTANT
//      velocity (linear, no easing, no wobble), cursor PERFECTLY STILL while it
//      dwells. Some nodes get a FULL-node highlight that snaps on instantly and
//      holds while focused. Positions are threaded so the cursor never jumps. ----
const SPEED=515;                                    // slower, calmer travel (not too fast)
function boxTarget(n){return[n.x+Math.min(n.w*0.60,PADX+n._lw[0]*0.66),n.y+n.h*0.5];}
function offPoint(){const n=nodes[ri(0,N-1)];return[n.x+rrange(-70,n.w+70),n.y+rrange(-46,n.h+46)];}
function makePath(from,to){                          // curved, wandering polyline (not a dead-straight line)
  const dx=to[0]-from[0],dy=to[1]-from[1],pl=Math.hypot(dx,dy)||1,nx=-dy/pl,ny=dx/pl;
  const segs=ri(1,2);const pts=[from.slice()];
  for(let i=1;i<=segs;i++){const f=i/(segs+1)+rrange(-0.09,0.09);const off=rrange(-1,1)*Math.min(52,pl*0.15);
    pts.push([from[0]+dx*f+nx*off,from[1]+dy*f+ny*off]);}
  pts.push(to.slice());return pts;}
function plen(p){let l=0;for(let i=1;i<p.length;i++)l+=Math.hypot((p[i][0]-p[i-1][0])*SCALE,(p[i][1]-p[i-1][1])*SCALE);return l;}
function pathPos(p,mp){let segl=[],tot=0;for(let i=1;i<p.length;i++){const l=Math.hypot(p[i][0]-p[i-1][0],p[i][1]-p[i-1][1]);segl.push(l);tot+=l;}
  let d=clamp(mp,0,1)*tot;for(let i=0;i<segl.length;i++){if(d<=segl[i]||i===segl.length-1){const f=segl[i]>0?d/segl[i]:0;return[lerp(p[i][0],p[i+1][0],f),lerp(p[i][1],p[i+1][1],f)];}d-=segl[i];}return p[p.length-1];}
const rootN=nodes[0];
let START=[rootN.x+rootN.w*0.5,rootN.y+rootN.h*0.5];
const STOPS=[];let tc=rrange(0.2,0.5),lastN=-1,fromPos=START.slice();
while(tc<DUR-0.3){
  const box=RND()<0.68;let ni=-1,T,sel=null,dwell,endPos,still=false;
  if(box){do{ni=ri(0,N-1);}while(N>1&&ni===lastN);lastN=ni;const n=nodes[ni],st=n._st,nl=n._lines.length;
    if(RND()<0.55){                                  // this node gets a SLOW, progressive drag-select
      let lines,lastFrac;
      if(RND()<0.5){lines=nl;lastFrac=1.0;}          // sometimes the whole node
      else{lines=ri(1,nl);lastFrac=(lines<nl)?rrange(0.6,1.0):rrange(0.35,1.0);}   // sometimes just a bit
      T=[n.x+PADX+6,n.y+PADY+st.lh*0.55];            // start of the text (drag begins here)
      endPos=[n.x+PADX+n._lw[lines-1]*lastFrac,n.y+PADY+(lines-1)*st.lh+st.lh*0.55];
      dwell=rrange(2.4,5.0);sel={lines,lastFrac,swDur:clamp(lines*rrange(0.55,0.95)+rrange(0.3,0.7),0.9,3.4)};
    }else{T=boxTarget(n);endPos=T;still=RND()<0.4;dwell=still?rrange(3.0,6.5):rrange(1.4,3.2);}  // sometimes just REST, dead still
  }else{T=offPoint();endPos=T;still=RND()<0.3;dwell=still?rrange(2.0,4.5):rrange(0.5,1.4);}
  const path=makePath(fromPos,T);const travel=clamp(plen(path)/SPEED,0.6,2.3);
  const tArr=tc+travel,tEnd=tArr+dwell;if(sel)sel.swStart=tArr+0.35;
  STOPS.push({box,ni,T,path,tc,tArr,tEnd,sel,still,ph:rrange(0,6.28)});
  fromPos=endPos.slice();tc=tEnd;}
const scene=document.createElement('canvas');scene.width=sceneW*SS;scene.height=sceneH*SS;
function buildScene(){const c=scene.getContext('2d');c.scale(SS,SS);c.translate(-minX,-minY);
  c.strokeStyle=TH.line;c.lineWidth=2;c.lineJoin='round';
  nodes.forEach(n=>{(n._kids||[]).forEach(ch=>{const x1=n.x+n.w,y1=n.y+n.h/2,x2=ch.x,y2=ch.y+ch.h/2,mx=(x1+x2)/2;
    c.beginPath();c.moveTo(x1,y1);c.lineTo(mx,y1);c.lineTo(mx,y2);c.lineTo(x2,y2);c.stroke();});});
  nodes.forEach(n=>{const st=n._st;
    rr(c,n.x-6,n.y-4,n.w+12,n.h+8,8);c.fillStyle=TH.box;c.fill();
    c.lineWidth=1.4;c.strokeStyle=TH.boxStroke;c.stroke();
    c.fillStyle=st.col;c.textBaseline='top';c.textAlign='left';c.font=n._font;
    let ty=n.y+PADY;n._lines.forEach(L=>{c.fillText(L,n.x+PADX,ty);ty+=st.lh;});});
}
function curStop(t){let si=-1;for(let i=0;i<STOPS.length;i++){if(t>=STOPS[i].tc)si=i;else break;}return si;}
function renderFrame(t){
  ctx.fillStyle=TH.bg;ctx.fillRect(0,0,W,H);ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
  if(CAMKEYS){                                   // ---- BIG-MAP moving camera ----
    const c=camAt(t);
    const camX=c.cx+Math.sin(t*0.31)*7+Math.sin(t*0.13)*4;
    const camY=c.cy+Math.cos(t*0.27)*5+Math.cos(t*0.11)*3;
    const scale=c.sc*(1+Math.sin(t*0.4)*0.005);
    const S=(wx,wy)=>[W/2+(wx-camX)*scale,H/2+(wy-camY)*scale];
    const[bx,by]=S(minX,minY);ctx.drawImage(scene,bx,by,sceneW*scale,sceneH*scale);
    const n=c.node,st=n._st;
    const dwell=(c.mp==null)?1:smoothstep(0.5,0.92,c.mp);   // highlight fades in as we settle
    if(dwell>0.02){ctx.save();ctx.globalAlpha=0.32*dwell;ctx.fillStyle=TH.sel;
      for(let i=0;i<n._lines.length;i++){const[hx,hy]=S(n.x+PADX-4,n.y+PADY+i*st.lh+st.lh*0.08);
        ctx.fillRect(hx,hy,(n._lw[i]+8)*scale,st.lh*0.9*scale);}ctx.restore();
      const[ox,oy]=S(n.x-6,n.y-4);ctx.save();ctx.globalAlpha=dwell;
      rr(ctx,ox,oy,(n.w+12)*scale,(n.h+8)*scale,8);ctx.lineWidth=2.4;ctx.strokeStyle=TH.outline;ctx.stroke();ctx.restore();}
    // cursor: ease between the two focus nodes during a move; small organic drift on dwell
    let cwx,cwy;
    if(c.mp!=null){const e=easeIO(c.mp),an=c.a.node,bn=c.b.node;
      cwx=lerp(an.x+an.w*0.5,bn.x+bn.w*0.5,e)+Math.sin(t*1.9)*6;
      cwy=lerp(an.y+an.h*0.55,bn.y+bn.h*0.55,e)+Math.cos(t*1.7)*4;
    }else{const dt=t;cwx=n.x+n.w*0.5+Math.sin(dt*0.8+n._i)*n.w*0.14+Math.sin(dt*1.9)*7;
      cwy=n.y+n.h*0.55+Math.cos(dt*0.7+n._i)*Math.max(14,n.h*0.3)+Math.cos(dt*1.7)*5;}
    const[cx,cy]=S(cwx,cwy);drawCursor(cx,cy);
    return;
  }
  ctx.drawImage(scene,PANx,PANy,sceneW*SCALE,sceneH*SCALE);   // STATIC map
  const si=curStop(t);
  if(si<0){const[cx0,cy0]=S(START[0],START[1]);drawCursor(cx0,cy0);return;}
  const s=STOPS[si];const arrived=(t>=s.tArr);
  if(s.box){const n=nodes[s.ni];const st=n._st;
    // PROGRESSIVE drag-select — sweeps in slowly; extent varies (whole node OR just a bit)
    if(arrived&&s.sel){const sp=smoothstep(s.sel.swStart,s.sel.swStart+s.sel.swDur,t);
      const fade=1-smoothstep(s.tEnd-0.3,s.tEnd,t);const shown=sp*s.sel.lines;
      if(fade>0){ctx.save();ctx.globalAlpha=0.44*fade;ctx.fillStyle=TH.sel;
        for(let i=0;i<s.sel.lines;i++){let f=clamp(shown-i,0,1);const ff=(i<s.sel.lines-1)?f:f*s.sel.lastFrac;if(ff<=0)continue;
          const[hx,hy]=S(n.x+PADX-4,n.y+PADY+i*st.lh+st.lh*0.08);ctx.fillRect(hx,hy,(n._lw[i]*ff+8)*SCALE,st.lh*0.9*SCALE);}
        ctx.restore();}}
    // blue "selected topic" outline — snaps on the instant the cursor lands
    if(arrived){const off=smoothstep(s.tEnd-0.12,s.tEnd,t);const[ox,oy]=S(n.x-6,n.y-4);
      ctx.save();ctx.globalAlpha=1-off;rr(ctx,ox,oy,(n.w+12)*SCALE,(n.h+8)*SCALE,8);
      ctx.lineWidth=2.4;ctx.strokeStyle=TH.outline;ctx.stroke();ctx.restore();}}
  // cursor: organic path; while drag-selecting it becomes an I-BEAM and follows the sweep;
  // sometimes it just RESTS dead-still; otherwise a small alive drift (not frozen, not floaty)
  let cw,ibeam=false;
  if(!arrived){cw=pathPos(s.path,(t-s.tc)/(s.tArr-s.tc));}
  else if(s.box&&s.sel){const n=nodes[s.ni],st=n._st;ibeam=true;
    if(t>=s.sel.swStart){const sp=smoothstep(s.sel.swStart,s.sel.swStart+s.sel.swDur,t);const shown=sp*s.sel.lines;
      let li=clamp(Math.floor(shown),0,s.sel.lines-1);let within=clamp(shown-li,0,1);if(li===s.sel.lines-1)within*=s.sel.lastFrac;
      cw=[n.x+PADX+n._lw[li]*within,n.y+PADY+li*st.lh+st.lh*0.55];}
    else cw=[s.T[0],s.T[1]];}
  else if(s.still){cw=[s.T[0],s.T[1]];}              // DEAD still (a real person pausing)
  else{const dt=t-s.tArr,dw=s.tEnd-s.tArr;const ramp=smoothstep(0,0.4,dt)*(1-smoothstep(dw-0.4,dw,dt));
    cw=[s.T[0]+(Math.sin(dt*0.8+s.ph)*6+Math.sin(dt*0.29+s.ph)*3)*ramp,
        s.T[1]+(Math.cos(dt*0.7+s.ph)*4+Math.cos(dt*0.24)*2)*ramp];}
  if(!s.still)cw=[cw[0]+Math.sin(t*7.3+s.ph)*0.6,cw[1]+Math.cos(t*6.7)*0.5];   // faint life tremor (not while resting)
  const[cx,cy]=S(cw[0],cw[1]);
  if(ibeam)drawIBeam(cx,cy,nodes[s.ni]._st.lh*SCALE);else drawCursor(cx,cy);
}
function drawCursor(x,y){ctx.save();ctx.translate(x,y);ctx.scale(1.0,1.0);
  ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(0,22);ctx.lineTo(6,17);ctx.lineTo(10,25);ctx.lineTo(13,23.5);ctx.lineTo(9,15.5);ctx.lineTo(16,15);ctx.closePath();
  ctx.fillStyle='#fff';ctx.strokeStyle='#000';ctx.lineWidth=1.3;ctx.fill();ctx.stroke();ctx.restore();}
function drawIBeam(x,y,h){h=Math.max(18,h*0.92);const w=Math.max(3.5,h*0.16);ctx.save();ctx.translate(x,y);ctx.lineCap='round';
  ctx.strokeStyle='#000';ctx.lineWidth=4;                       // dark halo for contrast on any bg
  ctx.beginPath();ctx.moveTo(0,-h/2);ctx.lineTo(0,h/2);ctx.moveTo(-w,-h/2);ctx.lineTo(w,-h/2);ctx.moveTo(-w,h/2);ctx.lineTo(w,h/2);ctx.stroke();
  ctx.strokeStyle='#fff';ctx.lineWidth=2;
  ctx.beginPath();ctx.moveTo(0,-h/2);ctx.lineTo(0,h/2);ctx.moveTo(-w,-h/2);ctx.lineTo(w,-h/2);ctx.moveTo(-w,h/2);ctx.lineTo(w,h/2);ctx.stroke();ctx.restore();}
window.renderFrame=renderFrame;
function init(){buildScene();window.__ready=true;}
init();
</script></body></html>"""


def _build_xmind_html(tree: dict, dur: float, w: int = 1920, h: int = 1080,
                      theme: dict = None, seed: int = None, camera: dict = None) -> str:
    th = theme or XMIND_THEME
    tree_json = json.dumps(tree)
    if seed is None:                              # content-derived → different every segment
        seed = zlib.crc32(tree_json.encode("utf-8")) & 0xffffffff
    return (XMIND_HTML
            .replace("__W__", str(w)).replace("__H__", str(h))
            .replace("__DURATION__", str(float(dur)))
            .replace("__SEED__", str(int(seed)))
            .replace("__CAM__", json.dumps(camera) if camera else "null")
            .replace("__THEME__", json.dumps(th))
            .replace("__TREE__", tree_json))


def render_xmind_segments(jobs: list, fps: int = 30, w: int = 1920, h: int = 1080,
                          workdir: Path = None, workers: int = 4) -> list:
    """Render many xmind-style segments in parallel. jobs = [(tree, dur, out_path)]."""
    from concurrent.futures import ThreadPoolExecutor
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    prepared = []
    for i, (tree, dur, out) in enumerate(jobs):
        prepared.append((_build_xmind_html(tree, dur, w, h), float(dur), Path(out), tmp / f"xf_{i:03d}"))
    workers = max(1, min(workers, len(prepared)))
    chunks = [prepared[i::workers] for i in range(workers)]

    def do(chunk):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu"])
            clip = {"x": 0, "y": 0, "width": w, "height": h}
            for html, dur, out, fdir in chunk:
                fdir.mkdir(parents=True, exist_ok=True)
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
                page.set_content(html, wait_until="load")
                page.wait_for_function("window.__ready===true", timeout=20000)
                total = max(1, int(round(dur * fps)))
                for k in range(total):
                    page.evaluate("(t)=>window.renderFrame(t)", k / fps)
                    page.screenshot(path=str(fdir / f"f_{k:05d}.jpg"), type="jpeg", quality=92, clip=clip)
                _frames_to_mp4(fdir, out, fps)
                page.close()
            browser.close()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do, chunks))
    return [Path(out) for _, _, out in jobs]


def render_xmind_full(tree: dict, camera: dict, out_mp4: Path, fps: int = 30, w: int = 1920,
                      h: int = 1080, chunk_sec: float = 45.0, workers: int = 6,
                      workdir: Path = None) -> Path:
    """Render ONE long continuous BIG-MAP camera over an xmind tree (camera travels +
    zooms to each topic per its keyframe time), chunked in parallel, then concat."""
    from concurrent.futures import ThreadPoolExecutor
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    dur = float(camera["duration"])
    html = _build_xmind_html(tree, dur, w, h, camera=camera)
    total_frames = max(1, int(round(dur * fps)))
    cf = max(1, int(chunk_sec * fps))
    chunks, a, ci = [], 0, 0
    while a < total_frames:
        b = min(total_frames, a + cf)
        chunks.append((ci, a, b)); a = b; ci += 1
    workers = max(1, min(workers, len(chunks)))
    groups = [chunks[i::workers] for i in range(workers)]

    def do(group):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu"])
            pg = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            pg.set_content(html, wait_until="load")
            pg.wait_for_function("window.__ready===true", timeout=20000)
            clip = {"x": 0, "y": 0, "width": w, "height": h}
            for (ci, a, b) in group:
                fdir = tmp / f"c_{ci:04d}"
                fdir.mkdir(exist_ok=True)
                for k in range(a, b):
                    pg.evaluate("(t)=>window.renderFrame(t)", k / fps)
                    pg.screenshot(path=str(fdir / f"f_{k-a:05d}.jpg"), type="jpeg", quality=92, clip=clip)
                _frames_to_mp4(fdir, tmp / f"chunk_{ci:04d}.mp4", fps)
            br.close()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do, groups))
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{(tmp / f'chunk_{i:04d}.mp4').resolve()}'\n" for i in range(len(chunks))), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_mp4)],
                   check=True, capture_output=True)
    return Path(out_mp4)


if __name__ == "__main__":
    _demo()
