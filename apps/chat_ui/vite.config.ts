import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/') || id.includes('node_modules/scheduler')) {
            return 'react-vendor'
          }
          if (
            id.includes('node_modules/react-markdown') ||
            id.includes('node_modules/remark-') ||
            id.includes('node_modules/rehype-') ||
            id.includes('node_modules/micromark') ||
            id.includes('node_modules/mdbriefcase') ||
            id.includes('node_modules/mdast') ||
            id.includes('node_modules/unified') ||
            id.includes('node_modules/unist-') ||
            id.includes('node_modules/vfile') ||
            id.includes('node_modules/bail') ||
            id.includes('node_modules/trough') ||
            id.includes('node_modules/character-entitlement') ||
            id.includes('node_modules/decode-named-character') ||
            id.includes('node_modules/character-reference') ||
            id.includes('node_modules/hast-util') ||
            id.includes('node_modules/property-information') ||
            id.includes('node_modules/space-separated') ||
            id.includes('node_modules/comma-separated') ||
            id.includes('node_modules/estree-util') ||
            id.includes('node_modules acorn') ||
            id.includes('node_modules/ccount') ||
            id.includes('node_modules/escape-string') ||
            id.includes('node_modules/is-alphabetical') ||
            id.includes('node_modules/is-alphanumerical') ||
            id.includes('node_modules/is-buffer') ||
            id.includes('node_modules/is-decimal') ||
            id.includes('node_modules/is-plain-obj') ||
            id.includes('node_modules/is-reference') ||
            id.includes('node_modules/is-alphabetical') ||
            id.includes('node_modules/devlop') ||
            id.includes('node_modules/longest-streak') ||
            id.includes('node_modules/mz/') ||
            id.includes('node_modules/extend') ||
            id.includes('node_modules/is-core-module') ||
            id.includes('node_modules/parse-entities') ||
            id.includes('node_modules/stringify-entities') ||
            id.includes('node_modules/character-entities') ||
            id.includes('node_modules/zwitch') ||
            id.includes('node_modules/unist-util') ||
            id.includes('node_modules/markdown-table') ||
            id.includes('node_modules/mdast-util')
          ) {
            return 'markdown'
          }
          if (id.includes('node_modules/framer-motion') || id.includes('node_modules/motion-dom') || id.includes('node_modules/motion-utils')) {
            return 'motion'
          }
          if (id.includes('node_modules/katex')) {
            return 'katex'
          }
        },
      },
    },
  },
})
