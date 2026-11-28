/*
 * Run pyDeprecate examples in the browser for docs/try-it.md.
 *
 * Each run starts a dedicated module worker using this file as its entry point. The worker loads Pyodide and installs
 * pyDeprecate. The page does nothing unless it contains the #try-it widget.
 */
(function () {
  "use strict";

  var scriptUrl = typeof document !== "undefined" && document.currentScript.src;

  // Pin the Pyodide release: a new release must not change what the page runs.
  var PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

  var EXAMPLES = {
    forward: {
      label: "Rename a function",
      code: [
        "from deprecate import deprecated",
        "",
        "",
        "# NEW API — the maintained implementation",
        "def compute_total(items: list, tax: float = 0.2) -> float:",
        "    return sum(items) * (1 + tax)",
        "",
        "",
        "# DEPRECATED API — `calc_total` forwards to `compute_total`",
        '@deprecated(target=compute_total, deprecated_in="1.0", remove_in="2.0")',
        "def calc_total(items: list, tax: float = 0.2) -> float:",
        "    pass  # never runs: calls are forwarded",
        "",
        "",
        "print(calc_total([1, 2], tax=0.5))  # warns: FutureWarning",
        "",
      ].join("\n"),
    },
    argument: {
      label: "Rename an argument",
      code: [
        "from deprecate import TargetMode, deprecated",
        "",
        "",
        "# NEW API — only the replacement argument remains in the signature",
        "@deprecated(",
        "    target=TargetMode.ARGS_REMAP,",
        '    args_mapping={"num_proc": "workers"},',
        '    deprecated_in="1.4",',
        '    remove_in="2.0",',
        ")",
        "def load_dataset(path: str, *, workers: int = 0) -> dict:",
        '    return {"path": path, "workers": workers}',
        "",
        "",
        'print(load_dataset("data", num_proc=4))  # warns: FutureWarning',
        'print(load_dataset("data", workers=2))  # no warning: the new name',
        "",
      ].join("\n"),
    },
    cls: {
      label: "Rename a class",
      code: [
        "from deprecate import deprecated_class",
        "",
        "",
        "# NEW API — the class callers should move to",
        "class ImageClassifier:",
        "    def __init__(self, classes: int) -> None:",
        "        self.classes = classes",
        "",
        "",
        "# DEPRECATED API — the old name builds the new class and warns",
        '@deprecated_class(target=ImageClassifier, deprecated_in="1.4", remove_in="2.0")',
        "class Classifier:",
        "    pass",
        "",
        "",
        "model = Classifier(classes=10)  # warns: FutureWarning",
        "print(type(model).__name__, model.classes)",
        "",
      ].join("\n"),
    },
    config: {
      label: "Read the stored configuration",
      code: [
        "from deprecate import deprecated, get_deprecation_config",
        "",
        "",
        "def compute_total(items: list) -> float:",
        "    return sum(items)",
        "",
        "",
        '@deprecated(target=compute_total, deprecated_in="1.0", remove_in="2.0")',
        "def calc_total(items: list) -> float:",
        "    pass",
        "",
        "",
        "config = get_deprecation_config(calc_total)",
        "print(config.name, config.deprecated_in, config.remove_in, config.target.__name__)",
        "",
      ].join("\n"),
    },
  };

  // Run `code` in a fresh namespace so every run starts with new warning registries and warning budgets.
  // `pyodide` is a loaded Pyodide instance; `write` receives each line printed to stdout or stderr.
  async function runCode(pyodide, code, write) {
    pyodide.setStdout({ batched: write });
    pyodide.setStderr({ batched: write });
    var namespace = pyodide.globals.get("dict")();
    try {
      await pyodide.runPythonAsync(code, { globals: namespace });
    } finally {
      namespace.destroy();
    }
  }

  async function loadRuntime(setStatus) {
    setStatus("Loading Python…");
    var loaded = await import(PYODIDE_URL);
    var pyodide = await loaded.loadPyodide();
    setStatus("Installing pyDeprecate from PyPI…");
    await pyodide.loadPackage("micropip");
    await pyodide.pyimport("micropip").install("pyDeprecate");
    return pyodide;
  }

  if (typeof document === "undefined" && typeof self !== "undefined") {
    self.addEventListener("message", async function (event) {
      function send(type, text) {
        self.postMessage({ type: type, text: text });
      }

      try {
        var pyodide = await loadRuntime(function (text) {
          send("status", text);
        });
        send("status", "Running…");
        await runCode(pyodide, event.data.code, function (line) {
          send("output", line);
        });
        send("done");
      } catch (error) {
        send("failed", error.message || String(error));
      }
    });
    return;
  }

  function init() {
    var root = document.getElementById("try-it");
    if (!root) {
      return;
    }
    var select = document.getElementById("try-it-example");
    var editor = document.getElementById("try-it-code");
    var button = document.getElementById("try-it-run");
    var stop = document.getElementById("try-it-stop");
    var status = document.getElementById("try-it-status");
    var output = document.getElementById("try-it-output");

    Object.keys(EXAMPLES).forEach(function (key) {
      var option = document.createElement("option");
      option.value = key;
      option.textContent = EXAMPLES[key].label;
      select.appendChild(option);
    });
    editor.value = EXAMPLES[select.value].code;
    root.hidden = false;

    var worker = null;

    function setRunning(running) {
      button.disabled = running;
      select.disabled = running;
      editor.readOnly = running;
      stop.disabled = !running;
    }

    function finish(message) {
      // Stop background Python work before the next run can accept any output.
      if (worker) {
        worker.terminate();
        worker = null;
      }
      status.textContent = message;
      setRunning(false);
    }

    function onMessage(event) {
      var data = event.data;
      switch (data.type) {
        case "status":
          status.textContent = data.text;
          break;
        case "output":
          output.textContent += data.text + "\n";
          break;
        case "done":
          finish("Done.");
          break;
        case "failed":
          output.textContent += data.text;
          finish("Run failed.");
          break;
      }
    }

    function createWorker() {
      var nextWorker = new Worker(scriptUrl, { type: "module" });
      nextWorker.addEventListener("message", function (event) {
        // A terminated worker can have messages queued from an earlier run.
        if (worker === nextWorker && button.disabled) {
          onMessage(event);
        }
      });
      nextWorker.addEventListener("error", function (event) {
        if (worker === nextWorker) {
          output.textContent += event.message || "Could not start the Python worker.";
          finish("Run failed.");
        }
      });
      return nextWorker;
    }

    select.addEventListener("change", function () {
      if (!button.disabled) {
        editor.value = EXAMPLES[select.value].code;
        output.textContent = "";
      }
    });

    function run() {
      if (button.disabled) {
        return;
      }
      var code = editor.value;
      setRunning(true);
      output.textContent = "";
      status.textContent = "Starting Python…";
      try {
        worker = createWorker();
        worker.postMessage({ code: code });
      } catch (error) {
        output.textContent = error.message || String(error);
        finish("Run failed.");
      }
    }

    button.addEventListener("click", run);
    stop.addEventListener("click", function () {
      finish("Stopped.");
    });
    editor.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        if (!button.disabled) {
          run();
        }
      }
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
})();
