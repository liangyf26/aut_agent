// Upload aut_agent docs to Feishu cloud docs
const fs = require('fs');
const path = require('path');

const config = JSON.parse(fs.readFileSync(
  path.join(process.env.USERPROFILE, '.openclaw', 'openclaw.json'), 'utf8'
));
const { appId, appSecret } = config.channels.feishu;

const BASE = 'https://open.feishu.cn/open-apis';
const OWNER_ID = 'ou_2dfdb336d21b8a88c2b21f5ba579279b';

let token = null;

async function api(method, endpoint, body) {
  if (!token) throw new Error('No token');
  const url = BASE + endpoint;
  const opts = {
    method,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const data = await res.json();
  if (data.code !== 0) {
    console.error(`API Error: ${endpoint} → code=${data.code} msg=${data.msg}`);
    throw new Error(data.msg);
  }
  return data;
}

async function getToken() {
  const res = await fetch(BASE + '/auth/v3/tenant_access_token/internal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret })
  });
  const data = await res.json();
  if (data.code !== 0) throw new Error('Auth failed: ' + data.msg);
  token = data.tenant_access_token;
  console.log('Token obtained, expires in', data.expire, 's');
}

// Create a docx document in a folder (or root)
async function createDoc(title) {
  const body = {
    title,
    folder_token: ''  // empty = root
  };
  const data = await api('POST', '/docx/v1/documents', body);
  const docId = data.data.document.document_id;
  console.log('Created doc:', title, '→', docId);
  return docId;
}

// Convert markdown lines to feishu blocks
function mdToBlocks(mdText) {
  const lines = mdText.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Skip empty lines
    if (line.trim() === '') {
      i++;
      continue;
    }

    // Heading
    const hMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (hMatch) {
      const level = hMatch[1].length;
      const text = hMatch[2];
      const blockType = 2 + level; // 3=h1, 4=h2, ..., 8=h6
      const key = {1:'heading1',2:'heading2',3:'heading3',4:'heading4',5:'heading5',6:'heading6'}[level];
      blocks.push({
        block_type: blockType,
        [key]: {
          elements: [{ text_run: { content: text } }],
          style: {}
        }
      });
      i++;
      continue;
    }

    // Horizontal rule → skip (feishu divider API differs)
    if (line.match(/^---+$/)) {
      i++;
      continue;
    }

    // Code block
    if (line.startsWith('```')) {
      i++;
      const codeLines = [];
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      blocks.push({
        block_type: 14,
        code: {
          elements: [{ text_run: { content: codeLines.join('\n') } }],
          style: { language: 1 }
        }
      });
      continue;
    }

    // Bullet list
    if (line.match(/^[\s]*[-*]\s/)) {
      const bulletItems = [];
      while (i < lines.length && lines[i].match(/^[\s]*[-*]\s/)) {
        const text = lines[i].replace(/^[\s]*[-*]\s+/, '');
        bulletItems.push(text);
        i++;
      }
      blocks.push({
        block_type: 12,
        bullet: {
          elements: bulletItems.map(t => ({
            text_run: { content: t }
          })),
          style: {}
        }
      });
      continue;
    }

    // Ordered list
    if (line.match(/^\d+[\.\)]\s/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\d+[\.\)]\s/)) {
        const text = lines[i].replace(/^\d+[\.\)]\s+/, '');
        items.push(text);
        i++;
      }
      blocks.push({
        block_type: 13,
        ordered: {
          elements: items.map(t => ({
            text_run: { content: t }
          })),
          style: {}
        }
      });
      continue;
    }

    // Bold/italic inline handling - simple approach
    // **bold** → bold, *italic* → italic
    const elements = parseInline(line);
    blocks.push({
      block_type: 2,
      text: {
        elements,
        style: {}
      }
    });
    i++;
  }

  return blocks;
}

