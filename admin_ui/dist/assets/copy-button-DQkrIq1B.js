import{y as c,a5 as h,a0 as t,s as p}from"./index-Bqt-n8hA.js";import{C as y}from"./check-MtjswKR2.js";/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const u=c("Copy",[["rect",{width:"14",height:"14",x:"8",y:"8",rx:"2",ry:"2",key:"17jyea"}],["path",{d:"M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2",key:"zix9uf"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const x=c("RefreshCw",[["path",{d:"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8",key:"v9h5vc"}],["path",{d:"M21 3v5h-5",key:"1q7to0"}],["path",{d:"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16",key:"3uifl3"}],["path",{d:"M8 16H3v5",key:"1cv678"}]]);function f({value:o,className:s,title:n="کپی"}){const[d,a]=h.useState(!1),i=async r=>{r.preventDefault(),r.stopPropagation();try{await navigator.clipboard.writeText(o)}catch{const e=document.createElement("textarea");e.value=o,document.body.appendChild(e),e.select();try{document.execCommand("copy")}catch{}document.body.removeChild(e)}a(!0),setTimeout(()=>a(!1),1400)};return t.jsx("button",{type:"button",onClick:i,title:n,className:p("inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border text-muted-foreground transition hover:border-white/25 hover:text-white",s),children:d?t.jsx(y,{className:"h-3.5 w-3.5 text-emerald-300"}):t.jsx(u,{className:"h-3.5 w-3.5"})})}export{f as C,x as R};
