import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Built straight into the plugin tree so build.sh packages whatever is there.
// manifest: true is load-bearing - common.php's um_asset_tags() resolves the
// hashed filenames from it at run time, exactly as Unraid does for its own
// bundle, so no hash is ever written into a .page.
export default defineConfig({
  plugins: [vue()],
  base: '/plugins/unraid-manager/ui/',
  build: {
    outDir: '../source/usr/local/emhttp/plugins/unraid-manager/ui',
    emptyOutDir: true,
    manifest: true,
    rollupOptions: { input: 'src/main.js' },
    // Nobody debugs this from a production box, and a source map would double
    // the shipped bytes against a 250 KB budget.
    sourcemap: false,
  },
})
