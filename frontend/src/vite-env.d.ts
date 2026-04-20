/// <reference types="vite/client" />

declare module '*.css';
declare module '*.svg' {
  const src: string;
  export default src;
}
/* `@fontsource-variable/*` packages (Geist, Geist Mono) are pure CSS —
 * the package's `exports` map resolves the bare specifier to
 * `index.css`. TypeScript's generic `*.css` declaration doesn't cover
 * node_modules exports-map resolution for bare specifiers, so we
 * declare the prefix explicitly to allow the side-effect imports in
 * `main.tsx`. */
declare module '@fontsource-variable/*';
