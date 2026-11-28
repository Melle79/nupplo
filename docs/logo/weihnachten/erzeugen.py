"""Baut die Weihnachtsfassungen des Logos aus den Originalen in docs/logo/.

    python3 docs/logo/weihnachten/erzeugen.py <Zielordner>

Erzeugt Fassung A (gewählt, Sven 22.11.2026) und B (grüner Baum mit
Kugeln, verworfen). Nur A liegt hier im Repo."""
import re, sys, os
SRC=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..")
OUT=sys.argv[1]
FARBEN=[("#00963E","#00702E"),("#0057A6","#003F7A"),("#D01012","#A50D0F"),("#FFCF00","#E0B400")]  # unten -> oben, wie im Original
n=[0]
def stein(x0,x1,y0,y1,farbe,dunkel,stroke=None):
    h=y1-y0; R=0.4*h; r=0.2*h
    R=min(R,(x1-x0)/2); 
    d=(f"M{x0+R:.3f},{y0:.3f} H{x1-R:.3f} Q{x1:.3f},{y0:.3f} {x1:.3f},{y0+R:.3f} V{y1-r:.3f} "
       f"Q{x1:.3f},{y1:.3f} {x1-r:.3f},{y1:.3f} H{x0+r:.3f} Q{x0:.3f},{y1:.3f} {x0:.3f},{y1-r:.3f} "
       f"V{y0+R:.3f} Q{x0:.3f},{y0:.3f} {x0+R:.3f},{y0:.3f} Z")
    n[0]+=1; cid=f"wb{n[0]}"
    s=f'<clipPath id="{cid}"><path d="{d}"/></clipPath><path d="{d}" fill="{farbe}"/>'
    s+=f'<rect x="{x0:.3f}" y="{y1-0.3*h:.3f}" width="{x1-x0:.3f}" height="{0.3*h:.3f}" fill="{dunkel}" clip-path="url(#{cid})"/>'
    if stroke: s+=f'<path d="{d}" fill="none" stroke="#1D1D1B" stroke-width="{stroke:.3f}"/>'
    return s
def kugel(cx,cy,r,farbe,stroke=None):
    s=f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="{farbe}"'
    if stroke: s+=f' stroke="#1D1D1B" stroke-width="{stroke:.3f}"'
    return s+'/>'
def stern(cx,cy,R,farbe,stroke=None):
    import math
    pts=[]
    for i in range(10):
        rr=R if i%2==0 else R*0.45
        a=-math.pi/2+i*math.pi/5
        pts.append(f"{cx+rr*math.cos(a):.3f},{cy+rr*math.sin(a):.3f}")
    s=f'<polygon points="{" ".join(pts)}" fill="{farbe}" stroke-linejoin="round"'
    s+= f' stroke="{"#1D1D1B" if stroke else farbe}" stroke-width="{(stroke or R*0.12):.3f}"'
    return s+'/>'

def baum(x0,x1,yb,yt,stroke,farben,ornamente,sternkante=None):
    """Drei Steine, nach oben schmaler, darauf ein gelber Stern.
    Der Stern ersetzt den gelben Stein des Turms – so bleiben alle vier
    Logofarben, und die Spitze liest sich als Baum statt als Stapelturm."""
    W=x1-x0; cx=(x0+x1)/2; H=yb-yt; gap=H*0.02
    sternR=H*0.14
    h=(H-2*sternR*0.92-2*gap)/3
    s=""; lagen=[]
    for i,(b,(f,d)) in enumerate(zip([1.0,0.70,0.40],farben)):
        y1=yb-i*(h+gap); y0=y1-h
        s+=stein(cx-W*b/2,cx+W*b/2,y0,y1,f,d,stroke); lagen.append(y0)
    kr=h*0.2
    for li,fx,f in ornamente:
        s+=kugel(cx+fx*W,lagen[li]+h*0.42,kr,f,stroke*0.7 if stroke else None)
    top=lagen[-1]-gap
    # Der Stern bekommt eine dunkle Kante, wo er auf Gelb steht (Kopfleiste,
    # Symbol) – ohne sie verschwindet er dort.
    s+=stern(cx,top-sternR*0.9,sternR,"#FFCF00",sternkante or (stroke*2.6 if stroke else None))
    return s
def baum_a(x0,x1,yb,yt,stroke=None,sk=None):
    return baum(x0,x1,yb,yt,stroke,FARBEN[:3],[],sk)
GRUEN=("#00963E","#00702E")
def baum_b(x0,x1,yb,yt,stroke=None,sk=None):
    return baum(x0,x1,yb,yt,stroke,[GRUEN]*3,
        [(0,-0.30,"#D01012"),(0,0.0,"#FFCF00"),(0,0.30,"#0057A6"),(1,-0.15,"#0057A6"),(1,0.15,"#D01012"),(2,0.0,"#FFCF00")],sk)

orig=open(f"{SRC}/nupplo-logo.svg").read()
hell=open(f"{SRC}/nupplo-logo-hell.svg").read()
sym=open(f"{SRC}/nupplo-symbol.svg").read()
turm_re=re.compile(r'<clipPath id="n54725">.*<rect x="2642\.14775390625" y="-585[^>]*/>',re.S)
sym_re=re.compile(r'<clipPath.*</svg>',re.S)
for name,fn in (("a",baum_a),("b",baum_b)):
    for src,suff,sk in ((orig,"dunkle-schrift",16),(hell,"helle-schrift",None)):
        t=fn(2642.14775390625,3118.1158203124996,0.0,-700.638671875,None,sk)
        assert turm_re.search(src)
        open(f"{OUT}/nupplo-logo-weihnachten-{name}-{suff}.svg","w").write(turm_re.sub(lambda m:t,src))
    st=6.158021281515189
    t2=fn(314.2168648820017,709.7831351179983,808.96,226.67508263299578,st)
    open(f"{OUT}/nupplo-symbol-weihnachten-{name}.svg","w").write(sym_re.sub(lambda m:t2+"</svg>",sym))
