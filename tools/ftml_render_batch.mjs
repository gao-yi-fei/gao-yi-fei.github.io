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
  const removed = { modules: 0, includes: 0, attachments: 0, html: 0, spacers: 0 };
  let text = source.replace(/\r\n?/g, "\n");
  const lines = text.split("\n");
  const kept = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (/^\[\[include\b/i.test(trimmed)) {
      removed.includes += 1;
      if (!trimmed.endsWith("]]")) while (++index < lines.length && lines[index].trim() !== "]]" ) {}
      continue;
    }
    const module = trimmed.match(/^\[\[module\s+([^\s\]]+)/i);
    if (module) {
      removed.modules += 1;
      const name = module[1].toLowerCase();
      if (["css", "listpages"].includes(name)) while (++index < lines.length && !/\[\[\/module\]\]/i.test(lines[index])) {}
      continue;
    }
    if (/^\[\[\/module\]\]$/i.test(trimmed)) continue;
    if (/^\[\[html\]\]$/i.test(trimmed)) {
      removed.html += 1;
      while (++index < lines.length && !/^\[\[\/html\]\]$/i.test(lines[index].trim())) {}
      continue;
    }
    if (/^@{2,}$/.test(trimmed)) { removed.spacers += 1; continue; }
    kept.push(line);
  }
  text = kept.join("\n");
  text = text.replace(/\[\[(?:f<)?image\b[^\]]*\]\]/gi, () => { removed.attachments += 1; return token("IMAGE"); });
  text = text.replace(/\[\[\*?user\s+([^\]]+)\]\]/gi, (_match, name) => token("USER", name.trim()));
  return { text, removed };
}

function restoreTokens(html) {
  return html
    .replace(/SCPPER_USER_([A-Za-z0-9_-]+)__/g, (_match, value) => `<span class="wiki-user">${Buffer.from(value, "base64url").toString("utf8").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]))}</span>`)
    .replace(/SCPPER_IMAGE_[A-Za-z0-9_-]*__/g, '');
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
