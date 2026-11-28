---
id: try-it
description: Try pyDeprecate in your browser with no install. Pick an example, edit it, run it, and see the FutureWarning and the forwarded result.
---

# Try it in your browser

Run pyDeprecate without installing anything. Pick an example, edit it, and press **Run**.

<div class="try-it" id="try-it" hidden>
  <label for="try-it-example">Example</label>
  <select id="try-it-example"></select>
  <textarea id="try-it-code" spellcheck="false" rows="18" aria-label="Python code"></textarea>
  <div class="try-it-bar">
    <button id="try-it-run" type="button" class="md-button md-button--primary">Run</button>
    <button id="try-it-stop" type="button" class="md-button" disabled>Stop</button>
    <span id="try-it-status" role="status"></span>
  </div>
  <pre id="try-it-output" aria-live="polite"></pre>
</div>
<noscript>This page needs JavaScript to run the examples. The same code works in any Python 3.9+ environment after `pip install pyDeprecate`.</noscript>

The page runs Python in your browser with [Pyodide](https://pyodide.org/), then installs pyDeprecate from PyPI. Each **Run** starts a fresh Python session. Your browser can cache the runtime and package downloads. Press **Stop** to end the session and cancel any work still in progress. The page itself does not upload code from the editor. **Executed snippets can make network requests**, so run only code you trust. Press Ctrl+Enter or Cmd+Enter in the editor to run.

## What to look for

- **The warning names both versions and the replacement.** The default `FutureWarning` says what was deprecated, in which release, what replaces it and when it goes away. Line numbers refer to the editor.
- **The deprecated body never runs.** In the first example the function body is `pass`, yet the call returns the replacement's result.
- **The new name is silent.** In the argument example only the call that still uses `num_proc` warns.

Change the version numbers, the target or the argument mapping and run again. Python errors appear in the output panel.

## Next steps

- [Getting Started](getting-started.md) installs the package and walks through the decorator.
- [Convert existing deprecations](adoption/convert-existing.md) turns hand-written warnings into wrappers.
