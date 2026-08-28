// Real proof of what NodeCard.vue actually RENDERS - not a source-text grep.
// Fix round 1, item 5: null-vs-zero, empty-array-vs-0%, and unknown-vs-failed
// are facts about output, and a comment sitting next to the right branch is
// enough to fool a grep (two of frontend_test.php's checks were, until this
// round). Compiles NodeCard.vue (and its child StatusChip.vue) with the same
// @vue/compiler-sfc Vite's own plugin uses, SSR-renders real fixtures with
// @vue/server-renderer, and asserts on the resulting HTML string. Costs no
// new dependency: both packages are already in frontend/node_modules,
// transitively via `vue` and `@vitejs/plugin-vue`.
//   node tests/js/node_card.mjs   ->   "node_card: all pass" (exit 0)
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.join(here, '..', '..', 'frontend')
const componentsDir = path.join(frontend, 'src', 'components')

// Explicit resolved paths, not bare specifiers (this file lives outside
// frontend/, so a bare `import 'vue'` would never find frontend/node_modules)
// - the same reason live_singleton.mjs resolves `vue/index.mjs` by hand.
const compilerSfcPath = path.join(frontend, 'node_modules', '@vue', 'compiler-sfc', 'dist', 'compiler-sfc.esm-browser.js')
const vueEntryPath = path.join(frontend, 'node_modules', 'vue', 'index.mjs')
const serverRendererPath = path.join(frontend, 'node_modules', 'vue', 'server-renderer', 'index.mjs')

let fails = 0
function check (name, ok) {
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name)
  if (!ok) fails++
}

const { parse, compileScript, compileTemplate } = await import(pathToFileURL(compilerSfcPath).href)
const { createSSRApp } = await import(pathToFileURL(vueEntryPath).href)
const { renderToString } = await import(pathToFileURL(serverRendererPath).href)

// A scratch dir INSIDE frontend/node_modules: the compiled output's own
// `import ... from 'vue'` (emitted by compileScript/compileTemplate as bare
// specifiers, out of our control) only resolves if the file that contains it
// sits somewhere under frontend/ - Node walks up from a module's own
// location looking for a node_modules directory, and frontend/node_modules
// is the one that has to be found. Already covered by the blanket
// node_modules .gitignore entry.
const cacheDir = fs.mkdtempSync(path.join(frontend, 'node_modules', '.node-card-ssr-'))
const compiled = new Map() // absolute .vue path -> compiled .mjs path

