#!/usr/bin/env node
/**
 * Verify WebUI internationalization requirements (I18N-01..I18N-04):
 * 1. en.json and pseudo-dev.json have identical key hierarchies and {placeholder} tokens.
 * 2. Every API error code in errorMap.ts maps to a valid key in en.json.
 * 3. Every t('...') reference in Vue/TS files resolves to a valid key in en.json.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const EN_PATH = path.join(ROOT, 'webui/src/locales/en.json');
const PSEUDO_PATH = path.join(ROOT, 'webui/src/locales/pseudo-dev.json');
const ERROR_MAP_PATH = path.join(ROOT, 'webui/src/i18n/errorMap.ts');
const SRC_DIR = path.join(ROOT, 'webui/src');

function flattenCatalog(obj, prefix = '') {
  const result = new Map();
  for (const [k, v] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      for (const [subK, subV] of flattenCatalog(v, fullKey)) {
        result.set(subK, subV);
      }
    } else {
      result.set(fullKey, String(v));
    }
  }
  return result;
}

function extractPlaceholders(str) {
  return [...str.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map((m) => m[1]).sort();
}

const enCatalog = flattenCatalog(JSON.parse(fs.readFileSync(EN_PATH, 'utf8')));
const pseudoCatalog = flattenCatalog(JSON.parse(fs.readFileSync(PSEUDO_PATH, 'utf8')));

const errors = [];

for (const [key, enVal] of enCatalog.entries()) {
  if (!enVal.trim()) {
    errors.push(`Empty translation value in en.json for key '${key}'`);
  }
  if (!pseudoCatalog.has(key)) {
    errors.push(`Missing key '${key}' in pseudo-dev.json`);
    continue;
  }
  const pseudoVal = pseudoCatalog.get(key);
  const enTokens = extractPlaceholders(enVal).join(',');
  const pseudoTokens = extractPlaceholders(pseudoVal).join(',');
  if (enTokens !== pseudoTokens) {
    errors.push(
      `Placeholder mismatch for '${key}': en=[${enTokens}] vs pseudo-dev=[${pseudoTokens}]`
    );
  }
}

for (const key of pseudoCatalog.keys()) {
  if (!enCatalog.has(key)) {
    errors.push(`Extra key '${key}' in pseudo-dev.json not present in en.json`);
  }
}

const errorMapContent = fs.readFileSync(ERROR_MAP_PATH, 'utf8');
for (const match of errorMapContent.matchAll(/:\s*'([^']+)'/g)) {
  const targetKey = match[1];
  if (!enCatalog.has(targetKey)) {
    errors.push(`errorMap.ts references missing catalog key '${targetKey}'`);
  }
}

function walkFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walkFiles(full));
    } else if (entry.name.endsWith('.vue') || entry.name.endsWith('.ts')) {
      out.push(full);
    }
  }
  return out;
}

for (const file of walkFiles(SRC_DIR)) {
  const content = fs.readFileSync(file, 'utf8');
  for (const match of content.matchAll(/\bt\(\s*'([^']+)'/g)) {
    const key = match[1];
    if (!enCatalog.has(key)) {
      errors.push(`${path.relative(ROOT, file)} references unknown catalog key '${key}'`);
    }
  }
}

if (errors.length > 0) {
  for (const err of errors) {
    console.error(`ERROR: ${err}`);
  }
  process.exit(1);
}

console.log(
  `OK: Verified ${enCatalog.size} translation keys across en.json and pseudo-dev.json, errorMap.ts, and Vue components.`
);
