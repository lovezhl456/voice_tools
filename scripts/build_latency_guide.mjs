// Build-time only: MARKED_MODULE points to an installed marked ESM module.
// Generated pages contain no remote runtime dependencies.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {marked} = await import(process.env.MARKED_MODULE ? pathToFileURL(process.env.MARKED_MODULE).href : 'marked');
const root = path.resolve(import.meta.dirname, '..');
const output = path.join(root, 'docs/latency-site');
fs.mkdirSync(output, {recursive:true});
const docs = [['latency','使用与结果'],['latency-install','安装与回退'],['latency-integration','任务与协议'],['latency-validation','验收记录']];
const nav = `<nav><a href="/">← 本地总导航</a>${docs.map(([name,title])=>`<a href="${name}.html">${title}</a>`).join('')}<a href="demo/">合成录音演示 ↗</a><a href="task-review/">任务复查演示 ↗</a></nav>`;
const style = `*{box-sizing:border-box}body{margin:0;background:#f2f6f7;color:#183347;font:16px/1.8 system-ui,sans-serif}header{background:#15394b;color:white;padding:32px max(20px,calc((100vw - 1040px)/2))}header p{color:#c0e1e6}nav{display:flex;flex-wrap:wrap;gap:12px}nav a{color:#d9f1f2;font-size:14px}main{max-width:1040px;margin:28px auto;background:white;padding:32px;border-radius:16px}h1{font-size:30px}h2{margin-top:32px;border-top:1px solid #dce8ec;padding-top:20px}a{color:#14727a;overflow-wrap:anywhere}pre{overflow:auto;padding:18px;background:#eef4f6;border-radius:10px;font-size:13px}code{font-family:ui-monospace,monospace;overflow-wrap:anywhere}table{display:block;max-width:100%;overflow:auto;border-collapse:collapse;font-size:14px}th,td{border:1px solid #dae5e8;padding:10px;text-align:left;min-width:100px}blockquote{border-left:4px solid #19818b;padding-left:16px}footer{max-width:1040px;margin:20px auto;color:#526976}@media(max-width:650px){main{padding:18px;margin:12px}header{padding:24px 18px}h1{font-size:24px}}`;
for (const [name] of docs) {
  let markdown = fs.readFileSync(path.join(root,'docs',name+'.md'),'utf8');
  markdown = markdown.replace(/\]\(([^)]+)\)/g,(all,url)=>{
    if (/^(https?:|#)/.test(url)) return all;
    const stem=url.replace(/\.md$/,'');
    if (docs.some(([name])=>name===stem)) return `](${stem}.html)`;
    const resolved=path.posix.normalize('docs/'+url);
    return `](https://github.com/lovezhl456/voice_tools/blob/feat/v0.15.1-latency/${resolved})`;
  });
  const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>latency · ${name}</title><style>${style}</style></head><body><header><p>VOICE_TOOLS 0.15.1 · 可选外部引擎</p><h1>双轨录音延迟说明书</h1>${nav}</header><main>${marked.parse(markdown)}</main><footer>此站只展示说明书和合成音频；业务报告保留在各自结果目录。内容源为仓库四份 latency 文档。</footer></body></html>`;
  fs.writeFileSync(path.join(output,name+'.html'),html);
  if(name==='latency')fs.writeFileSync(path.join(output,'index.html'),html);
}
