import { createApp } from 'vue'
import App from './App.vue'
import './styles/tokens.css'

// The .page provides the mount point. Bail quietly when it is absent - nothing
// else on the webGUI hosts this bundle, and a thrown error there would land in
// someone else's console.
const mount = document.getElementById('um-app')
if (mount) createApp(App).mount(mount)
