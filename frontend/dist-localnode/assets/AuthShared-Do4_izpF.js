import{t as e}from"./check-B0x9vtT0.js";import{D as t,E as n,L as r,Mn as i,gn as a,w as o}from"./index-B6Azh60C.js";var s=r(`arrow-right`,[[`path`,{d:`M5 12h14`,key:`1ays0h`}],[`path`,{d:`m12 5 7 7-7 7`,key:`xquz4c`}]]);i();var c=a(),l={primary:`#1a699f`,primaryDark:`#155580`,primaryDarker:`#0d3f61`,tealLight:`#e6f3f9`,tealMedium:`#b8dcee`,tealPale:`#f0f8fc`,tealSidebar:`#d6f1e8`,white:`#ffffff`,text:`#0f172a`,textSub:`#475569`,textMuted:`#94a3b8`,border:`#e2e8f0`,bg:`#f0f8fc`,success:`#0f766e`,error:`#dc2626`,errorLight:`#fef2f2`},u=`
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap');
  *, *::before, *::after { box-sizing: border-box; }

  @keyframes slide-in-left  { from { opacity:0; transform:translateX(-32px) } to { opacity:1; transform:translateX(0) } }
  @keyframes slide-in-right { from { opacity:0; transform:translateX(32px)  } to { opacity:1; transform:translateX(0) } }
  @keyframes fade-up        { from { opacity:0; transform:translateY(18px)  } to { opacity:1; transform:translateY(0) } }
  @keyframes spin-auth      { to   { transform:rotate(360deg) } }

  .auth-left  { animation: slide-in-left  0.65s cubic-bezier(.22,1,.36,1) both; }
  .auth-right { animation: slide-in-right 0.65s cubic-bezier(.22,1,.36,1) 0.08s both; }
  .auth-fade  { animation: fade-up 0.45s cubic-bezier(.22,1,.36,1) both; }

  .auth-input {
    width:100%; padding:13px 16px; border-radius:14px; outline:none;
    border:1.5px solid ${l.border}; font-size:14px; color:${l.text};
    background:${l.white}; transition:border-color .15s, box-shadow .15s;
    font-family:inherit;
  }
  .auth-input:focus {
    border-color:${l.primary} !important;
    box-shadow:0 0 0 3px rgba(26,105,159,.12) !important;
  }
  .auth-input::placeholder { color:${l.textMuted}; }
  .auth-input-icon { padding-left:42px !important; }

  .auth-btn {
    width:100%; padding:14px; border-radius:14px; border:none;
    background:linear-gradient(135deg, ${l.primary} 0%, ${l.primaryDark} 100%);
    color:#fff; font-size:15px; font-weight:700; cursor:pointer;
    display:flex; align-items:center; justify-content:center; gap:8px;
    transition:filter .2s, transform .15s; font-family:inherit; letter-spacing:-0.2px;
  }
  .auth-btn:hover:not(:disabled) { filter:brightness(1.06); transform:translateY(-1px); }
  .auth-btn:active:not(:disabled){ transform:translateY(0); }
  .auth-btn:disabled { opacity:0.65; cursor:not-allowed; }

  .auth-link { color:${l.primary}; font-weight:600; text-decoration:none; }
  .auth-link:hover { text-decoration:underline; }

  .auth-role-btn {
    padding:12px 14px; border-radius:12px; cursor:pointer;
    border:1.5px solid ${l.border}; background:#fff;
    text-align:left; font-family:inherit; transition:all 0.15s;
  }
  .auth-role-btn:hover { border-color:${l.primary}; background:${l.tealLight}; }

  ::-webkit-scrollbar { width:6px; }
  ::-webkit-scrollbar-thumb { background:${l.tealMedium}; border-radius:10px; }
`,d=({bullets:t,illustration:n,footer:r})=>(0,c.jsxs)(`div`,{className:`auth-left`,style:{width:`48%`,minWidth:460,position:`relative`,overflow:`hidden`,background:`linear-gradient(180deg, ${l.tealSidebar} 0%, ${l.tealPale} 48%, ${l.tealLight} 100%)`,display:`flex`,flexDirection:`column`,justifyContent:`space-between`,padding:`56px 56px 42px`,borderRight:`1px solid ${l.border}`},children:[(0,c.jsx)(`div`,{style:{position:`absolute`,width:220,height:220,borderRadius:`50%`,background:`rgba(255,255,255,0.08)`,top:-40,right:-40,pointerEvents:`none`}}),(0,c.jsx)(`div`,{style:{position:`absolute`,width:110,height:110,borderRadius:`50%`,background:`rgba(255,255,255,0.06)`,bottom:18,left:-28,pointerEvents:`none`}}),(0,c.jsxs)(`div`,{style:{position:`relative`,zIndex:1},children:[(0,c.jsxs)(`div`,{style:{display:`flex`,alignItems:`center`,gap:10,marginBottom:22},children:[(0,c.jsx)(`div`,{style:{width:38,height:38,borderRadius:10,flexShrink:0,background:`linear-gradient(135deg, ${l.primary}, ${l.primaryDark})`,display:`flex`,alignItems:`center`,justifyContent:`center`},children:(0,c.jsx)(o,{size:20,color:`#fff`})}),(0,c.jsx)(`span`,{style:{fontSize:13,fontWeight:700,letterSpacing:.5,textTransform:`uppercase`,color:l.textMuted,fontFamily:`var(--font-heading)`},children:`QIntellect Technologies`})]}),(0,c.jsxs)(`h1`,{style:{fontFamily:`var(--font-display)`,fontSize:44,fontWeight:800,lineHeight:1.08,margin:0,maxWidth:430,letterSpacing:-1.2,color:l.text},children:[`Revolutionize`,(0,c.jsx)(`br`,{}),`Attendance with AI`]}),(0,c.jsx)(`div`,{style:{marginTop:28,display:`grid`,gap:12,maxWidth:400},children:t.map(t=>(0,c.jsxs)(`div`,{style:{display:`flex`,alignItems:`center`,gap:12},children:[(0,c.jsx)(`div`,{style:{width:26,height:26,borderRadius:8,flexShrink:0,background:`rgba(26,105,159,0.08)`,display:`grid`,placeItems:`center`},children:(0,c.jsx)(e,{size:14,color:l.primary,strokeWidth:3})}),(0,c.jsx)(`span`,{style:{fontSize:15,fontWeight:500,color:l.text},children:t})]},t))})]}),n&&(0,c.jsx)(`div`,{style:{position:`relative`,marginTop:28,zIndex:1},children:n}),(0,c.jsx)(`div`,{style:{display:`flex`,flexWrap:`wrap`,gap:18,color:l.textMuted,fontSize:12,marginTop:40,position:`relative`,zIndex:1},children:r??(0,c.jsxs)(c.Fragment,{children:[(0,c.jsx)(`span`,{style:{cursor:`pointer`},children:`Terms of Service`}),(0,c.jsx)(`span`,{style:{cursor:`pointer`},children:`Privacy Policy`}),(0,c.jsx)(`span`,{children:`© QIntellect Technologies`})]})})]}),f=({left:e,right:t})=>(0,c.jsxs)(`div`,{style:{display:`flex`,height:`100vh`,fontFamily:`var(--font-body)`,overflow:`hidden`,background:l.bg},children:[(0,c.jsx)(`style`,{children:u}),e,t]}),p=({children:e})=>(0,c.jsxs)(`div`,{className:`auth-right`,style:{flex:1,minWidth:420,overflow:`auto`,display:`flex`,alignItems:`center`,justifyContent:`center`,background:`#f8fbfd`,padding:`48px 40px`,position:`relative`},children:[(0,c.jsx)(`div`,{style:{position:`absolute`,top:32,right:32,width:160,height:160,borderRadius:28,background:`rgba(16,185,129,0.08)`,zIndex:0}}),(0,c.jsx)(`div`,{style:{position:`absolute`,bottom:60,left:28,width:120,height:120,borderRadius:28,background:`rgba(56,189,248,0.08)`,zIndex:0}}),(0,c.jsxs)(`div`,{style:{width:`100%`,maxWidth:420,position:`relative`,zIndex:1},children:[(0,c.jsxs)(`div`,{style:{display:`flex`,justifyContent:`space-between`,alignItems:`center`,marginBottom:26,color:l.textSub,fontSize:13},children:[(0,c.jsx)(`span`,{style:{fontWeight:600},children:`QIntellect AI Attendance`}),(0,c.jsx)(`span`,{style:{color:l.primary,fontWeight:600,cursor:`pointer`},children:`Support`})]}),(0,c.jsx)(`div`,{style:{background:`#fff`,borderRadius:28,boxShadow:`0 40px 70px rgba(15,45,74,0.08)`,padding:`36px`,border:`1px solid rgba(226,232,240,0.85)`,position:`relative`,overflow:`hidden`},children:e})]})]}),m=({children:e})=>(0,c.jsx)(`label`,{style:{display:`block`,fontSize:13,fontWeight:600,color:`#334155`,marginBottom:7},children:e}),h=({leftIcon:e,rightToggle:r,extraRight:i,style:a,...o})=>(0,c.jsxs)(`div`,{style:{position:`relative`},children:[e&&(0,c.jsx)(`div`,{style:{position:`absolute`,left:14,top:`50%`,transform:`translateY(-50%)`,pointerEvents:`none`,display:`flex`},children:e}),(0,c.jsx)(`input`,{className:`auth-input${e?` auth-input-icon`:``}`,style:{paddingRight:r?44:void 0,...a},...o}),i&&(0,c.jsx)(`div`,{style:{position:`absolute`,right:r?44:12,top:`50%`,transform:`translateY(-50%)`},children:i}),r&&(0,c.jsx)(`button`,{type:`button`,onClick:r.onToggle,style:{position:`absolute`,right:12,top:`50%`,transform:`translateY(-50%)`,background:`none`,border:`none`,color:l.textMuted,cursor:`pointer`,padding:4,display:`flex`,alignItems:`center`},children:r.show?(0,c.jsx)(t,{size:16}):(0,c.jsx)(n,{size:16})})]}),g=({loading:e,disabled:t,children:n,loadingLabel:r=`Please wait…`,type:i=`submit`})=>(0,c.jsx)(`button`,{type:i,className:`auth-btn`,disabled:e||t,children:e?(0,c.jsxs)(c.Fragment,{children:[(0,c.jsx)(`div`,{style:{width:16,height:16,borderRadius:`50%`,border:`2px solid rgba(255,255,255,0.3)`,borderTopColor:`#fff`,animation:`spin-auth 0.7s linear infinite`}}),r]}):n}),_=({message:e})=>(0,c.jsxs)(`div`,{style:{background:l.errorLight,border:`1px solid #fca5a5`,borderRadius:14,padding:`12px 14px`,fontSize:13,color:l.error,fontWeight:500,marginBottom:20,display:`flex`,alignItems:`center`,gap:8},children:[(0,c.jsx)(`span`,{style:{fontSize:16},children:`⚠️`}),` `,e]});export{h as a,f as c,_ as i,s as l,g as n,m as o,p as r,d as s,l as t};