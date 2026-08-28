// The SFC compile+render rig shared by every .vue render harness in this
// directory. Extracted from node_card.mjs when Tasks 14/15 needed the same
// sixty lines a second and a third time - one copy that both harnesses import,
// rather than three that can drift apart.
//
// Compiles a .vue file with the same @vue/compiler-sfc Vite's own plugin uses,
// SSR-renders it with @vue/server-renderer, and hands back the HTML string.
// Costs no new dependency: both packages are already in frontend/node_modules,
// transitively via `vue` and `@vitejs/plugin-vue`.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
export const frontend = path.join(here, '..', '..', 'frontend')

// Explicit resolved paths, not bare specifiers (these files live outside
// frontend/, so a bare `import 'vue'` would never find frontend/node_modules)
// - the same reason live_singleton.mjs resolves `vue/index.mjs` by hand.
const compilerSfcPath = path.join(frontend, 'node_modules', '@vue', 'compiler-sfc', 'dist', 'compiler-sfc.esm-browser.js')
const vueEntryPath = path.join(frontend, 'node_modules', 'vue', 'index.mjs')
const serverRendererPath = path.join(frontend, 'node_modules', 'vue', 'server-renderer', 'index.mjs')

const { parse, compileScript, compileTemplate } = await import(pathToFileURL(compilerSfcPath).href)
const { createSSRApp } = await import(pathToFileURL(vueEntryPath).href)
const { renderToString } = await import(pathToFileURL(serverRendererPath).href)

// SSR preserves Vue template comments as literal HTML comments in the output -
// discovered in node_card.mjs, where a component's own explanatory comment
// contained the exact substring a check was hunting for and false-passed it.
// These harnesses assert on rendered OUTPUT, not on prose that rode along in
// the markup, so strip it the same way frontend_test.php's vue_code_only()
// strips it from source.
export function renderedTextOnly (html) {
  return html.replace(/<!--[\s\S]*?-->/g, '')
}

/* Builds a compiler bound to its own scratch dir.
     stubs: { './relative/specifier.js': "module source" } - any import in a
     compiled SFC matching a key is rewritten to a generated module with that
     source. Views import ../api.js and ../live.js, which open a real fetch, a
     real EventSource and two real setIntervals at call time; stubbing them is
     what lets a fixture be rendered at all. */
export function createCompiler ({ stubs = {} } = {}) {
  // A scratch dir INSIDE frontend/node_modules: the compiled output's own
  // `import ... from 'vue'` (emitted by compileScript/compileTemplate as bare
  // specifiers, out of our control) only resolves if the file that contains it
  // sits somewhere under frontend/ - Node walks up from a module's own location
  // looking for a node_modules directory, and frontend/node_modules is the one
  // that has to be found. Already covered by the blanket node_modules
  // .gitignore entry.
  const cacheDir = fs.mkdtempSync(path.join(frontend, 'node_modules', '.um-ssr-'))
  const compiled = new Map() // absolute .vue path -> compiled .mjs path
  const stubFiles = new Map() // specifier -> generated .mjs path

  function stubFor (specifier) {
    if (!stubFiles.has(specifier)) {
      const out = path.join(cacheDir, 'stub-' + specifier.replace(/[^a-z0-9]+/gi, '-') + '.mjs')
      fs.writeFileSync(out, stubs[specifier])
      stubFiles.set(specifier, out)
    }
    return stubFiles.get(specifier)
  }

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
      // components, setup consts) so e.g. <StatusChip> resolves to the imported
      // variable directly instead of a runtime component lookup that has
      // nothing registered to find - exactly what @vitejs/plugin-vue does
      // internally to join the two halves of one <script setup> SFC.
      compilerOptions: { bindingMetadata: script.bindings },
    })

    // compileScript's own output is `export default { ... }`; rename so we can
    // attach ssrRender to the same object before re-exporting it.
    let scriptCode = script.content.replace('export default', 'const _sfc_main =')
    scriptCode = scriptCode.replace(/from\s+(['"])(\.[^'"]+?)\1/g, (whole, quote, rel) => {
      if (Object.prototype.hasOwnProperty.call(stubs, rel)) {
        return `from ${quote}${pathToFileURL(stubFor(rel)).href}${quote}`
      }
      const depAbs = path.resolve(path.dirname(absVuePath), rel)
      if (rel.endsWith('.vue')) {
        return `from ${quote}${pathToFileURL(compileSFCFile(depAbs)).href}${quote}`
      }
      // A plain .js sibling that is not stubbed (time.js): point at the REAL
      // file. Left as a relative specifier it resolves against the scratch dir
      // inside frontend/node_modules and fails with ERR_MODULE_NOT_FOUND -
      // loudly, but for a reason that has nothing to do with the test.
      if (fs.existsSync(depAbs)) {
        return `from ${quote}${pathToFileURL(depAbs).href}${quote}`
      }
      return whole
    })
    const ssrCode = tmpl.code.replace('export function ssrRender', 'function ssrRender')

    const outPath = path.join(cacheDir, id.replace(/\.vue$/, '') + '.mjs')
    fs.writeFileSync(outPath, `${scriptCode}\n${ssrCode}\n_sfc_main.ssrRender = ssrRender\nexport default _sfc_main\n`)
    compiled.set(absVuePath, outPath)
    return outPath
  }

  async function load (absVuePath) {
    const out = compileSFCFile(absVuePath)
    return (await import(pathToFileURL(out).href)).default
  }

  async function render (component, props) {
    return renderedTextOnly(await renderToString(createSSRApp(component, props)))
  }

  return { compileSFCFile, load, render, cleanup: () => fs.rmSync(cacheDir, { recursive: true, force: true }) }
}

export function reporter (suite) {
  let fails = 0
  return {
    check (name, ok) {
      console.log((ok ? 'PASS  ' : 'FAIL  ') + name)
      if (!ok) fails++
    },
    done () {
      console.log(fails === 0 ? `${suite}: all pass` : `${suite}: ${fails} FAILED`)
      process.exit(fails === 0 ? 0 : 1)
    },
  }
}
