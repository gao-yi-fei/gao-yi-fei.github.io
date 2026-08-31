// Build-time bridge to Wikijump's FTML parser. It never reaches the live site.
import { readFileSync } from "node:fs";
import init, * as FTML from "./vendor/wikijump-ftml/ftml.js";

const input = await new Promise((resolve, reject) => {
  let data = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { data += chunk; });
  process.stdin.on("end", () => resolve(data));
  process.stdin.on("error", reject);
});

await init({ module_or_path: readFileSync(new URL("./vendor/wikijump-ftml/ftml_bg.wasm", import.meta.url)) });

function token(kind, value = "") {
  return `SCPPER_${kind}_${Buffer.from(value, "utf8").toString("base64url")}__`;
}

function stripUnavailable(source) {
  const removed = { modules: 0, includes: 0, attachments: 0, html: 0, spacers: 0, rate: 0 };
  let text = source.replace(/\r\n?/g, "\n");
  // Whole-block constructs must be peeled off before FTML sees them. The
  // closure can span many lines and its terminator is not necessarily on a
  // line of its own (e.g. "| param=value ]]"), so match by regex over the
  // whole source instead of scanning line by line. This also prevents a
  // multi-line include from feeding FTML a block with no closing terminator,
  // which the WASM parser can hang on.
  text = text.replace(/\[\[include\b[^\n]*?(?:\n(?!\[\[include\b)[^\n]*)*?\]\]/gis, (match) => {
    removed.includes += 1;
    const name = match.replace(/^\[\[include\s+/i, "").split(/[\s|]/)[0] || "未命名组件";
    return token("NOTICE", name);
  });
  text = text.replace(/\[\[module\s+([^\s\]]+)[^\n]*?(?:\n(?![\[\[])[^\n]*)*?\]\]|\[\[\/module\]\]/gis, (match) => {
    const name = (match.match(/^\[\[module\s+([^\s\]]+)/i) || [])[1]?.toLowerCase();
    if (name === "rate") {
      removed.rate += 1;
      return token("NOTICE", "评分组件已由页首快照替代");
    }
    if (name) {
      removed.modules += 1;
      return token("NOTICE", `动态模块（${name}）在只读备份中已省略`);
    }
    return "";
  });
  text = text.replace(/\[\[tabview\]\][\s\S]*?\[\[\/tabview\]\]|\[\[tabs\]\][\s\S]*?\[\[\/tabs\]\]|\[\[tab\s[^\]]*\]\][\s\S]*?\[\[\/tab\]\]|\[\[\/?tab(?:view|s)?\]\]/gis, (match) => {
    if (/tabview|tabs\]\]$|\[\[tabs\]\]/i.test(match)) {
      return token("NOTICE", "选项卡视图在只读备份中已省略");
    }
    return "";
  });
  // Ruby/furigana annotation spans can nest and span many lines; FTML leaves
  // them half-parsed (e.g. "[[span class=\"rt\"]][[span class=\"ruby\"]][[span").
  // Peel the whole block, keep the base text, and drop the stray [[/span]] tags.
  text = text.replace(/\[\[span class=["'](?:ruby|rt)["']\]\]([\s\S]*?)\[\[\/span\]\]/gis, (_match, inner) => {
    const base = inner.replace(/\[\[\/?span class=["'](?:ruby|rt)["']\]\]/gis, "").replace(/^\/\/##[^|]*\|/m, "").replace(/##\/\//g, "");
    return base;
  });
  // Any other inline [[span]]/[[/span]] is dynamic styling that the
  // read-only backup must not execute; drop it rather than leak raw markup.
  text = text.replace(/\[\[\/?span\b[^\]]*\]\]/gis, "");
  // [[=]] is a centering marker that FTML keeps verbatim; drop it.
  text = text.replace(/\[\[=\]\]|\[\[\/=\]\]/g, "");
  text = text.replace(/\[\[html\]\][\s\S]*?\[\[\/html\]\]/gis, () => {
    removed.html += 1;
    return "";
  });
  text = text.replace(/^@+$/gm, () => {
    removed.spacers += 1;
    return "";
  });
  text = text.replace(/\[\[(?:f<)?image\b[^\]]*\]\]/gi, () => { removed.attachments += 1; return token("IMAGE"); });
  text = text.replace(/\[\[\*?user\s+([^\]]+)\]\]/gi, (_match, name) => token("USER", name.trim()));
  return { text, removed };
}

function restoreTokens(html) {
  return html
    .replace(/SCPPER_USER_([A-Za-z0-9_-]+)__/g, (_match, value) => `<span class="wiki-user">${Buffer.from(value, "base64url").toString("utf8").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]))}</span>`)
    .replace(/SCPPER_IMAGE_[A-Za-z0-9_-]*__/g, '<span class="missing-resource">图片附件未归档</span>')
    .replace(/SCPPER_NOTICE_([A-Za-z0-9_-]+)__/g, (_match, value) => `<aside class="backup-note">包含组件未归档：<code>${Buffer.from(value, "base64url").toString("utf8").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]))}</code></aside>`);
}

const records = JSON.parse(input);
const result = {};
for (const record of records) {
  const { text, removed } = stripUnavailable(record.source || "");
  let tokens, info, settings, parseInfo, parseSettings, parsed, tree, rendered;
  try {
    tokens = FTML.tokenize(text);
    info = new FTML.PageInfo({ alt_title: null, category: null, language: "default", score: record.rating || 0, page: record.name, site: "scp-mc", tags: record.tags || [], title: record.title || record.name });
    settings = FTML.WikitextSettings.from_mode("page", "wikijump");
    parseInfo = info.copy(); parseSettings = settings.copy();
    parsed = FTML.parse(tokens, parseInfo, parseSettings);
    tree = parsed.syntax_tree();
    rendered = FTML.render_html(tree, info, settings);
    result[record.name] = { html: restoreTokens(rendered.body()), removed, errors: parsed.errors().length };
  } catch (error) {
    result[record.name] = { html: "", removed, errors: 1, error: String(error?.message || error) };
  } finally {
    for (const object of [rendered, tree, parsed, parseSettings, parseInfo, settings, info, tokens]) {
      try { object?.free(); } catch (_) {}
    }
  }
}
process.stdout.write(JSON.stringify(result));
