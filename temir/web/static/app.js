(function () {
  const wsStatus = document.getElementById("ws-status");
  const costMeter = document.getElementById("cost-meter");
  const pipelineList = document.getElementById("pipeline-list");
  const strategyView = document.getElementById("strategy-view");
  const decisionView = document.getElementById("decision-view");
  const llmView = document.getElementById("llm-view");
  const patchView = document.getElementById("patch-view");
  const logView = document.getElementById("log-view");
  const spineTimeline = document.getElementById("spine-timeline");
  const btnClear = document.getElementById("btn-clear");
  const btnDemo = document.getElementById("btn-demo");
  const runIdInput = document.getElementById("run-id-input");
  const btnLoadRun = document.getElementById("btn-load-run");
  const replaySlider = document.getElementById("replay-slider");
  const btnReplayFirst = document.getElementById("btn-replay-first");
  const btnReplayPrev = document.getElementById("btn-replay-prev");
  const btnReplayNext = document.getElementById("btn-replay-next");
  const btnReplayLast = document.getElementById("btn-replay-last");
  const replayMeta = document.getElementById("replay-meta");
  const replayFocus = document.getElementById("replay-focus");
  const replayV2State = document.getElementById("replay-v2-state");

  let totalCost = 0;
  let loadedRunId = "";
  let replayV2Timer = null;
  /** @type {Array<Object>} */
  let replayBuffer = [];
  let replayIndex = 0;
  /** @type {Array<{id: string, label: string, state: string}>} */
  let pipelineDynamic = [];
  let llmTail = [];
  /** Live-only spine (до загрузки journal) */
  let spineLinesLive = [];

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = proto + "//" + window.location.host + "/ws";

  function spineLineFromMsg(msg) {
    const tid = msg.payload && msg.payload.task_id ? " task=" + msg.payload.task_id : "";
    return (
      (msg.seq != null ? "#" + msg.seq : "?") + " [" + (msg.topic || "?") + "]" + tid
    );
  }

  function renderPipelineDynamic() {
    if (!pipelineList) {
      return;
    }
    pipelineList.innerHTML = "";
    if (pipelineDynamic.length === 0) {
      const li = document.createElement("li");
      li.className = "pend";
      li.textContent = "[ ] Нет задач в состоянии — загрузите run или дождитесь task.* (демо: только WS в этом процессе).";
      pipelineList.appendChild(li);
      return;
    }
    pipelineDynamic.forEach(function (s) {
      const li = document.createElement("li");
      li.className = s.state;
      const mark = s.state === "done" ? "[ok] " : s.state === "run" ? "[*] " : s.state === "err" ? "[!] " : "[ ] ";
      li.textContent = mark + s.label;
      pipelineList.appendChild(li);
    });
  }

  function mutatePipelineState(msg) {
    const t = msg.topic;
    const p = msg.payload || {};
    if (t === "task.created") {
      const id = p.task_id;
      if (id && !pipelineDynamic.some(function (e) { return e.id === id; })) {
        pipelineDynamic.push({
          id: id,
          label: (p.executor || "?") + ": " + id,
          state: "pend",
        });
      }
      return;
    }
    if (t === "task.started") {
      const id = p.task_id;
      if (!id) {
        return;
      }
      const x = pipelineDynamic.find(function (e) { return e.id === id; });
      if (x) {
        x.state = "run";
        x.label = (p.executor || x.label.split(":")[0] || "?") + ": " + id;
      } else {
        pipelineDynamic.push({
          id: id,
          label: (p.executor || "?") + ": " + id,
          state: "run",
        });
      }
      return;
    }
    if (t === "task.completed") {
      const x = pipelineDynamic.find(function (e) { return e.id === p.task_id; });
      if (x) {
        x.state = "done";
      }
      return;
    }
    if (t === "task.failed" || t === "task.skipped") {
      const x = pipelineDynamic.find(function (e) { return e.id === p.task_id; });
      if (x) {
        x.state = "err";
      }
    }
  }

  function formatEventLine(obj) {
    return (
      (obj.ts || "") +
      " #" +
      (obj.seq != null ? obj.seq : "?") +
      " [" +
      (obj.topic || "?") +
      "] " +
      JSON.stringify(obj.payload ?? {})
    );
  }

  function appendLog(obj) {
    logView.textContent = formatEventLine(obj) + "\n" + logView.textContent;
  }

  function resetDerivedPanels() {
    totalCost = 0;
    costMeter.textContent = "$0.0000";
    decisionView.textContent = "—";
    patchView.textContent = "—";
    if (strategyView) {
      strategyView.textContent = "—";
    }
    llmTail = [];
    if (llmView) {
      llmView.textContent = "—";
    }
  }

  function updatePanels(msg) {
    if (msg.topic === "cost.tick" && msg.payload && typeof msg.payload.usd_delta === "number") {
      totalCost += msg.payload.usd_delta;
      costMeter.textContent = "$" + totalCost.toFixed(4);
    }
    if (msg.topic === "decision.strategy.selected" && msg.payload && strategyView) {
      strategyView.textContent = JSON.stringify(msg.payload, null, 2);
    }
    if (msg.topic === "decision.selected" || msg.topic === "decision.alternatives") {
      decisionView.textContent = JSON.stringify(msg.payload, null, 2);
    }
    if (msg.topic === "patch.proposed" && msg.payload) {
      if (msg.payload.diff) {
        patchView.textContent = msg.payload.diff;
      } else if (msg.payload.summary && msg.payload.summary.diff_preview) {
        patchView.textContent = msg.payload.summary.diff_preview;
      }
    }
    if (
      (msg.topic === "llm.requested" || msg.topic === "llm.completed") &&
      llmView
    ) {
      const short =
        spineLineFromMsg(msg) +
        " " +
        JSON.stringify(msg.payload || {}).slice(0, 160) +
        (JSON.stringify(msg.payload || {}).length > 160 ? "…" : "");
      llmTail.push(short);
      if (llmTail.length > 20) {
        llmTail = llmTail.slice(-20);
      }
      llmView.textContent = llmTail.join("\n");
    }
  }

  function handleEvent(msg) {
    appendLog(msg);
    updatePanels(msg);
    mutatePipelineState(msg);
    renderPipelineDynamic();
    if (!loadedRunId && spineTimeline) {
      spineLinesLive.push(spineLineFromMsg(msg));
      if (spineLinesLive.length > 800) {
        spineLinesLive = spineLinesLive.slice(-800);
      }
      spineTimeline.textContent = spineLinesLive.join("\n");
    }
  }

  function scheduleReplayV2State(rawIndex) {
    if (!loadedRunId || !replayV2State) {
      return;
    }
    if (replayV2Timer) {
      clearTimeout(replayV2Timer);
    }
    replayV2Timer = setTimeout(function () {
      replayV2Timer = null;
      const url =
        "/api/run/" +
        encodeURIComponent(loadedRunId) +
        "/replay/v2/state?raw_end_inclusive=" +
        encodeURIComponent(String(rawIndex));
      fetch(url)
        .then(function (r) {
          if (!r.ok) {
            throw new Error("HTTP " + r.status);
          }
          return r.json();
        })
        .then(function (data) {
          replayV2State.textContent = JSON.stringify(data.state, null, 2);
        })
        .catch(function (e) {
          replayV2State.textContent = "Replay v2: " + String(e);
        });
    }, 120);
  }

  function setReplayUiEnabled(on) {
    const ok = on && replayBuffer.length > 0;
    [replaySlider, btnReplayFirst, btnReplayPrev, btnReplayNext, btnReplayLast].forEach(function (el) {
      if (el) {
        el.disabled = !ok;
      }
    });
    if (!ok) {
      replayMeta.textContent = "";
    }
  }

  function applyReplayFrame(idx, skipSliderWrite) {
    if (replayBuffer.length === 0) {
      return;
    }
    replayIndex = Math.max(0, Math.min(idx, replayBuffer.length - 1));
    if (!skipSliderWrite && replaySlider) {
      replaySlider.value = String(replayIndex);
    }
    resetDerivedPanels();
    pipelineDynamic = [];
    const lines = [];
    const spineLines = [];
    for (let j = 0; j <= replayIndex; j++) {
      const msg = replayBuffer[j];
      lines.push(formatEventLine(msg));
      updatePanels(msg);
      mutatePipelineState(msg);
      spineLines.push(spineLineFromMsg(msg));
    }
    logView.textContent = lines.join("\n");
    renderPipelineDynamic();
    if (spineTimeline) {
      spineTimeline.textContent = spineLines.join("\n");
    }
    const cur = replayBuffer[replayIndex];
    const seqLabel = cur.seq != null ? cur.seq : replayIndex + 1;
    replayMeta.textContent =
      "step " +
      (replayIndex + 1) +
      " / " +
      replayBuffer.length +
      " | seq " +
      seqLabel +
      " | " +
      (cur.topic || "?") +
      " | run " +
      (cur.run_id || "?");
    replayFocus.textContent = JSON.stringify(cur, null, 2);
    scheduleReplayV2State(replayIndex);
  }

  function initReplayFromJournal(evs) {
    replayBuffer = evs.slice();
    if (replayBuffer.length === 0) {
      setReplayUiEnabled(false);
      replayFocus.textContent = "No events in journal.";
      if (replayV2State) {
        replayV2State.textContent = "No events in journal.";
      }
      return;
    }
    replaySlider.min = "0";
    replaySlider.max = String(replayBuffer.length - 1);
    setReplayUiEnabled(true);
    applyReplayFrame(replayBuffer.length - 1);
  }

  function connect() {
    const sock = new WebSocket(wsUrl);
    sock.onopen = function () {
      wsStatus.textContent = "WS: online";
      wsStatus.classList.remove("offline");
      wsStatus.classList.add("online");
    };
    sock.onclose = function () {
      wsStatus.textContent = "WS: offline";
      wsStatus.classList.remove("online");
      wsStatus.classList.add("offline");
      setTimeout(connect, 2000);
    };
    sock.onmessage = function (ev) {
      try {
        handleEvent(JSON.parse(ev.data));
      } catch (e) {
        appendLog({ topic: "parse.error", payload: { raw: ev.data }, ts: new Date().toISOString() });
      }
    };
    window.__temirWs = sock;
  }

  btnClear.addEventListener("click", function () {
    logView.textContent = "";
    if (!loadedRunId) {
      spineLinesLive = [];
      if (spineTimeline) {
        spineTimeline.textContent = "";
      }
    }
  });

  btnLoadRun.addEventListener("click", async function () {
    const id = runIdInput && runIdInput.value ? runIdInput.value.trim() : "";
    if (!id) {
      return;
    }
    try {
      const res = await fetch("/api/run/" + encodeURIComponent(id));
      if (!res.ok) {
        appendLog({
          topic: "journal.load.error",
          payload: { status: res.status, run_id: id },
          ts: new Date().toISOString(),
        });
        return;
      }
      const data = await res.json();
      const evs = data.events || [];
      loadedRunId = id;
      initReplayFromJournal(evs);
    } catch (e) {
      appendLog({
        topic: "journal.load.error",
        payload: { error: String(e) },
        ts: new Date().toISOString(),
      });
    }
  });

  if (replaySlider) {
    replaySlider.addEventListener("input", function () {
      applyReplayFrame(parseInt(replaySlider.value, 10), true);
    });
  }
  if (btnReplayFirst) {
    btnReplayFirst.addEventListener("click", function () {
      applyReplayFrame(0);
    });
  }
  if (btnReplayPrev) {
    btnReplayPrev.addEventListener("click", function () {
      applyReplayFrame(replayIndex - 1);
    });
  }
  if (btnReplayNext) {
    btnReplayNext.addEventListener("click", function () {
      applyReplayFrame(replayIndex + 1);
    });
  }
  if (btnReplayLast) {
    btnReplayLast.addEventListener("click", function () {
      applyReplayFrame(replayBuffer.length - 1);
    });
  }

  btnDemo.addEventListener("click", function () {
    const sock = window.__temirWs;
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(
        JSON.stringify({
          type: "demo",
          decision: {
            chosen: "pip install httpx",
            confidence: 0.91,
            alternatives: [
              { action: "rewrite import", score: 0.42 },
              { action: "mock module", score: 0.21 },
            ],
          },
        }),
      );
    }
  });

  setReplayUiEnabled(false);
  renderPipelineDynamic();
  connect();
})();
