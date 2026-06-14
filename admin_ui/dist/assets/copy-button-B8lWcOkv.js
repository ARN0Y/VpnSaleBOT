import{x as d,a4 as m,$ as t,r as p}from"./index-CYikJhbe.js";import{C as u}from"./check-hUi0kzy1.js";/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const h=d("Copy",[["rect",{width:"14",height:"14",x:"8",y:"8",rx:"2",ry:"2",key:"17jyea"}],["path",{d:"M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2",key:"zix9uf"}]]);function y({value:o,className:c,title:n="کپی"}){const[s,r]=m.useState(!1),i=async a=>{a.preventDefault(),a.stopPropagation();try{await navigator.clipboard.writeText(o)}catch{const e=document.createElement("textarea");e.value=o,document.body.appendChild(e),e.select();try{document.execCommand("copy")}catch{}document.body.removeChild(e)}r(!0),setTimeout(()=>r(!1),1400)};return t.jsx("button",{type:"button",onClick:i,title:n,className:p("inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border text-muted-foreground transition hover:border-white/25 hover:text-white",c),children:s?t.jsx(u,{className:"h-3.5 w-3.5 text-emerald-300"}):t.jsx(h,{className:"h-3.5 w-3.5"})})}export{y as C};
