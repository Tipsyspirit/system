/* Frontend: renders state from the backend. All numbers come from /api/rules and /api/state.
   The server is the only place that calculates XP, stats, caps and levels. */
(function(){
  var TOKEN_KEY="sw_token_v2";
  var $=function(id){return document.getElementById(id)};
  var store={
    get:function(k){try{return localStorage.getItem(k)}catch(e){return null}},
    set:function(k,v){try{localStorage.setItem(k,v)}catch(e){}},
    del:function(k){try{localStorage.removeItem(k)}catch(e){}}
  };
  var RULES=null, S=null, cur="status", step=0, picked=[], mode="in", pend=null;
  var DOMAIN_NAMES=[["Health","STR"],["Career","FOC"],["Learning","KNO"],["Wellbeing","VIT"],["Relationships","SOC"],["Creativity","KNO"]];
  var RAR_META={common:{n:"Common",c:"#b9b3cc",rule:"15 min or less"},rare:{n:"Rare",c:"#4fa8ff",rule:"15 to 60 min"},
    epic:{n:"Epic",c:"#c26bff",rule:"multi-session, needs proof"},legendary:{n:"Legendary",c:"#ffb347",rule:"weeks of work"}};
  var EVID=[["none","None"],["note","Note"],["proof","Proof"],["proof_reflection","Proof + reflection"]];
  var CLASS_THEME={
    Common:{glow:"#a8a2c4",hi:"#e4e0f5",dim:"#3a3750",line:"#e4e0f5",gr:"168,162,196",mut:"#a09db3"},
    Rare:{glow:"#2f8cff",hi:"#7cc0ff",dim:"#123b73",line:"#bfe0ff",gr:"47,140,255",mut:"#8fb0d4"},
    Epic:{glow:"#8a3ffc",hi:"#b784ff",dim:"#3b1a78",line:"#c9b8ff",gr:"138,63,252",mut:"#9d8fc4"},
    Legendary:{glow:"#ff9f1c",hi:"#ffc866",dim:"#6b3f08",line:"#ffe2a8",gr:"255,159,28",mut:"#cdb088"},
    Mythic:{glow:"#ff2e63",hi:"#ff7a9c",dim:"#6b0f2a",line:"#ffc2d1",gr:"255,46,99",mut:"#cf93a1"}
  };

  function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
  function nf(x){return String(Math.round(x*10)/10)}
  function toast(msg){var t=$("toast");t.textContent=msg;t.classList.add("show");clearTimeout(toast.h);toast.h=setTimeout(function(){t.classList.remove("show")},3800)}
  function api(method,path,body){
    var h={"Content-Type":"application/json"},tk=store.get(TOKEN_KEY);if(tk)h.Authorization="Bearer "+tk;
    return fetch("/api"+path,{method:method,headers:h,body:body?JSON.stringify(body):undefined}).then(function(r){
      return r.json().catch(function(){return {}}).then(function(j){
        if(r.status===401&&path!=="/login"&&path!=="/register"){store.del(TOKEN_KEY);S=null;route()}
        if(!r.ok){var d=j.detail;if(Array.isArray(d))d=d.map(function(x){return x.msg}).join(". ");var e=new Error(d||"Something went wrong.");e.status=r.status;throw e}
        return j;
      });
    },function(){var e=new Error("Cannot reach the server.");e.status=0;throw e});
  }
  function show(id){["auth","onb","sw"].forEach(function(s){var e=$(s);e.hidden=s!==id;if(s===id){e.classList.remove("boot");void e.offsetWidth;e.classList.add("boot")}});
    $("tagline").textContent=id==="sw"?"Your progress, in numbers.":"Turn real progress into levels."}
  function badge(r){var b=el("span","badge",RAR_META[r].n);b.style.color=RAR_META[r].c;b.style.borderColor=RAR_META[r].c;return b}
  function bar(pct,small){var t=el("div","track"+(small?" s":"")),f=el("div","fill");f.style.width=Math.max(0,Math.min(100,pct))+"%";t.appendChild(f);return t}
  function allQ(g){var o=[];g.milestones.forEach(function(m){o=o.concat(m.quests)});return o}
  function applyTheme(){
    var st=document.documentElement.style,map={"--glow":"glow","--glow-hi":"hi","--glow-dim":"dim","--line":"line","--gr":"gr","--muted":"mut"};
    if(!S||!store.get(TOKEN_KEY)){Object.keys(map).forEach(function(k){st.removeProperty(k)});return}
    var c=CLASS_THEME[S.class]||CLASS_THEME.Common;Object.keys(map).forEach(function(k){st.setProperty(k,c[map[k]])});
  }
  function flashPanel(){var pn=document.querySelector("#sw .panel");pn.classList.remove("flash");void pn.offsetWidth;pn.classList.add("flash")}
  function setState(s){S=s;renderSW()}
  function fail(e){if(e.status!==401)toast(e.message)}

  /* AUTH */
  function setMode(m){mode=m;$("tab-in").setAttribute("aria-selected",m==="in");$("tab-up").setAttribute("aria-selected",m==="up");
    $("auth-go").textContent=m==="in"?"Sign in":"Create account";$("pass").autocomplete=m==="in"?"current-password":"new-password";$("auth-err").textContent=""}
  $("tab-in").onclick=function(){setMode("in")};
  $("tab-up").onclick=function(){setMode("up")};
  $("auth-go").onclick=function(){
    var e=$("email").value.trim(),p=$("pass").value;
    if(!/^\S+@\S+\.\S+$/.test(e)){$("auth-err").textContent="Enter a valid email address.";return}
    if(p.length<8){$("auth-err").textContent="Password needs at least 8 characters.";return}
    api("POST",mode==="in"?"/login":"/register",{email:e,password:p}).then(function(r){store.set(TOKEN_KEY,r.token);$("pass").value="";route()},
      function(er){$("auth-err").textContent=er.message});
  };

  /* ONBOARDING */
  function renderDomains(){
    var box=$("domains");box.innerHTML="";
    DOMAIN_NAMES.forEach(function(d,i){
      var b=el("button","chip",d[0]);b.type="button";b.setAttribute("aria-pressed",picked.indexOf(i)>-1);
      b.onclick=function(){var k=picked.indexOf(i);if(k>-1)picked.splice(k,1);else picked.push(i);b.setAttribute("aria-pressed",k===-1)};
      box.appendChild(b);
    });
  }
  function showStep(){
    document.querySelectorAll("[data-step]").forEach(function(s){s.hidden=+s.dataset.step!==step});
    document.querySelectorAll(".steps i").forEach(function(b,i){b.classList.toggle("on",i<=step)});
    $("onb-next").textContent=step===2?"Begin":"Continue";
    $("onb-back").hidden=step===0;$("onb-err").textContent="";
  }
  $("onb-back").onclick=function(){step--;showStep()};
  $("onb-next").onclick=function(){
    if(step===0&&!$("pname").value.trim()){$("onb-err").textContent="Enter a player name to continue.";return}
    if(step===1&&!picked.length){toast("Pick at least one area");return}
    if(step<2){step++;showStep();return}
    api("POST","/player",{name:$("pname").value.trim(),age:$("page").value||"",job:$("pjob").value.trim(),
      focus:DOMAIN_NAMES[picked[0]][1],quest:$("pquest").value.trim(),rarity:$("pdiff").value}).then(function(s){S=s;cur="status";route()},fail);
  };

  /* QUEST ROWS */
  function questRow(g,m,q){
    var r=RULES.rarity[q.rarity],again=q.done&&q.rarity==="common",row=el("div","quest"+(q.done?" done":""));
    var b=el("button",null,"✓");b.type="button";
    b.setAttribute("aria-label",(q.done?(again?"Log again: ":"Completed: "):"Complete: ")+q.text);
    b.onclick=function(){if(q.done&&!again){toast("Already logged.");return}openSheet(g,m,q)};
    var t=el("span","t",q.text);
    t.appendChild(el("small",null,"+"+r.xp+" XP, "+g.stat+" +"+r.stat_xp+" base"+((q.runs||[]).length>1?", logged "+q.runs.length+"x":"")));
    var a=S.advice&&S.advice[String(q.id)];
    if(a)a.tips.forEach(function(x){t.appendChild(el("small","hint",x))});
    if(a&&a.p_complete!=null&&!q.done)t.appendChild(el("small",null,"Likelihood to finish: "+Math.round(a.p_complete*100)+"%"));
    if(!q.done){
      var sk=el("button","link","Skip");sk.type="button";sk.style.cssText="font-size:14px;padding:2px 0;text-align:left";
      sk.onclick=function(e){e.stopPropagation();api("POST","/quests/"+q.id+"/skip").then(function(s){setState(s);toast("Skipped. No penalty.")},fail)};
      t.appendChild(sk);
    }
    row.appendChild(b);row.appendChild(t);row.appendChild(badge(q.rarity));return row;
  }
  function addRow(ph,opts,label,cb){
    var f=el("form","mini"),i=el("input");i.placeholder=ph;i.maxLength=60;i.setAttribute("aria-label",ph);f.appendChild(i);
    var s=null;
    if(opts){s=el("select");s.setAttribute("aria-label","Choose type");opts.forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];s.appendChild(x)});f.appendChild(s)}
    var bt=el("button","btn",label);bt.type="submit";f.appendChild(bt);
    f.onsubmit=function(e){e.preventDefault();var v=i.value.trim();if(!v)return;cb(v,s&&s.value)};
    return f;
  }
  var rarOpts=["common","rare","epic","legendary"].map(function(k){return [k,RAR_META[k].n+" ("+RAR_META[k].rule+")"]});

  /* COMPLETION SHEET: effort + evidence, then the server decides */
  function openSheet(g,m,q){
    pend={g:g,m:m,q:q,effort:3,ev:q.rarity==="legendary"?"proof_reflection":q.rarity==="epic"?"proof":"none",note:"",proof:"",refl:"",err:"",pv:null};
    refreshPreview();renderSW();var s=$("sheet");if(s.scrollIntoView)s.scrollIntoView({block:"start"});
  }
  function refreshPreview(){
    if(!pend)return;var p=pend;
    api("POST","/quests/"+p.q.id+"/preview",{effort:p.effort,evidence:p.ev}).then(function(r){if(pend===p){p.pv=r;renderSheet()}},function(){});
  }
  function renderSheet(){
    var s=$("sheet");s.innerHTML="";
    if(!pend){s.hidden=true;return}
    s.hidden=false;var p=pend,q=p.q,box=el("div","goal"),h=el("div","gh");
    h.appendChild(el("h4",null,q.text));h.appendChild(badge(q.rarity));box.appendChild(h);
    box.appendChild(el("label",null,"How hard was this today? (1 very easy, 5 very hard)"));
    var ec=el("div","chips");
    [1,2,3,4,5].forEach(function(n){var b=el("button","chip",String(n));b.type="button";b.setAttribute("aria-pressed",p.effort===n);
      b.onclick=function(){p.effort=n;refreshPreview();renderSheet()};ec.appendChild(b)});
    box.appendChild(ec);
    box.appendChild(el("label",null,"Evidence"));
    var vc=el("div","chips");
    EVID.forEach(function(k){var b=el("button","chip",k[1]);b.type="button";b.setAttribute("aria-pressed",p.ev===k[0]);
      b.onclick=function(){p.ev=k[0];p.err="";refreshPreview();renderSheet()};vc.appendChild(b)});
    box.appendChild(vc);
    function fld(label,key,multi,max){
      var l=el("label",null,label),i=el(multi?"textarea":"input");if(multi)i.rows=3;i.maxLength=max;i.value=p[key];
      i.oninput=function(){p[key]=i.value};l.appendChild(i);box.appendChild(l);
    }
    if(p.ev==="note")fld("Short note: what did you do?","note",false,200);
    if(p.ev==="proof"||p.ev==="proof_reflection")fld("Proof: a link, a file name or what you can show","proof",false,200);
    if(p.ev==="proof_reflection")fld("Reflection: what did you notice or learn?","refl",true,300);
    var req=q.rarity==="epic"?"Epic quests need proof or proof with reflection.":q.rarity==="legendary"?"Legendary quests need proof with reflection and this week's review saved.":"";
    if(req)box.appendChild(el("div","cnt",req));
    if(p.pv){
      var n="Effort x"+RULES.effort[p.effort]+". Evidence x"+RULES.evidence[p.ev]+".";
      if(p.pv.repeat>1)n+=" Repeat "+p.pv.repeat+" today, x"+p.pv.repeat_mult+".";
      if(p.pv.resting)n+=" Comeback bonus +20%.";
      box.appendChild(el("div","cnt",n));
      box.appendChild(el("div","cnt","Estimate before daily caps: "+p.pv.stat+" +"+nf(p.pv.raw)+" XP."));
    }
    var er=el("p","err",p.err);er.setAttribute("role","alert");box.appendChild(er);
    var ok=el("button","btn","Log completion");ok.type="button";
    ok.onclick=function(){
      api("POST","/quests/"+q.id+"/complete",{effort:p.effort,evidence:p.ev,note:p.note,proof:p.proof,reflection:p.refl}).then(function(r){
        pend=null;setState(r.state);toast(r.messages.join("\n"));if(r.flash)flashPanel();
      },function(e){if(e.status===422||e.status===400){p.err=e.message;renderSheet()}else fail(e)});
    };
    var no=el("button","btn ghost","Cancel");no.type="button";no.onclick=function(){pend=null;renderSW()};
    box.appendChild(ok);box.appendChild(no);s.appendChild(box);
  }

  /* STATUS */
  function renderStatus(){
    var p=S;
    $("lv").textContent=p.level;$("nm").textContent=p.name;
    $("mt").textContent=p.tier+(p.title?" · "+p.title:"")+" · "+p.job+(p.age?" · Age "+p.age:"");
    $("xp-t").textContent=p.xp+" / "+p.need;$("xp-f").style.width=Math.min(100,p.xp/p.need*100)+"%";
    $("mp-t").textContent=p.mp+" / "+RULES.mp_max;$("mp-f").style.width=(p.mp/RULES.mp_max*100)+"%";
    $("hd").textContent=p.class.toUpperCase()+" CLASS";
    $("ncls").textContent="Today "+p.today.xp+" / "+RULES.caps.xp+" XP";
    $("pts").textContent="Grown by logged actions";
    var s=$("stats");s.innerHTML="";
    RULES.stat_keys.forEach(function(k){
      var lv=p.statLevel[k],req=p.statReq[k],xp=p.statXp[k]||0,d=el("div","stat"),top=el("div","top"),r=el("span","r");
      top.appendChild(el("span",null,RULES.stat_labels[k]));
      if(p.resting[k]){var rb=el("span","badge","Resting");rb.style.color="var(--glow-hi)";rb.style.borderColor="var(--glow-hi)";r.appendChild(rb)}
      r.appendChild(el("b",null,lv));top.appendChild(r);
      d.appendChild(top);d.appendChild(bar(xp/req*100,true));d.appendChild(el("div","xpc",Math.floor(xp)+" / "+req));
      s.appendChild(d);
    });
    var box=$("nextup");box.innerHTML="";var list=[];
    p.goals.forEach(function(g){g.milestones.forEach(function(m){m.quests.forEach(function(q){if(!q.done)list.push([g,m,q])})})});
    list.sort(function(a,b){var pa=(p.advice[String(a[2].id)]||{}).p_complete,pb=(p.advice[String(b[2].id)]||{}).p_complete;return (pb==null?0.5:pb)-(pa==null?0.5:pa)});
    if(!list.length)box.appendChild(el("p","empty","All quests done. Add more in the Quests tab."));
    list.slice(0,3).forEach(function(x){box.appendChild(questRow(x[0],x[1],x[2]))});
    var lg=$("log");lg.innerHTML="";
    var es=p.log.slice(-8).reverse();
    if(!es.length)lg.appendChild(el("p","empty","No logged actions yet. Complete a quest to start your log."));
    es.forEach(function(e){
      var d=el("div","past"),bits=[];
      if(e.xp)bits.push("+"+e.xp+" XP");
      if(e.sx)bits.push(e.stat+" +"+nf(e.sx)+" XP");
      if(e.effort)bits.push("effort "+e.effort);
      if(e.ev)bits.push(e.ev.replace("_"," + "));
      (e.flags||[]).forEach(function(f){bits.push(f)});
      d.appendChild(el("b",null,e.text));
      d.appendChild(el("p",null,new Date(e.t).toLocaleDateString(undefined,{month:"short",day:"numeric"})+" · "+(bits.join(" · ")||"logged")));
      if(e.detail)d.appendChild(el("p",null,e.detail));
      lg.appendChild(d);
    });
  }

  /* QUESTS */
  function renderQuests(){
    var v=$("v-quests");v.innerHTML="";
    v.appendChild(el("p","sub","Harder quests give more XP and faster stat growth. Epic needs proof. Legendary needs proof, reflection and a saved weekly review."));
    var lg=el("div","legend");
    ["common","rare","epic","legendary"].forEach(function(k){var b=badge(k);b.textContent=RAR_META[k].n+" +"+RULES.rarity[k].xp+" XP";lg.appendChild(b)});
    v.appendChild(lg);
    if(!S.goals.length)v.appendChild(el("p","empty","No goals yet. Add your first one below."));
    S.goals.forEach(function(g){
      var qs=allQ(g),d=qs.filter(function(q){return q.done}).length,card=el("div","goal"),h=el("div","gh");
      h.appendChild(el("h4",null,g.text));h.appendChild(el("span","tag","Raises "+RULES.stat_labels[g.stat]));card.appendChild(h);
      card.appendChild(bar(qs.length?d/qs.length*100:0));card.appendChild(el("div","cnt",d+" / "+qs.length+" quests"));
      g.milestones.forEach(function(m){
        var md=m.quests.filter(function(q){return q.done}).length,box=el("div","ms"),mh=el("div","mh");
        mh.appendChild(el("b",null,m.text));mh.appendChild(el("span","cnt",md+" / "+m.quests.length+(m.bonus?", cleared":"")));
        box.appendChild(mh);box.appendChild(bar(m.quests.length?md/m.quests.length*100:0,true));
        m.quests.forEach(function(q){box.appendChild(questRow(g,m,q))});
        box.appendChild(addRow("Add a quest",rarOpts,"Add",function(t,r){api("POST","/milestones/"+m.id+"/quests",{text:t,rarity:r}).then(setState,fail)}));
        card.appendChild(box);
      });
      var ms=el("div","ms");
      ms.appendChild(addRow("Add a milestone",null,"Add",function(t){api("POST","/goals/"+g.id+"/milestones",{text:t}).then(setState,fail)}));
      card.appendChild(ms);v.appendChild(card);
    });
    v.appendChild(el("h3","sec","NEW GOAL"));
    var so=RULES.stat_keys.map(function(k){return [k,RULES.stat_labels[k]]});
    v.appendChild(addRow("Goal name",so,"Add goal",function(t,k){api("POST","/goals",{text:t,stat:k}).then(setState,fail)}));
  }

  /* REVIEW */
  function renderReview(){
    var v=$("v-review");v.innerHTML="";
    var wk=S.week,saved=S.reviews[wk]||{},t0=new Date(wk+"T00:00:00").getTime(),done=0,xp=0,sxp=0,cnt={};
    S.log.forEach(function(e){if(e.t<t0)return;xp+=e.xp||0;sxp+=e.sx||0;if(e.type==="quest"){done++;cnt[e.rarity]=(cnt[e.rarity]||0)+1}});
    v.appendChild(el("p","sub","Week of "+new Date(t0).toLocaleDateString(undefined,{month:"short",day:"numeric"})+". Look back, then choose your focus for next week. Reviews calibrate difficulty and never change past XP."));
    var sums=el("div","sums");
    [[done,"Quests done"],[xp,"XP earned"],[nf(sxp),"Stat XP"]].forEach(function(a){var b=el("div","sum");b.appendChild(el("b",null,a[0]));b.appendChild(el("span",null,a[1]));sums.appendChild(b)});
    v.appendChild(sums);
    var lg=el("div","legend");
    Object.keys(cnt).forEach(function(k){var b=badge(k);b.textContent=RAR_META[k].n+" x"+cnt[k];lg.appendChild(b)});
    v.appendChild(lg);
    function field(label,val,ph){var l=el("label",null,label),a=el("textarea");a.rows=3;a.maxLength=300;a.value=val||"";a.placeholder=ph;l.appendChild(a);v.appendChild(l);return a}
    var wins=field("What went well?",saved.wins,"Finished my run three times");
    var block=field("What got in the way?",saved.block,"Late nights, no plan for mornings");
    var next=field("Focus for next week",saved.next,"One Rare quest per goal");
    var energy=saved.energy||0;
    v.appendChild(el("label",null,"Energy this week (1 low, 5 high)"));
    var chips=el("div","chips");
    for(var i=1;i<=5;i++)(function(n){var c=el("button","chip",String(n));c.type="button";c.setAttribute("aria-pressed",energy===n);
      c.onclick=function(){energy=n;[].forEach.call(chips.children,function(x,j){x.setAttribute("aria-pressed",j+1===n)})};chips.appendChild(c)})(i);
    v.appendChild(chips);
    var sv=el("button","btn",saved.at?"Update review":"Save review (+"+RULES.review.xp+" XP, "+RULES.review.stat+" +"+RULES.review.stat_xp+")");sv.type="button";
    sv.onclick=function(){
      api("POST","/review",{wins:wins.value,block:block.value,next:next.value,energy:energy}).then(function(r){
        setState(r.state);toast(r.messages.join("\n"));if(r.flash)flashPanel()},fail);
    };
    v.appendChild(sv);
    var past=Object.keys(S.reviews).filter(function(k){return k!==wk}).sort().reverse().slice(0,3);
    if(past.length){
      v.appendChild(el("h3","sec","PAST REVIEWS"));
      past.forEach(function(k){var r=S.reviews[k],d=el("div","past");d.appendChild(el("b",null,"Week of "+k));d.appendChild(el("p",null,r.wins||"No notes"));v.appendChild(d)});
    }
    if(S.ml){
      v.appendChild(el("h3","sec","ADVISOR"));
      v.appendChild(el("p","sub","Learns from your completions, skips and effort ratings. It only gives advice. It never changes XP or stats. Model v"+S.ml.version+", "+S.ml.samples+" outcomes learned."));
    }
  }

  function renderSW(){
    applyTheme();
    ["status","quests","review"].forEach(function(n){$("v-"+n).hidden=n!==cur});
    document.querySelectorAll(".nav button").forEach(function(b){b.setAttribute("aria-selected",b.dataset.v===cur)});
    renderSheet();
    if(cur==="status")renderStatus();else if(cur==="quests")renderQuests();else renderReview();
  }
  document.querySelectorAll(".nav button").forEach(function(b){b.onclick=function(){cur=b.dataset.v;renderSW()}});
  $("signout").onclick=function(){store.del(TOKEN_KEY);S=null;cur="status";pend=null;route()};
  var armT=0;
  $("reset").onclick=function(){
    var b=$("reset");
    if(!b.dataset.arm){b.dataset.arm="1";b.textContent="Tap again to delete your player";clearTimeout(armT);
      armT=setTimeout(function(){delete b.dataset.arm;b.textContent="Reset player"},4000);return}
    clearTimeout(armT);delete b.dataset.arm;b.textContent="Reset player";
    api("DELETE","/player").then(function(){S=null;step=0;picked=[];cur="status";pend=null;route()},fail);
  };

  function net(){var on=navigator.onLine;$("sync").classList.toggle("off",!on);$("sync-t").textContent=on?"Online":"Offline: reconnect to save"}
  window.addEventListener("online",net);window.addEventListener("offline",net);net();

  function route(){
    applyTheme();
    if(!store.get(TOKEN_KEY)){show("auth");return}
    api("GET","/state").then(function(s){S=s;renderSW();show("sw")},function(e){
      if(e.status===404){step=0;renderDomains();showStep();show("onb")}
      else if(e.status===0){toast(e.message);show("auth")}
    });
  }
  api("GET","/rules").then(function(r){RULES=r;route()},function(e){toast(e.message);show("auth")});
})();