// Compiles one SFC to a plain ESM module with an attached `ssrRender`,
// recursing into any relative `./Foo.vue` import (NodeCard.vue imports
// StatusChip.vue) and rewriting that import to point at ITS compiled output.
function compileSFCFile (absVuePath) {
  const cached = compiled.get(absVuePath)
  if (cached) return cached
  const source = fs.readFileSync(absVuePath, 'utf-8')
  const id = path.basename(absVuePath)
  const { descriptor } = parse(source, { filename: absVuePath })
  const script = compileScript(descriptor, { id })
  const tmpl = compileTemplate({
    source: descriptor.template.content,
    filename: absVuePath,
    id,
    ssr: true,
    ssrCssVars: [],
    // Links the template compile to the script's local bindings (imported
    // components, setup consts) so e.g. <StatusChip> resolves to the
    // imported variable directly instead of a runtime component lookup that
    // has nothing registered to find - exactly what @vitejs/plugin-vue does
    // internally to join the two halves of one <script setup> SFC.
    compilerOptions: { bindingMetadata: script.bindings },
  })

  // compileScript's own output is `export default { ... }`; rename so we can
  // attach ssrRender to the same object before re-exporting it.
  let scriptCode = script.content.replace('export default', 'const _sfc_main =')
  scriptCode = scriptCode.replace(/from\s+(['"])(\.[^'"]+?\.vue)\1/g, (whole, quote, rel) => {
    const depAbs = path.resolve(path.dirname(absVuePath), rel)
    const depOut = compileSFCFile(depAbs)
    return `from ${quote}${pathToFileURL(depOut).href}${quote}`
  })
  const ssrCode = tmpl.code.replace('export function ssrRender', 'function ssrRender')

  const outPath = path.join(cacheDir, id.replace(/\.vue$/, '') + '.mjs')
  fs.writeFileSync(outPath, `${scriptCode}\n${ssrCode}\n_sfc_main.ssrRender = ssrRender\nexport default _sfc_main\n`)
  compiled.set(absVuePath, outPath)
  return outPath
}

const nodeCardOut = compileSFCFile(path.join(componentsDir, 'NodeCard.vue'))
const { default: NodeCard } = await import(pathToFileURL(nodeCardOut).href)

// SSR preserves Vue template comments as literal HTML comments in the
// output - discovered here because NodeCard.vue's own "not 0% of something"
// explanatory comment then contains the exact substring the array_empty
// check below hunts for, false-failing it for a reason that has nothing to
// do with what actually rendered. The whole point of this harness is to
// assert on rendered OUTPUT, not on prose that rode along in the markup, so
// strip it the same way frontend_test.php's vue_code_only() strips it from
// source.
function renderedTextOnly (html) {
  return html.replace(/<!--[\s\S]*?-->/g, '')
}

async function renderCard (node) {
  const app = createSSRApp(NodeCard, { node })
  return renderedTextOnly(await renderToString(app))
}

function baseNode (overrides = {}) {
  return {
    id: 'n1', name: 'Raven', state: 'ok', since: null, array_state: 'started',
    array_empty: false, capacity: { used: 100, total: 200 },
    unraid: '6.12.9', api: '4.1.2', booted_at: null, last_seen: null,
    unread: null, indicators: {},
    ...overrides,
  }
}

try {
  /* ── null-vs-zero (amendment A) ─────────────────────────────────────── */
  const htmlNull = await renderCard(baseNode({ unread: null }))
  const htmlZero = await renderCard(baseNode({ unread: { alert: 0, warning: 0, info: 0 } }))
  check('unread: null and unread: {alert:0,warning:0,info:0} render differently',
        htmlNull !== htmlZero)
  check('unread: null renders the "we have not heard" treatment, not a zero count',
        htmlNull.includes('um-unknown') && !htmlNull.includes('0 alert'))
  check('unread: {0,0,0} renders the real zero breakdown, not the unknown treatment',
        htmlZero.includes('0 alert') && htmlZero.includes('0 warn') && htmlZero.includes('0 info'))

  /* ── empty-array-vs-0% (Raven, constraint 3) ──────────────────────────── */
  const htmlEmpty = await renderCard(baseNode({ array_empty: true, capacity: { used: 0, total: 0 } }))
  check('array_empty renders "empty array"', htmlEmpty.includes('empty array'))
  check('array_empty never renders "0%" (the exact regression verified wrong on Raven)',
        !htmlEmpty.includes('0%'))

  /* ── unknown-vs-failed: an unknown indicator must not colour the head chip ─ */
  const htmlUnknownIndicator = await renderCard(baseNode({
    state: 'ok',
    indicators: { disk_temp: { state: 'unknown', value: null, basis: 'no sensor', since: null } },
  }))
  const head = htmlUnknownIndicator.match(/<div class="um-card-head">[\s\S]*?<\/div>/)
  assert.ok(head, 'um-card-head not found in rendered output')
  check('a node.state="ok" head chip stays OK even when one indicator is unknown',
        head[0].includes('um-ok') && !head[0].includes('um-unknown'))
} finally {
  fs.rmSync(cacheDir, { recursive: true, force: true })
}

console.log(fails === 0 ? 'node_card: all pass' : `node_card: ${fails} FAILED`)
process.exit(fails === 0 ? 0 : 1)
