// Pending Figma build step for "Continuum — Console redesign"
// https://www.figma.com/design/wXLsJuJEhQz9HZDZORbnKs
//
// Not run yet: the Figma Starter plan's MCP call limit was hit before this step.
// Run it once with the Figma MCP `use_figma` tool (fileKey wXLsJuJEhQz9HZDZORbnKs)
// when the limit resets. It:
//   1. builds frame "07 Login" on the Screens page at (4620, 1300)
//   2. clones "01 Today — worklist" (6:71) to "01 Today — worklist (dark)" at (4620, 0)
//      and rebinds every colour variable to the same-named token in "Color · Dark"
//   3. shortens one overflowing ledger button label
// Node ids referenced: 3:39 Button set, 6:71 Today, 9:220 Action ledger, 9:616 Identity review.

const SANS="Atkinson Hyperlegible Next", MONO="Atkinson Hyperlegible Mono";
for (const st of ["Regular","Medium","SemiBold","Bold","Italic"]) await figma.loadFontAsync({family:SANS,style:st});
for (const st of ["Regular","Medium","SemiBold"]) await figma.loadFontAsync({family:MONO,style:st});
const cols0 = await figma.variables.getLocalVariableCollectionsAsync();
const allV = await figma.variables.getLocalVariablesAsync();
const L={},DK={},BYID={}; for (const v of allV){ BYID[v.id]=v; const c=cols0.find(c=>c.id===v.variableCollectionId).name; if(c==="Color · Light")L[v.name]=v; if(c==="Color · Dark")DK[v.name]=v; }
const TS={}; for (const s of await figma.getLocalTextStylesAsync()) TS[s.name]=s;
const P=(tok)=>figma.variables.setBoundVariableForPaint({type:'SOLID',color:{r:0,g:0,b:0}},'color',L[tok]);
async function T(txt, style="body/default", color="text/primary", w){ const t=figma.createText(); await t.setTextStyleIdAsync(TS[style].id); t.characters=txt; t.fills=[P(color)]; if(w){t.textAutoResize="HEIGHT"; t.resize(w,t.height);} return t; }
function A(dir="HORIZONTAL", o={}){ const f=figma.createAutoLayout(dir); f.fills=[]; const {gap=0,pad,px,py,pt,pb,fill,stroke,r,name,align,justify}=o; f.itemSpacing=gap; if(pad!=null){f.paddingLeft=f.paddingRight=f.paddingTop=f.paddingBottom=pad;} if(px!=null){f.paddingLeft=f.paddingRight=px;} if(py!=null){f.paddingTop=f.paddingBottom=py;} if(pt!=null)f.paddingTop=pt; if(pb!=null)f.paddingBottom=pb; if(fill) f.fills=[P(fill)]; if(stroke){f.strokes=[P(stroke)]; f.strokeWeight=1;} if(r) f.cornerRadius=r; if(name) f.name=name; if(align) f.counterAxisAlignItems=align; if(justify) f.primaryAxisAlignItems=justify; return f; }
function add(p,...k){ for(const x of k) if(x) p.appendChild(x); return p; }
function W(n,w){ n.resize(w,n.height); if(n.layoutMode==="HORIZONTAL") n.primaryAxisSizingMode="FIXED"; else n.counterAxisSizingMode="FIXED"; return n; }
function FW(n){ n.layoutSizingHorizontal="FILL"; return n; }
function rule(f,side="Bottom",tok="border/subtle"){ f.strokes=[P(tok)]; for(const s of ["Top","Bottom","Left","Right"]) f["stroke"+s+"Weight"]= s===side?1:0; return f; }
const sets={btn:await figma.getNodeByIdAsync("3:39")};
const vr=(s,p,v)=>s.children.find(c=>c.name===`${p}=${v}`);
const btn=(v,l)=>{const i=vr(sets.btn,"Variant",v).createInstance(); i.setProperties({"Label#3:7":l}); return i;};
const page = figma.root.children.find(p=>p.name==="Screens"); await figma.setCurrentPageAsync(page);

const led=await figma.getNodeByIdAsync("9:220");
for (const i of led.findAll(n=>n.type==="INSTANCE"&&n.mainComponent&&n.mainComponent.parent&&n.mainComponent.parent.id==="3:39")){ if(i.componentProperties["Label#3:7"].value==="Fix phone") i.setProperties({"Label#3:7":"Fix"}); }

