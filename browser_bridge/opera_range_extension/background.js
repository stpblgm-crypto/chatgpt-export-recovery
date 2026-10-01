importScripts("config.js");

const cfg = globalThis.RECOVERY_CONFIG;
const stateKey = "chatgptExportRangeBridgeState";

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getState() {
  const obj = await chrome.storage.local.get(stateKey);
  return obj[stateKey] || {
    status: "INIT",
    nextStart: cfg.start,
    chunkIndex: 0,
    completed: [],
    error: null
  };
}

async function setState(state) {
  await chrome.storage.local.set({[stateKey]: state});
}

async function discoverExportUrl() {
  const rows = await chrome.downloads.search({orderBy:["-startTime"], limit:100});
  for (const row of rows) {
    try {
      const u = new URL(row.url || "");
      if (u.protocol !== "https:") continue;
      if (u.hostname !== "chatgpt.com" && !u.hostname.endsWith(".chatgpt.com")) continue;
      if (!u.pathname.startsWith("/backend-api/estuary/content")) continue;
      return row.url;
    } catch {}
  }
  throw new Error("no recent ChatGPT export download URL found");
}

function waitDownload(id, expectedSize) {
  return new Promise((resolve, reject) => {
    const listener = async delta => {
      if (delta.id !== id) return;
      if (delta.bytesReceived && delta.bytesReceived.current > expectedSize) {
        try { await chrome.downloads.cancel(id); } catch {}
        chrome.downloads.onChanged.removeListener(listener);
        reject(new Error("download exceeded expected range size"));
        return;
      }
      if (delta.state?.current === "complete") {
        chrome.downloads.onChanged.removeListener(listener);
        const rows = await chrome.downloads.search({id});
        if (!rows.length) return reject(new Error("download record missing"));
        const row = rows[0];
        if (row.bytesReceived !== expectedSize || row.totalBytes !== expectedSize) {
          return reject(new Error(`range size mismatch got=${row.bytesReceived}/${row.totalBytes} expected=${expectedSize}`));
        }
        resolve(row);
      } else if (delta.state?.current === "interrupted") {
        chrome.downloads.onChanged.removeListener(listener);
        reject(new Error("download interrupted"));
      }
    };
    chrome.downloads.onChanged.addListener(listener);
  });
}

async function downloadRange(url, start, end, index, prefix) {
  const expected = end - start + 1;
  const filename = `${cfg.folder}/${prefix}_${String(index).padStart(4,"0")}_${String(start).padStart(12,"0")}_${String(end).padStart(12,"0")}.bin`;
  const id = await chrome.downloads.download({
    url,
    filename,
    conflictAction: "overwrite",
    saveAs: false,
    headers: [
      {name: "Range", value: `bytes=${start}-${end}`},
      {name: "Accept", value: "*/*"},
      {name: "Accept-Encoding", value: "identity"}
    ]
  });
  return await waitDownload(id, expected);
}

async function runCanary() {
  let state = await getState();
  if (!["INIT","CANARY_PENDING","CANARY_HOLD"].includes(state.status)) return state;
  state.status = "CANARY_RUNNING";
  state.error = null;
  await setState(state);
  try {
    const url = await discoverExportUrl();
    const start = cfg.canaryStart;
    const end = start + cfg.canarySize - 1;
    const row = await downloadRange(url, start, end, 0, "canary");
    state.status = "CANARY_PASS";
    state.canary = {start, end, size: cfg.canarySize, filename: row.filename, downloadId: row.id};
    await setState(state);
  } catch (e) {
    state.status = "CANARY_HOLD";
    state.error = String(e?.message || e);
    await setState(state);
  }
  return state;
}

async function runTail() {
  let state = await getState();
  if (!["TAIL_READY","TAIL_RUNNING","TAIL_HOLD"].includes(state.status)) return;
  state.status = "TAIL_RUNNING";
  state.error = null;
  await setState(state);
  const url = await discoverExportUrl();

  while (state.nextStart < cfg.remoteTotal) {
    const start = state.nextStart;
    const end = Math.min(start + cfg.chunkSize - 1, cfg.remoteTotal - 1);
    const expected = end - start + 1;
    try {
      const row = await downloadRange(url, start, end, state.chunkIndex, "chunk");
      state.completed.push({index:state.chunkIndex,start,end,size:expected,filename:row.filename,downloadId:row.id});
      state.nextStart = end + 1;
      state.chunkIndex += 1;
      await setState(state);
      await sleep(250);
    } catch (e) {
      state.status = "TAIL_HOLD";
      state.error = String(e?.message || e);
      await setState(state);
      return;
    }
  }
  state.status = "TAIL_COMPLETE";
  await setState(state);
}

chrome.runtime.onInstalled.addListener(async () => {
  await setState({status:"CANARY_PENDING",nextStart:cfg.start,chunkIndex:0,completed:[],error:null});
  await runCanary();
});

chrome.action.onClicked.addListener(async () => {
  let state = await getState();
  if (["CANARY_PENDING","CANARY_HOLD","INIT"].includes(state.status)) {
    await runCanary();
  } else if (state.status === "CANARY_PASS") {
    state.status = "TAIL_READY";
    state.nextStart = cfg.start;
    state.chunkIndex = 0;
    state.completed = [];
    state.error = null;
    await setState(state);
    await runTail();
  } else if (["TAIL_READY","TAIL_RUNNING","TAIL_HOLD"].includes(state.status)) {
    await runTail();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg?.cmd === "status") sendResponse(await getState());
    else if (msg?.cmd === "startTail") {
      let state = await getState();
      if (state.status !== "CANARY_PASS") throw new Error("canary not passed");
      state.status = "TAIL_READY";
      state.nextStart = cfg.start;
      state.chunkIndex = 0;
      state.completed = [];
      state.error = null;
      await setState(state);
      runTail();
      sendResponse({ok:true});
    } else if (msg?.cmd === "retry") {
      let state = await getState();
      if (state.status === "TAIL_HOLD") {
        state.status = "TAIL_READY";
        await setState(state);
        runTail();
        sendResponse({ok:true});
      } else sendResponse({ok:false,status:state.status});
    }
  })().catch(e => sendResponse({ok:false,error:String(e?.message||e)}));
  return true;
});
