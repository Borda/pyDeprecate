"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../docs/javascripts/try-it.js"), "utf8");

function element() {
  return {
    listeners: {},
    value: "",
    textContent: "",
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
    appendChild(child) {
      this.value ||= child.value;
    },
  };
}

function startWidget() {
  const elements = Object.fromEntries(
    ["", "-example", "-code", "-run", "-stop", "-status", "-output"].map((suffix) => ["try-it" + suffix, element()]),
  );
  const workers = [];
  class MockWorker {
    constructor(url, options) {
      this.url = url;
      this.type = options.type;
      this.messages = [];
      this.terminated = false;
      this.listeners = {};
      workers.push(this);
    }
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    }
    postMessage(message) {
      this.messages.push(message);
    }
    terminate() {
      this.terminated = true;
    }
    emit(data) {
      this.listeners.message({ data });
    }
  }
  const url = "https://docs.example/javascripts/try-it.js";
  vm.runInNewContext(source, {
    Worker: MockWorker,
    document: {
      currentScript: { src: url },
      createElement: element,
      getElementById: (id) => elements[id],
      readyState: "complete",
    },
  });
  return {
    select: elements["try-it-example"],
    editor: elements["try-it-code"],
    run: elements["try-it-run"],
    stop: elements["try-it-stop"],
    output: elements["try-it-output"],
    workers,
    url,
  };
}

test("Run submits the click-time text while the worker starts and releases controls on completion", () => {
  const widget = startWidget();
  assert.equal(widget.workers.length, 0);
  widget.editor.value = "print('submitted')";
  widget.run.listeners.click();
  const [worker] = widget.workers;
  assert.ok(worker, "Run starts a worker");
  assert.equal(worker.url, widget.url);
  assert.equal(worker.type, "module");
  assert.equal(widget.select.disabled, true);
  assert.equal(widget.editor.readOnly, true);
  widget.editor.value = "print('later edit')";
  worker.emit({ type: "status", text: "Loading…" });
  assert.equal(worker.messages.length, 1);
  assert.equal(worker.messages[0].code, "print('submitted')");
  worker.emit({ type: "output", text: "submitted" });
  worker.emit({ type: "done" });
  assert.equal(widget.output.textContent, "submitted\n");
  assert.equal(widget.run.disabled, false);
  assert.equal(widget.select.disabled, false);
  assert.equal(widget.editor.readOnly, false);
});

test("Stop terminates the worker and a new run ignores its queued output", () => {
  const widget = startWidget();
  widget.run.listeners.click();
  const [first] = widget.workers;
  assert.ok(first, "Run starts a worker");
  assert.equal(widget.stop.disabled, false);
  widget.stop.listeners.click();
  assert.equal(first.terminated, true);
  assert.equal(widget.stop.disabled, true);
  widget.run.listeners.click();
  const second = widget.workers[1];
  assert.ok(second, "A new run starts a replacement worker");
  first.emit({ type: "output", text: "stale" });
  first.emit({ type: "done" });
  assert.equal(widget.output.textContent, "");
  assert.equal(widget.run.disabled, true);
  second.emit({ type: "output", text: "current" });
  second.emit({ type: "done" });
  assert.equal(widget.output.textContent, "current\n");
  assert.equal(widget.run.disabled, false);
});

test("a completed run cannot write to the next run", () => {
  const widget = startWidget();
  widget.run.listeners.click();
  const [first] = widget.workers;
  first.emit({ type: "done" });
  assert.equal(first.terminated, true);
  widget.run.listeners.click();
  const second = widget.workers[1];
  assert.ok(second, "Run starts a fresh Python session");
  first.emit({ type: "output", text: "delayed output" });
  assert.equal(widget.output.textContent, "");
  second.emit({ type: "output", text: "second run" });
  second.emit({ type: "done" });
  assert.equal(widget.output.textContent, "second run\n");
});

// Pyodide 314 keeps an unterminated line (`print("x", end="")`) out of a batched handler until the stream is fsynced:
// `sys.stdout.flush()` alone does not deliver it. The fake models that, so a run that ends without the fsync loses it.
function fakePyodide(run) {
  const state = { pending: "", handler: null, destroyed: 0 };
  return {
    state,
    setStdout(options) {
      state.handler = options.batched;
    },
    setStderr() {},
    globals: {
      get: () => () => ({ destroy: () => (state.destroyed += 1) }),
    },
    async runPythonAsync() {
      return run(state);
    },
    runPython(code) {
      if (code.includes("os.fsync(") && state.pending) {
        state.handler(state.pending);
        state.pending = "";
      }
    },
  };
}

function loadRunCode() {
  const mod = { exports: {} };
  vm.runInNewContext(source, { module: mod });
  return mod.exports.runCode;
}

test("an unterminated printed line is delivered before the run completes", async () => {
  const pyodide = fakePyodide((state) => {
    state.pending = "result";
  });
  const lines = [];
  await loadRunCode()(pyodide, 'print("result", end="")', (line) => lines.push(line));
  assert.deepEqual(lines, ["result"]);
  assert.equal(pyodide.state.destroyed, 2, "the flush namespace and the run namespace are both released");
});

test("partial output survives a run that raises, and the run's own error is kept", async () => {
  const pyodide = fakePyodide((state) => {
    state.pending = "partial";
    throw new Error("boom");
  });
  const lines = [];
  await assert.rejects(
    loadRunCode()(pyodide, "...", (line) => lines.push(line)),
    /boom/,
  );
  assert.deepEqual(lines, ["partial"]);
});
