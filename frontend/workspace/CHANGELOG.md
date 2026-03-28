<instructions>
## 🚨 MANDATORY: CHANGELOG TRACKING 🚨

You MUST maintain this file to track your work across messages. This is NON-NEGOTIABLE.

---

## INSTRUCTIONS

- **MAX 5 lines** per entry - be concise but informative
- **Include file paths** of key files modified or discovered
- **Note patterns/conventions** found in the codebase
- **Sort entries by date** in DESCENDING order (most recent first)
- If this file gets corrupted, messy, or unsorted -> re-create it. 
- CRITICAL: Updating this file at the END of EVERY response is MANDATORY.
- CRITICAL: Keep this file under 300 lines. You are allowed to summarize, change the format, delete entries, etc., in order to keep it under the limit.

</instructions>

<changelog>

## 2026-03-28 (responsive fix)
- Fixed `.desktop` hard-coded `width: 1280px` → `width: 100%` causing page to be cut and pushed right
- Made `.hero`, `.hero-information`, `.hacemos-que-el`, `.element-el-shoper-cunta-lo` fully fluid with `clamp()` and `%` units
- Fixed `.navigation` with `right: 0`, `flex-wrap`, `z-index`, and `box-sizing` for proper full-width sticky nav
- Added `@media` breakpoints at 768px and 480px in `style.css`
- Added `overflow-x: hidden` to both `body` and `.desktop`

## 2026-03-28
- Replaced `heading-como` placeholder with real logo image + real H2 heading + real subtitle paragraph
- Modified `index.html`: added `<img>`, `<h2>`, `<p>` inside `.heading-como`
- Modified `style.css`: updated `.heading-como` to column flex, added `.heading-como-img` and `.heading-como-sub` styles

</changelog>