function parseInline(text) {
  const elements = [];
  let remaining = text;

  while (remaining.length > 0) {
    // Bold+Italic ***...***
    const biMatch = remaining.match(/^\*\*\*(.+?)\*\*\*/);
    if (biMatch) {
      elements.push({
        text_run: {
          content: biMatch[1],
          text_element_style: { bold: true, italic: true }
        }
      });
      remaining = remaining.slice(biMatch[0].length);
      continue;
    }

    // Bold **...**
    const bMatch = remaining.match(/^\*\*(.+?)\*\*/);
    if (bMatch) {
      elements.push({
        text_run: {
          content: bMatch[1],
          text_element_style: { bold: true }
        }
      });
      remaining = remaining.slice(bMatch[0].length);
      continue;
    }

    // Italic *...* or _..._
    const iMatch = remaining.match(/^[\*_](.+?)[\*_]/);
    if (iMatch && !iMatch[0].startsWith('__')) {
      elements.push({
        text_run: {
          content: iMatch[1],
          text_element_style: { italic: true }
        }
      });
      remaining = remaining.slice(iMatch[0].length);
      continue;
    }

    // Inline code `...`
    const cMatch = remaining.match(/^`(.+?)`/);
    if (cMatch) {
      elements.push({
        text_run: {
          content: cMatch[1],
          text_element_style: { inline_code: true }
        }
      });
      remaining = remaining.slice(cMatch[0].length);
      continue;
    }

    // Link [text](url)
    const lMatch = remaining.match(/^\[(.+?)\]\((.+?)\)/);
    if (lMatch) {
      elements.push({
        text_run: {
          content: lMatch[1],
          text_element_style: { link: { url: lMatch[2] } }
        }
      });
      remaining = remaining.slice(lMatch[0].length);
      continue;
    }

    // Plain text up to next marker
    const nextMarker = remaining.search(/[\*\`\[]/);
    if (nextMarker === -1) {
      elements.push({ text_run: { content: remaining } });
      break;
    }
    if (nextMarker > 0) {
      elements.push({ text_run: { content: remaining.slice(0, nextMarker) } });
      remaining = remaining.slice(nextMarker);
    } else {
      // Marker at position 0, will be caught by handlers above
      // Fallback: take first char
      elements.push({ text_run: { content: remaining[0] } });
      remaining = remaining.slice(1);
    }
  }

  return elements;
}

async function addBlocks(docId, blocks) {
  // Feishu API max 50 blocks per request
  const chunkSize = 50;
  for (let i = 0; i < blocks.length; i += chunkSize) {
    const chunk = blocks.slice(i, i + chunkSize);
    const data = await api(
      'POST',
      `/docx/v1/documents/${docId}/blocks/${docId}/children`,
      {
        children: chunk,
        index: -1  // append at end
      }
    );
    console.log(`  Added blocks ${i + 1}-${Math.min(i + chunkSize, blocks.length)}`);
  }
}

async function uploadDoc(title, mdFilePath) {
  const content = fs.readFileSync(mdFilePath, 'utf8');
  // Remove YAML frontmatter if present
  const cleaned = content.replace(/^---[\s\S]*?---\n*/, '');
  console.log(`\nConverting: ${title} (${cleaned.length} chars)`);
  
  const blocks = mdToBlocks(cleaned);
  console.log(`  Parsed ${blocks.length} blocks`);
  
  const docId = await createDoc(title);
  await addBlocks(docId, blocks);
  
  // Get doc URL
  const info = await api('GET', `/docx/v1/documents/${docId}`);
  const docUrl = info.data.document.document_url || `https://bytedance.feishu.cn/docx/${docId}`;
  console.log(`  URL: ${docUrl}`);
  return { docId, docUrl };
}

async function main() {
  await getToken();

  const docs = [
    { title: 'aut_agent - 领域术语表 (CONTEXT)', file: 'C:\\project\\aut_agent\\CONTEXT.md' },
    { title: 'aut_agent - 头脑风暴与技术调研', file: 'C:\\project\\aut_agent\\docs\\头脑风暴.md' },
    { title: 'aut_agent - 需求分析初版', file: 'C:\\project\\aut_agent\\docs\\需求分析初版.md' },
  ];

  const results = [];
  for (const doc of docs) {
    try {
      const result = await uploadDoc(doc.title, doc.file);
      results.push(result);
    } catch (e) {
      console.error(`Failed: ${doc.title}:`, e.message);
    }
  }

  console.log('\n=== Done ===');
  for (const r of results) {
    console.log(`${r.docUrl}`);
  }
}

main().catch(e => {
  console.error('Fatal error:', e);
  process.exit(1);
});