// LOGIN
const f=A("HORIZONTAL",{name:"07 Login",fill:"bg/canvas"}); f.resize(1440,900); f.primaryAxisSizingMode="FIXED"; f.counterAxisSizingMode="FIXED"; f.x=4620; f.y=1300; f.clipsContent=true;
const left=A("VERTICAL",{gap:0,px:120,pt:48,pb:40}); f.appendChild(left); W(left,720); left.layoutSizingVertical="FILL";
add(left, add(A("VERTICAL",{gap:2}), await T("Continuum","title/section"), await T("Care coordination console","body/small","text/muted")));
const sp1=A("VERTICAL"); left.appendChild(sp1); sp1.layoutSizingVertical="FILL";
const form=A("VERTICAL",{gap:16}); W(form,380); left.appendChild(form);
add(form, add(A("VERTICAL",{gap:6}), await T("Sign in","title/page"), await T("Use your hospital account.","body/default","text/secondary")));
async function field(label,ph){ const g=A("VERTICAL",{gap:6}); form.appendChild(g); FW(g); g.appendChild(await T(label,"label/column","text/secondary")); const i=A("HORIZONTAL",{px:10,align:"CENTER",fill:"bg/surface",stroke:"border/strong",r:4}); i.resize(380,36); i.counterAxisSizingMode="FIXED"; g.appendChild(i); FW(i); i.appendChild(await T(ph,"body/default","text/muted")); }
await field("Work email","jordan.davis@stmarys.example"); await field("Password","••••••••••");
const sb=btn("primary","Sign in"); form.appendChild(sb); sb.layoutSizingHorizontal="FILL";
const dv=A("HORIZONTAL",{gap:10,align:"CENTER",pt:8}); form.appendChild(dv); FW(dv);
const l1=figma.createRectangle(); l1.resize(40,1); l1.fills=[P("border/subtle")]; const l2=figma.createRectangle(); l2.resize(40,1); l2.fills=[P("border/subtle")];
add(dv,l1,await T("or explore with synthetic patients","body/small","text/muted"),l2); l1.layoutSizingHorizontal="FILL"; l2.layoutSizingHorizontal="FILL";
const db=A("HORIZONTAL",{gap:8}); form.appendChild(db); FW(db); const b1=btn("secondary","As care coordinator"), b2=btn("secondary","As clinician"); add(db,b1,b2); b1.layoutSizingHorizontal="FILL"; b2.layoutSizingHorizontal="FILL";
form.appendChild(await T("Demo mode shows synthetic patients only. No real patient information.","body/small","text/muted",380));
const sp2=A("VERTICAL"); left.appendChild(sp2); sp2.layoutSizingVertical="FILL";
left.appendChild(await T("Build 2026.11.14 · EHR sandbox connected","mono/small","text/muted"));
const right=A("VERTICAL",{gap:0,px:64,pt:200,fill:"bg/surface"}); f.appendChild(right); right.layoutSizingHorizontal="FILL"; right.layoutSizingVertical="FILL"; rule(right,"Left");
right.appendChild(await T("A follow-up stays open until there is proof and a person signs it off.","title/section","text/primary",480));
const lst=A("VERTICAL",{gap:0,pt:24}); W(lst,480); right.appendChild(lst);
for (const [k,v] of [["Follow-ups","Built from discharge orders in the EHR, not retyped"],["Proof","Clinic visits and lab results matched to the order that asked for them"],["Actions","Every call and task logged as intended, sent, confirmed"],["Decisions","Closures, merges and new order types wait for a coordinator"]]){
  const r=A("HORIZONTAL",{gap:16,py:12}); lst.appendChild(r); FW(r); rule(r,"Top"); const vt=await T(v,"body/default","text/secondary"); add(r, await T(k,"body/strong","text/primary",110), vt); vt.layoutSizingHorizontal="FILL"; vt.textAutoResize="HEIGHT"; }

// DARK dashboard (Starter plan allows one variable mode, so dark is a parallel collection)
const src=await figma.getNodeByIdAsync("6:71"); const dk=src.clone(); dk.name="01 Today — worklist (dark)"; dk.x=4620; dk.y=0;
let rebound=0;
function rebind(paints){ if(!Array.isArray(paints)) return null; let ch=false; const out=paints.map(p=>{ const bv=p.boundVariables&&p.boundVariables.color; if(bv&&BYID[bv.id]){ const n=BYID[bv.id].name; if(DK[n]&&DK[n].id!==bv.id){ ch=true; rebound++; return figma.variables.setBoundVariableForPaint(p,'color',DK[n]); } } return p; }); return ch?out:null; }
for (const n of [dk, ...dk.findAll(()=>true)]){ if("fills" in n && n.fills!==figma.mixed){ const r=rebind(n.fills); if(r) n.fills=r; } if("strokes" in n){ const r=rebind(n.strokes); if(r) n.strokes=r; } }
const idf=await figma.getNodeByIdAsync("9:616");
await idf.screenshot({scale:0.6}); await f.screenshot({scale:0.5}); await dk.screenshot({scale:0.5});
return {login:f.id, dark:dk.id, rebound};
